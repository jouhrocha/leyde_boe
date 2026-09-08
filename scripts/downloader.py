"""
Descargador físico de ficheros — Fase 3 (download-files).

Provee:
  build_local_path(url, base_dir, distribution_id, fmt) → Path
  download_file(url, local_path, byte_size_expected, session) → (bytes, saltado)
"""

import hashlib
import logging
import mimetypes
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import RETRY_MAX, RETRY_BACKOFF, TIMEOUT, REQUEST_DELAY

log = logging.getLogger("scraper.downloader")

_CHUNK = 256 * 1024   # 256 KB

_MIME_EXT = {
    "application/json":              ".json",
    "text/csv":                      ".csv",
    "text/plain":                    ".txt",
    "application/xml":               ".xml",
    "text/xml":                      ".xml",
    "text/html":                     ".html",
    "text/turtle":                   ".ttl",
    "application/rdf+xml":           ".rdf",
    "application/zip":               ".zip",
    "application/x-zip-compressed":  ".zip",
    "application/pdf":               ".pdf",
    "application/vnd.ms-excel":      ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/octet-stream":      ".bin",
}


# ─── Sesión HTTP ───────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=RETRY_MAX,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    })
    return session


_SESSION = _make_session()


# ─── Excepción para URLs muertas ───────────────────────────────────────────────

class SinContenido(Exception):
    """El servidor respondió 404/410: URL ya no existe."""


# ─── build_local_path ─────────────────────────────────────────────────────────

def build_local_path(
    url: str,
    base_dir: str,
    distribution_id: str = "",
    fmt: str = "",
) -> Path:
    """
    Construye la ruta local donde se guardará el fichero.

    Estructura:  <base_dir>/<2 chars uid>/<stem>_<uid8><ext>

    - Intenta usar el nombre de la URL si es legible.
    - Si no, usa hash(distribution_id + url).
    - La extensión viene del MIME (fmt) o de la URL.
    """
    bd = Path(base_dir)

    parsed   = urlparse(url)
    url_name = unquote(Path(parsed.path).name)

    ext = _ext_from_mime(fmt) or _ext_from_url(url_name) or ".bin"

    uid = hashlib.sha1(f"{distribution_id}|{url}".encode()).hexdigest()[:16]

    if url_name and "." in url_name and len(url_name) < 120:
        safe = _sanitize(url_name)
        stem = Path(safe).stem[:60]
        ext  = Path(safe).suffix or ext
        filename = f"{stem}_{uid[:8]}{ext}"
    else:
        filename = f"{uid}{ext}"

    subdir = bd / uid[:2]
    subdir.mkdir(parents=True, exist_ok=True)

    return subdir / filename


def _ext_from_mime(fmt: str) -> str:
    if not fmt:
        return ""
    mime = fmt.split(";")[0].strip().lower()
    if mime in _MIME_EXT:
        return _MIME_EXT[mime]
    ext = mimetypes.guess_extension(mime) or ""
    return ext if ext.startswith(".") else ""


def _ext_from_url(name: str) -> str:
    if not name:
        return ""
    suffix = Path(name).suffix.lower()
    return suffix if 1 < len(suffix) <= 6 else ""


def _sanitize(name: str) -> str:
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip(". ")[:200] or "file"


# ─── download_file ────────────────────────────────────────────────────────────

def download_file(
    url: str,
    local_path,
    byte_size_expected=None,
    session=None,
):
    """
    Descarga `url` → `local_path` con reanudación por bytes (HTTP Range).

    Retorna (bytes_descargados: int, saltado: bool).
      - saltado=True si el fichero ya estaba completo en disco.

    Lanza SinContenido si el servidor devuelve 404/410.
    Lanza RuntimeError si agota los reintentos.
    """
    sess = session or _SESSION
    dest = Path(local_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    already = dest.stat().st_size if dest.exists() else 0

    if byte_size_expected and already >= byte_size_expected:
        log.debug(f"Saltado (ya completo, {already} B): {dest.name}")
        return (0, True)

    headers = {}
    if already > 0:
        headers["Range"] = f"bytes={already}-"
        log.debug(f"Reanudando desde {already} B: {url}")

    last_err = None
    for intento in range(1, RETRY_MAX + 1):
        try:
            resp = sess.get(url, headers=headers, stream=True, timeout=TIMEOUT)

            if resp.status_code in (404, 410):
                raise SinContenido(f"URL muerta ({resp.status_code}): {url}")

            if resp.status_code == 416:
                log.debug(f"416 → fichero ya completo: {dest.name}")
                return (0, True)

            if resp.status_code == 429:
                wait = 60 * intento
                log.warning(f"Rate-limit 429, esperando {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {resp.status_code} en {url}")

            mode = "ab" if (already > 0 and resp.status_code == 206) else "wb"
            bytes_written = 0

            with open(dest, mode) as f:
                for chunk in resp.iter_content(chunk_size=_CHUNK):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)

            time.sleep(REQUEST_DELAY)
            log.debug(f"Descargado {bytes_written} B → {dest.name}")
            return (bytes_written, False)

        except SinContenido:
            raise
        except (requests.RequestException, OSError) as e:
            last_err = e
            wait = RETRY_BACKOFF * (2 ** (intento - 1))
            log.warning(
                f"Error descargando {url} "
                f"(intento {intento}/{RETRY_MAX}): {e}. "
                f"Reintentando en {wait:.0f}s..."
            )
            time.sleep(wait)

    raise RuntimeError(
        f"No se pudo descargar {url} tras {RETRY_MAX} intentos. "
        f"Último error: {last_err}"
    )

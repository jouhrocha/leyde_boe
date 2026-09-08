"""
Módulo de descarga e ingesta de contratos públicos.
Fuente: Plataforma de Contratación del Estado (PLACE) — Ministerio de Hacienda

Tablas destino (definidas en catalog_db.py):
  contratos_menores  — contratos menores por año (2012-hoy)
  licitaciones       — volcado completo de licitaciones adjudicadas
"""

import csv
import hashlib
import io
import logging
import time
import zipfile
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import (
    CONTRATOS_DIR, PLACE_MENORES_URL, PLACE_LICITACIONES_URL,
    RETRY_MAX, RETRY_BACKOFF, TIMEOUT,
)

log = logging.getLogger("scraper.contratos")

_CHUNK = 256 * 1024


# ── Sesión HTTP ───────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    sess = requests.Session()
    retry = Retry(
        total=RETRY_MAX,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://",  adapter)
    sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://contrataciondelestado.es/",
    })
    return sess


_SESSION = _make_session()


# ── Mapeo de columnas (los CSV de PLACE varían el nombre por año) ─────────────

_CM = {
    "num_expediente":     ["Número de expediente", "NumExpediente", "Expediente"],
    "objeto":             ["Objeto del Contrato", "Objeto", "Descripcion"],
    "tipo_contrato":      ["Tipo de Contrato", "TipoContrato", "Tipo"],
    "cpv":                ["CPV", "Código CPV"],
    "organo":             ["Órgano de Contratación", "Organo", "OrganoContratacion"],
    "importe_sin_iva":    ["Importe sin IVA", "ImporteSinIVA", "Importe sin impuestos"],
    "importe_con_iva":    ["Importe con IVA", "ImporteConIVA", "Importe total"],
    "fecha_adjudicacion": ["Fecha Adjudicación", "FechaAdjudicacion", "Fecha de adjudicación"],
    "adjudicatario":      ["Adjudicatario", "NombreAdjudicatario", "Empresa adjudicataria"],
    "nif_adjudicatario":  ["NIF Adjudicatario", "NIFAdjudicatario", "NIF"],
    "pais":               ["País Adjudicatario", "Pais", "País"],
}

_LIC = {
    "num_expediente":     ["Número de expediente", "NumExpediente"],
    "objeto":             ["Objeto del Contrato", "Objeto"],
    "tipo_contrato":      ["Tipo de Contrato", "TipoContrato"],
    "cpv":                ["CPV", "Código CPV"],
    "organo":             ["Órgano de Contratación", "Organo"],
    "presupuesto_base":   ["Presupuesto base sin impuestos", "PresupuestoBase"],
    "valor_estimado":     ["Valor estimado del contrato", "ValorEstimado"],
    "importe_adj":        ["Importe de Adjudicación sin impuestos", "ImporteAdjudicacion"],
    "fecha_publicacion":  ["Fecha de publicación en el perfil", "FechaPublicacion"],
    "fecha_adjudicacion": ["Fecha Adjudicación", "FechaAdjudicacion"],
    "adjudicatario":      ["Adjudicatario", "NombreAdjudicatario"],
    "nif_adjudicatario":  ["NIF Adjudicatario", "NIFAdjudicatario"],
    "estado":             ["Estado", "EstadoLicitacion"],
    "procedimiento":      ["Tipo de Procedimiento", "Procedimiento"],
    "pais":               ["País Adjudicatario", "Pais"],
}


# ── Utilidades ────────────────────────────────────────────────────────────────

def _v(row: dict, candidatos: list) -> str:
    for c in candidatos:
        if c in row:
            return (row[c] or "").strip()
    return ""


def _float(s: str):
    if not s:
        return None
    s = s.replace(".", "").replace(",", ".").replace("€", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _mk_id(*partes) -> str:
    return hashlib.md5("|".join(str(p) for p in partes).encode()).hexdigest()[:16]


# ── Descarga ──────────────────────────────────────────────────────────────────

def descargar_zip(url: str, dest: Path) -> int:
    """
    Descarga url → dest (ZIP).
    Retorna bytes descargados, 0 si ya existía o 404.
    """
    if dest.exists() and dest.stat().st_size > 10_000:
        log.info(f"Ya existe en disco: {dest.name} ({dest.stat().st_size:,} B)")
        return dest.stat().st_size

    log.info(f"Descargando: {url}")
    r = _SESSION.get(url, stream=True, timeout=TIMEOUT)

    if r.status_code == 404:
        log.warning(f"404 — no disponible: {url}")
        return 0

    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(_CHUNK):
            f.write(chunk)
            total += len(chunk)

    log.info(f"Guardado: {dest.name} ({total:,} B)")
    time.sleep(0.3)
    return total


# ── Parseo CSV dentro de ZIP ──────────────────────────────────────────────────

def parsear_csv_en_zip(zip_path: Path) -> list[dict]:
    """
    Extrae y parsea el primer CSV dentro del ZIP.
    Prueba UTF-8, latin-1 y cp1252. Autodetecta separador ; o ,
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                log.warning(f"Sin CSV en {zip_path.name}")
                return []
            datos = zf.read(csvs[0])
            log.info(f"CSV dentro del ZIP: {csvs[0]}")
    except zipfile.BadZipFile:
        log.error(f"ZIP corrupto o descarga incompleta: {zip_path.name}")
        return []

    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            texto = datos.decode(enc)
            sep   = ";" if texto.count(";") > texto.count(",") else ","
            rows  = list(csv.DictReader(io.StringIO(texto), delimiter=sep))
            log.info(f"{len(rows):,} filas ({enc}, sep='{sep}')")
            return rows
        except UnicodeDecodeError:
            continue
        except Exception as e:
            log.error(f"Error parseando CSV: {e}")
            return []

    log.error(f"No se pudo decodificar {zip_path.name}")
    return []


# ── Preparación de filas para BD ─────────────────────────────────────────────

def preparar_menores(rows: list[dict], año: int) -> list[tuple]:
    """Convierte filas CSV de contratos menores a tuplas para INSERT."""
    batch = []
    errores = 0
    for row in rows:
        try:
            adj   = _v(row, _CM["adjudicatario"])
            exp   = _v(row, _CM["num_expediente"])
            fecha = _v(row, _CM["fecha_adjudicacion"])
            batch.append((
                _mk_id(año, exp, adj),
                año,
                exp,
                _v(row, _CM["objeto"]),
                _v(row, _CM["tipo_contrato"]),
                _v(row, _CM["cpv"]),
                _v(row, _CM["organo"]),
                _float(_v(row, _CM["importe_sin_iva"])),
                _float(_v(row, _CM["importe_con_iva"])),
                fecha,
                adj,
                _v(row, _CM["nif_adjudicatario"]),
                _v(row, _CM["pais"]),
                str(dict(row))[:500],
            ))
        except Exception as e:
            errores += 1
            if errores <= 3:
                log.warning(f"Fila ignorada (menores {año}): {e}")
    if errores:
        log.warning(f"Total filas ignoradas en menores {año}: {errores}")
    return batch


def preparar_licitaciones(rows: list[dict]) -> list[tuple]:
    """Convierte filas CSV de licitaciones a tuplas para INSERT."""
    batch = []
    errores = 0
    for row in rows:
        try:
            adj   = _v(row, _LIC["adjudicatario"])
            exp   = _v(row, _LIC["num_expediente"])
            fecha = _v(row, _LIC["fecha_adjudicacion"])
            batch.append((
                _mk_id(exp, adj, fecha),
                exp,
                _v(row, _LIC["objeto"]),
                _v(row, _LIC["tipo_contrato"]),
                _v(row, _LIC["cpv"]),
                _v(row, _LIC["organo"]),
                _float(_v(row, _LIC["presupuesto_base"])),
                _float(_v(row, _LIC["valor_estimado"])),
                _float(_v(row, _LIC["importe_adj"])),
                _v(row, _LIC["fecha_publicacion"]),
                fecha,
                adj,
                _v(row, _LIC["nif_adjudicatario"]),
                _v(row, _LIC["estado"]),
                _v(row, _LIC["procedimiento"]),
                _v(row, _LIC["pais"]),
                str(dict(row))[:500],
            ))
        except Exception as e:
            errores += 1
            if errores <= 3:
                log.warning(f"Fila ignorada (licitaciones): {e}")
    if errores:
        log.warning(f"Total filas ignoradas en licitaciones: {errores}")
    return batch

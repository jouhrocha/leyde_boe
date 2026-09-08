"""
Descargador del BORME (Boletín Oficial del Registro Mercantil).

Estructura REAL de la API del BOE (verificada):
  data.sumario.diario → lista de diarios
  diario.numero       → número de BORME
  diario.seccion      → lista de secciones
  seccion.codigo      → "A" (Sección 1 Empresarios) | "B" (Sección 2 Anuncios)
  seccion.nombre      → "SECCIÓN PRIMERA. Empresarios. Actos inscritos"
  seccion.item        → lista de ítems (uno por registro mercantil/provincia)
  item.identificador  → "BORME-A-2024-1-01"
  item.titulo         → "ARABA/ÁLAVA"
  item.url_pdf.texto  → URL del PDF
"""

import json
import time
import logging
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import requests

from config.settings import (
    BORME_SUMARIO_URL,
    BORME_START_DATE,
    BORME_FILES_DIR,
    BOE_REQUEST_DELAY,
    BOE_TIMEOUT,
    BOE_MAX_RETRIES,
)

log = logging.getLogger("scraper.borme")

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

_SESSION = requests.Session()
_SESSION.headers.update(_HEADERS)


# ─── Generación de fechas ──────────────────────────────────────────────────────

def genera_fechas(
    desde: str | None = None,
    hasta: str | None = None,
) -> list[str]:
    """
    Devuelve todos los días de lunes a viernes entre `desde` y `hasta`
    en formato YYYY-MM-DD.
    """
    d_desde = date.fromisoformat(desde or BORME_START_DATE)
    d_hasta = date.fromisoformat(hasta) if hasta else date.today()

    fechas = []
    current = d_desde
    while current <= d_hasta:
        if current.weekday() < 5:
            fechas.append(current.isoformat())
        current += timedelta(days=1)

    log.info(
        f"genera_fechas: {len(fechas)} días laborables "
        f"del {d_desde} al {d_hasta}"
    )
    return fechas


# ─── Cliente BOE ──────────────────────────────────────────────────────────────

class SinBorme(Exception):
    """El BOE devolvió 404: no hay BORME para esa fecha (festivo, etc.)."""


def fetch_sumario(fecha: str) -> dict:
    """
    Descarga el sumario BORME de `fecha` (YYYY-MM-DD) de la API del BOE.
    Retorna el dict JSON tal cual.
    Lanza SinBorme si el BOE responde 404.
    """
    fecha_boe = fecha.replace("-", "")
    url = BORME_SUMARIO_URL.format(fecha=fecha_boe)

    for intento in range(1, BOE_MAX_RETRIES + 1):
        try:
            resp = _SESSION.get(url, timeout=BOE_TIMEOUT)

            if resp.status_code == 404:
                raise SinBorme(fecha)

            if resp.status_code == 200:
                time.sleep(BOE_REQUEST_DELAY)
                return resp.json()

            if resp.status_code == 429:
                wait = 60 * intento
                log.warning(f"BOE rate-limit (429), esperando {wait}s...")
                time.sleep(wait)
                continue

            raise RuntimeError(f"HTTP {resp.status_code} en {url}")

        except SinBorme:
            raise
        except (requests.RequestException, ValueError) as e:
            wait = 2 ** intento
            log.warning(
                f"Error obteniendo BORME {fecha} "
                f"(intento {intento}/{BOE_MAX_RETRIES}): {e}. "
                f"Reintentando en {wait}s..."
            )
            time.sleep(wait)

    raise RuntimeError(
        f"No se pudo obtener el BORME de {fecha} tras {BOE_MAX_RETRIES} intentos"
    )


# ─── Utilidades ───────────────────────────────────────────────────────────────

def _aslist(v) -> list:
    """Normaliza: None → [], dict/obj → [obj], list → list."""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _url_pdf(item: dict) -> str:
    """
    Extrae la URL del PDF de un ítem.
    La API devuelve url_pdf como dict con clave 'texto', o directamente string.
    """
    pdf = item.get("url_pdf")
    if not pdf:
        return ""
    if isinstance(pdf, dict):
        return pdf.get("texto") or ""
    return str(pdf)


# ─── Parser del sumario ───────────────────────────────────────────────────────

def parse_sumario(fecha: str, data: dict) -> dict:
    """
    Parsea el JSON real de la API del BOE para un sumario BORME.

    Estructura verificada de la API:
      data.sumario.diario[]
        .numero              → número de BORME
        .seccion[]
          .codigo            → "A" = Sección 1 Empresarios
                               "B" = Sección 2 Anuncios
          .nombre            → nombre descriptivo de la sección
          .item[]
            .identificador   → "BORME-A-2024-1-01"
            .titulo          → nombre del registro mercantil / provincia
            .url_pdf.texto   → URL del PDF

    Retorna:
      {
        "numero":   "1",
        "actos":    [...],   ← sección A
        "anuncios": [...],   ← sección B
      }
    """
    try:
        diarios = _aslist(data["data"]["sumario"]["diario"])
    except (KeyError, TypeError) as e:
        log.error(f"Estructura inesperada en sumario {fecha}: {e}")
        return {"numero": None, "actos": [], "anuncios": []}

    if not diarios:
        return {"numero": None, "actos": [], "anuncios": []}

    actos    = []
    anuncios = []
    numero   = None

    for diario in diarios:
        if not isinstance(diario, dict):
            continue

        # Número de BORME (campo "numero" en la API real)
        if numero is None:
            numero = diario.get("numero") or diario.get("@numero")

        for seccion in _aslist(diario.get("seccion")):
            if not isinstance(seccion, dict):
                continue

            # Detectar sección por código ("A"/"B") o por nombre
            codigo     = (seccion.get("codigo") or "").upper().strip()
            nombre_sec = (seccion.get("nombre") or seccion.get("@nombre") or "").upper()
            es_sec1 = (
                codigo == "A"
                or "PRIMERA" in nombre_sec
                or "EMPRESARIOS" in nombre_sec
            )

            for item in _aslist(seccion.get("item")):
                if not isinstance(item, dict):
                    continue

                acto = {
                    "id":       item.get("identificador") or item.get("id") or item.get("@id") or "",
                    "fecha":    fecha,
                    "seccion":  "1" if es_sec1 else "2",
                    "registro": item.get("titulo") or item.get("@titulo") or "",
                    "titulo":   item.get("titulo") or item.get("@titulo") or "",
                    "url_pdf":  _url_pdf(item),
                    "url_xml":  item.get("url_xml") or item.get("urlXml") or "",
                    "url_htm":  item.get("url_htm") or item.get("urlHtm") or "",
                }

                # Construir URL del XML si está vacía
                if acto["id"] and not acto["url_xml"]:
                    acto["url_xml"] = "https://www.boe.es/diario_borme/xml.php?id=" + acto["id"]

                if es_sec1:
                    actos.append(acto)
                else:
                    anuncios.append(acto)

    log.debug(
        f"parse_sumario {fecha}: número={numero}, "
        f"actos={len(actos)}, anuncios={len(anuncios)}"
    )
    return {
        "numero":   numero,
        "actos":    actos,
        "anuncios": anuncios,
    }


# ─── Guardado en disco ────────────────────────────────────────────────────────

def fetch_acto_xml(identificador: str, fecha: str = "") -> str:
    # ── Corte por fecha: antes de BORME_XML_START_DATE no existe XML ──
    if fecha and fecha < BORME_XML_START_DATE:
        raise SinBorme(f"{identificador} (fecha {fecha} anterior al corte)")

    # ── Skip-list: ya sabemos que este ID no tiene XML ──
    if _en_skiplist(identificador):
        log.debug(f"Saltando {identificador} (en skip-list)")
        raise SinBorme(f"{identificador} (en skip-list)")

    url = f"https://www.boe.es/diario_borme/xml.php?id={identificador}"

    for intento in range(1, BOE_MAX_RETRIES + 1):
        try:
            resp = _SESSION.get(url, timeout=BOE_TIMEOUT)

            if resp.status_code == 404:
                _añadir_skiplist(identificador, fecha)   # ← nunca más
                raise SinBorme(identificador)

            if resp.status_code == 200:
                content_type = resp.headers.get("Content-Type", "")
                text = resp.text.lstrip()
                if "text/html" in content_type or text.startswith(("<!DOCTYPE", "<html")):
                    _añadir_skiplist(identificador, fecha)   # ← nunca más
                    raise SinBorme(identificador)
                time.sleep(BOE_REQUEST_DELAY)
                return text

            # ... resto igual


def _xml_text(node) -> str | None:
    """Extrae el texto de un nodo XML, None si no existe."""
    if node is None:
        return None
    return (node.text or "").strip() or None


def parse_acto_xml(xml_text: str, acto_id: str) -> dict:
    """
    Parsea el XML de un acto BORME.

    Estructura del XML del BOE:
      <documento>
        <metadatos>
          <identificador>BORME-A-2024-1-01</identificador>
          <titulo>ARABA/ÁLAVA</titulo>
          <fecha_publicacion>20240102</fecha_publicacion>
        </metadatos>
        <texto>
          <anuncio id="BORME-A-2024-1-01-1">
            <empresa>ACEROS VASCOS SL</empresa>
            <actos>
              <acto tipo="Constitución">Capital: 3.000,00 euros.</acto>
            </actos>
            <datos_registrales>Tomo 1234, Folio 1, Hoja BI-12345</datos_registrales>
          </anuncio>
        </texto>
      </documento>

    Retorna:
      {
        "acto_id":  "BORME-A-2024-1-01",
        "anuncios": [
          {
            "id":                "BORME-A-2024-1-01-1",
            "empresa":           "ACEROS VASCOS SL",
            "actos":             [{"tipo": "Constitución", "texto": "Capital: 3.000,00 euros."}],
            "datos_registrales": "Tomo 1234..."
          }
        ],
        "xml_raw": "<documento>..."
      }
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.error(f"Error parseando XML de {acto_id}: {e}")
        return {"acto_id": acto_id, "anuncios": [], "xml_raw": xml_text}

    anuncios = []
    texto_node = root.find(".//texto")
    if texto_node is None:
        texto_node = root

    for anuncio_node in texto_node.findall(".//anuncio"):
        anuncio_id = anuncio_node.get("id", "")

        empresa = _xml_text(anuncio_node.find("empresa"))

        actos = []
        actos_node = anuncio_node.find("actos")
        if actos_node is not None:
            for acto_node in actos_node.findall("acto"):
                actos.append({
                    "tipo":  acto_node.get("tipo", "").strip(),
                    "texto": (acto_node.text or "").strip(),
                })

        datos_reg = _xml_text(anuncio_node.find("datos_registrales"))

        anuncios.append({
            "id":                anuncio_id,
            "empresa":           empresa,
            "actos":             actos,
            "datos_registrales": datos_reg,
        })

    log.debug(f"parse_acto_xml {acto_id}: {len(anuncios)} anuncios")
    return {
        "acto_id":  acto_id,
        "anuncios": anuncios,
        "xml_raw":  xml_text,
    }


def guardar_acto_xml(
    identificador: str,
    xml_text: str,
    base_dir: str | None = None,
) -> str:
    """
    Guarda el XML de un acto en:
        <base_dir>/xml/<2chars>/<identificador>.xml
    """
    bd   = Path(base_dir or BORME_FILES_DIR)
    dest = bd / "xml" / identificador[:2] / f"{identificador}.xml"
    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(dest, "w", encoding="utf-8") as f:
        f.write(xml_text)

    return str(dest)


def guardar_sumario(fecha: str, raw_json: dict, base_dir: str | None = None) -> str:
    """
    Guarda el JSON del sumario en:
        <base_dir>/YYYY/MM/BORME-YYYYMMDD.json
    """
    bd = Path(base_dir or BORME_FILES_DIR)
    yyyy, mm, _ = fecha.split("-")
    dest = bd / yyyy / mm / f"BORME-{fecha.replace('-', '')}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(dest, "w", encoding="utf-8") as f:
        json.dump(raw_json, f, ensure_ascii=False, indent=2)

    return str(dest)
"""
Parser de PDFs del BORME usando pdftotext (poppler).
Añadir al final de core/borme.py o importar desde ahí.

Enganche en engine.py: cuando fetch_acto_xml lanza SinBorme,
_procesar_borme_acto_xml llama a parse_acto_pdf como fallback.
"""

import re
import json
import logging
import subprocess
import requests
import time

from config.settings import BOE_REQUEST_DELAY, BOE_TIMEOUT, BOE_MAX_RETRIES

log = logging.getLogger("scraper.borme")

# ─── Limpieza de cabeceras de página ──────────────────────────────────────────

_RE_CABECERA = re.compile(
    r'BOLET[^\n]+\n'      # "BOLETÍN OFICIAL DEL REGISTRO MERCANTIL"
    r'N[^\n]+\n'          # "Núm. 77   Jueves 23 de abril de 2026   Pág. XXXXX"
    r'[^\n]*\n'           # línea en blanco o sección
    r'(?:[^\n]*\n)?'      # posible segunda línea de sección
    r'(?:cve:[^\n]*\n)?'  # "cve: BORME-A-..."
    r'(?:Verificable[^\n]*\n)?',  # "Verificable en https://..."
    re.MULTILINE
)

_RE_CVE = re.compile(r'cve:\s*BORME[^\n]*\n?', re.MULTILINE)
_RE_VER = re.compile(r'Verificable\s+en\s+https://[^\n]*\n?', re.MULTILINE)


def _limpiar_texto(texto: str) -> str:
    """Elimina cabeceras de página y metadatos del texto extraído del PDF."""
    texto = _RE_CVE.sub('', texto)
    texto = _RE_VER.sub('', texto)
    texto = _RE_CABECERA.sub('\n', texto)
    # Colapsar múltiples líneas en blanco
    texto = re.sub(r'\n{3,}', '\n\n', texto)
    return texto.strip()


# ─── Extracción de texto del PDF ──────────────────────────────────────────────

def pdf_url_to_text(url: str) -> str:
    """
    Descarga un PDF del BOE y extrae su texto con pdftotext (poppler).
    Devuelve el texto limpio listo para parsear.
    Lanza RuntimeError si no se puede descargar o extraer.
    """
    from fake_useragent import UserAgent
    ua = UserAgent()
    headers = {
        "User-Agent": ua.random,
        "Accept": "application/pdf,*/*",
        "Referer": "https://www.boe.es/diario_borme/",
    }

    for intento in range(1, BOE_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=BOE_TIMEOUT)
            if resp.status_code == 404:
                raise RuntimeError(f"PDF no encontrado: {url}")
            if resp.status_code == 429:
                time.sleep(60 * intento)
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code} descargando PDF: {url}")

            pdf_bytes = resp.content
            time.sleep(BOE_REQUEST_DELAY)
            break
        except RuntimeError:
            raise
        except Exception as e:
            if intento == BOE_MAX_RETRIES:
                raise RuntimeError(f"No se pudo descargar {url}: {e}")
            time.sleep(2 ** intento)

    # Extraer texto con pdftotext (stdin → stdout)
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", "-", "-"],
            input=pdf_bytes,
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"pdftotext falló: {proc.stderr.decode()}")
        texto = proc.stdout.decode("utf-8", errors="replace")
    except FileNotFoundError:
        raise RuntimeError(
            "pdftotext no encontrado. Instala poppler-utils: "
            "apt install poppler-utils  /  choco install poppler"
        )

    return _limpiar_texto(texto)


# ─── Parser de anuncios ───────────────────────────────────────────────────────

# Patrón de inicio de anuncio: número de 6 dígitos + " - " + nombre empresa
_RE_ANUNCIO = re.compile(
    r'(\d{6})\s*[-–]\s*(.+?)(?=\d{6}\s*[-–]|\Z)',
    re.DOTALL
)

# Tipos de acto reconocidos en el BORME
_TIPOS_ACTO = [
    "Constitución",
    "Disolución",
    "Extinción",
    "Liquidación",
    "Ampliación de capital",
    "Reducción de capital",
    "Cambio de domicilio social",
    "Cambio de objeto social",
    "Nombramientos",
    "Revocaciones",
    "Otros conceptos",
    "Datos registrales",
    "Transformación",
    "Fusión",
    "Escisión",
    "Modificación de estatutos",
    "Concurso de acreedores",
    "Declaración de unipersonalidad",
]

_RE_DATOS_REG = re.compile(
    r'Datos registrales\.?\s*(.+?)(?=\n\n|\d{6}\s*[-–]|\Z)',
    re.DOTALL | re.IGNORECASE
)


def _extraer_actos(texto_anuncio: str) -> list[dict]:
    """
    Extrae los actos individuales del cuerpo de texto de un anuncio.
    Cada acto tiene: tipo + texto descriptivo.
    """
    actos = []

    # Intentar partir por tipos conocidos
    patron_tipos = '|'.join(re.escape(t) for t in _TIPOS_ACTO)
    partes = re.split(rf'({patron_tipos})\.?\s*', texto_anuncio, flags=re.IGNORECASE)

    i = 0
    while i < len(partes):
        parte = partes[i].strip()
        if not parte:
            i += 1
            continue
        # ¿Es un tipo de acto conocido?
        if any(parte.lower() == t.lower() for t in _TIPOS_ACTO):
            tipo  = parte
            texto = partes[i + 1].strip() if i + 1 < len(partes) else ""
            # Limpiar el texto del acto
            texto = re.sub(r'\s+', ' ', texto).strip()
            if tipo.lower() != "datos registrales":  # datos registrales se guarda aparte
                actos.append({"tipo": tipo, "texto": texto})
            i += 2
        else:
            i += 1

    # Si no encontramos actos por tipos, guardar el texto completo como "General"
    if not actos:
        texto_limpio = re.sub(r'\s+', ' ', texto_anuncio).strip()
        if texto_limpio:
            actos.append({"tipo": "General", "texto": texto_limpio[:2000]})

    return actos


def parse_acto_pdf(texto: str, acto_id: str) -> dict:
    """
    Parsea el texto extraído de un PDF de BORME y devuelve los anuncios.

    Retorna el mismo formato que parse_acto_xml:
    {
        "acto_id":  "BORME-A-2024-77-08",
        "anuncios": [
            {
                "id":                "BORME-A-2024-77-08-196565",
                "empresa":           "GRAN ROCA RED S.L.",
                "actos":             [{"tipo": "Constitución", "texto": "..."}],
                "datos_registrales": "S 8, H B 655171, I/A 1 (16.04.26)",
            }
        ],
    }
    """
    anuncios = []

    for match in _RE_ANUNCIO.finditer(texto):
        numero   = match.group(1).strip()
        contenido = match.group(2).strip()

        # Primera línea = nombre de empresa
        lineas   = contenido.split('\n')
        empresa  = lineas[0].strip()
        cuerpo   = '\n'.join(lineas[1:]).strip()

        # Extraer datos registrales (siempre al final)
        datos_reg = None
        m_dr = _RE_DATOS_REG.search(cuerpo)
        if m_dr:
            datos_reg = re.sub(r'\s+', ' ', m_dr.group(1)).strip()
            # Quitar datos registrales del cuerpo para parsear actos
            cuerpo = cuerpo[:m_dr.start()].strip()

        actos = _extraer_actos(cuerpo)

        anuncio_id = f"{acto_id}-{numero}"

        anuncios.append({
            "id":                anuncio_id,
            "empresa":           empresa,
            "actos":             actos,
            "datos_registrales": datos_reg,
        })

    log.debug(f"parse_acto_pdf {acto_id}: {len(anuncios)} anuncios")
    return {
        "acto_id":  acto_id,
        "anuncios": anuncios,
    }

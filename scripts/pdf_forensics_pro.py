#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           PDF FORENSICS PRO  ·  Motor de Análisis Forense Avanzado          ║
║                                                                              ║
║  MÓDULOS DE ANÁLISIS:                                                        ║
║   01 · Metadatos extendidos (XMP, DocInfo, fechas de creación/modificación) ║
║   02 · Actualizaciones incrementales (versiones ocultas del documento)       ║
║   03 · Objetos eliminados (xref free entries + recuperación de contenido)    ║
║   04 · Descompresión total de streams + operadores de texto                  ║
║   05 · Texto invisible (color blanco, alpha=0, render mode 3, escala 0)      ║
║   06 · Redacciones (Redact annotations, rectángulos negros/blancos)          ║
║   07 · Capas OCG ocultas (Optional Content Groups)                           ║
║   08 · Anotaciones ocultas (flags Hidden/NoView, contenido embebido)         ║
║   09 · JavaScript embebido y acciones automáticas                            ║
║   10 · Archivos adjuntos embebidos (EmbeddedFiles)                           ║
║   11 · Análisis de fuentes y encodings (glifos no estándar, ToUnicode)       ║
║   12 · Form XObjects con texto oculto                                        ║
║   13 · Firmas digitales y certificados                                        ║
║   14 · Steganografía en streams de imagen (LSB hints)                        ║
║   15 · Scan raw de bytes (URLs, emails, fechas, hashes, números largos)      ║
║   16 · Análisis de estructura xref stream (PDF 1.5+)                        ║
║   17 · Extracción de texto con posición (pdfminer)                          ║
║   18 · Extracción estándar pypdf (página a página)                          ║
║   19 · Decodificación universal residual (hex, base64, literal, UTF-16BE)  ║
║                                                                              ║
║  SALIDA:                                                                     ║
║   · Informe HTML forense interactivo por PDF                                 ║
║   · Informe consolidado multi-PDF (batch)                                    ║
║   · JSON de hallazgos estructurados                                          ║
║   · Hash SHA-512 de integridad sellando cada informe                         ║
╚══════════════════════════════════════════════════════════════════════════════╝

Uso:
    # Un solo PDF
    python3 pdf_forensics_pro.py documento.pdf

    # Directorio completo (batch)
    python3 pdf_forensics_pro.py /ruta/a/carpeta/

    # Con opciones
    python3 pdf_forensics_pro.py /ruta/pdfs/ --output-dir /informes --verbose
"""

import sys
import os
import re
import struct
import zlib
import json
import argparse
import hashlib
import datetime
import traceback
import textwrap
import urllib.request
import urllib.parse
import urllib.error
import threading
import time
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple, Any, Optional
import html as html_module

# ── Versión ────────────────────────────────────────────────────────────────────
VERSION = "3.1.0"
TOOL_NAME = "PDF Forensics PRO"

# ── Colores ANSI ───────────────────────────────────────────────────────────────
R    = "\033[91m";  G    = "\033[92m";  Y    = "\033[93m"
B    = "\033[94m";  M    = "\033[95m";  C    = "\033[96m"
W    = "\033[97m";  DIM  = "\033[2m";   BOLD = "\033[1m";  RST = "\033[0m"

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS DE UI
# ══════════════════════════════════════════════════════════════════════════════

def banner():
    print(f"""
{M}{BOLD}╔══════════════════════════════════════════════════════════════════╗
║        PDF FORENSICS PRO  ·  v{VERSION:<6}                           ║
║        Motor de Análisis Forense · Informe Sellado SHA-512       ║
║        Validación lingüística: LanguageTool + Datamuse APIs      ║
╚══════════════════════════════════════════════════════════════════╝{RST}
""")

def section(title: str, icon: str = "▶"):
    print(f"\n{C}{BOLD}{'═'*65}{RST}")
    print(f"{C}{BOLD}  {icon}  {title}{RST}")
    print(f"{C}{'─'*65}{RST}")

def hit(label: str, value: str, level: str = "HIGH"):
    LEVELS = {"CRITICAL": f"\033[41m\033[97m", "HIGH": R, "MED": Y, "LOW": G, "INFO": B}
    c = LEVELS.get(level, W)
    print(f"  {c}{BOLD}[{level}]{RST} {W}{label}:{RST} {Y}{value}{RST}")

def info(msg: str):
    print(f"  {DIM}→ {msg}{RST}")

def warn(msg: str):
    print(f"  {Y}⚠ {msg}{RST}")

def ok(msg: str):
    print(f"  {G}✓ {msg}{RST}")

def progress(current: int, total: int, name: str):
    pct = int((current / total) * 40)
    bar = "█" * pct + "░" * (40 - pct)
    print(f"\r  {C}[{bar}]{RST} {current}/{total}  {W}{name}{RST}", end="", flush=True)

# ══════════════════════════════════════════════════════════════════════════════
#  CLASE PRINCIPAL DE RESULTADOS
# ══════════════════════════════════════════════════════════════════════════════

class ForensicFinding:
    """Representa un hallazgo forense individual."""
    def __init__(self, module: str, level: str, title: str,
                 detail: str, page: Optional[int] = None,
                 raw_data: Optional[str] = None):
        self.module   = module
        self.level    = level      # CRITICAL / HIGH / MED / LOW / INFO
        self.title    = title
        self.detail   = detail
        self.page     = page
        self.raw_data = raw_data
        self.ts       = datetime.datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        return {
            "module":    self.module,
            "level":     self.level,
            "title":     self.title,
            "detail":    self.detail,
            "page":      self.page,
            "raw_data":  self.raw_data,
            "timestamp": self.ts,
        }

    def severity_score(self) -> int:
        return {"CRITICAL": 100, "HIGH": 75, "MED": 50, "LOW": 25, "INFO": 5}.get(self.level, 0)


class PDFForensicResult:
    """Acumula todos los hallazgos de un PDF."""
    def __init__(self, pdf_path: str):
        self.pdf_path   = pdf_path
        self.pdf_name   = Path(pdf_path).name
        self.file_size  = os.path.getsize(pdf_path)
        self.file_sha512 = _sha512_file(pdf_path)
        self.file_md5   = _md5_file(pdf_path)
        self.findings: List[ForensicFinding] = []
        self.metadata: Dict[str, Any] = {}
        self.errors: List[str] = []
        self.started_at = datetime.datetime.utcnow()
        self.finished_at: Optional[datetime.datetime] = None

    def add(self, module: str, level: str, title: str, detail: str,
            page: Optional[int] = None, raw_data: Optional[str] = None):
        self.findings.append(ForensicFinding(module, level, title, detail, page, raw_data))
        hit(title, detail, level)

    def add_error(self, msg: str):
        self.errors.append(msg)

    def finish(self):
        self.finished_at = datetime.datetime.utcnow()

    @property
    def risk_score(self) -> int:
        if not self.findings:
            return 0
        return min(100, sum(f.severity_score() for f in self.findings) // max(len(self.findings), 1))

    @property
    def risk_label(self) -> str:
        s = self.risk_score
        if s >= 75: return "CRÍTICO"
        if s >= 50: return "ALTO"
        if s >= 25: return "MEDIO"
        if s > 0:   return "BAJO"
        return "LIMPIO"

    def summary_by_level(self) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for f in self.findings:
            counts[f.level] += 1
        return dict(counts)

    def to_dict(self) -> dict:
        return {
            "pdf_path":   self.pdf_path,
            "pdf_name":   self.pdf_name,
            "file_size":  self.file_size,
            "sha512":     self.file_sha512,
            "md5":        self.file_md5,
            "risk_score": self.risk_score,
            "risk_label": self.risk_label,
            "metadata":   self.metadata,
            "findings":   [f.to_dict() for f in self.findings],
            "errors":     self.errors,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

# ══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES HASH
# ══════════════════════════════════════════════════════════════════════════════

def _sha512_file(path: str) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def _md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def _sha512_str(s: str) -> str:
    return hashlib.sha512(s.encode("utf-8")).hexdigest()

def _sha512_bytes(b: bytes) -> str:
    return hashlib.sha512(b).hexdigest()

# ══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES DE DESCOMPRESIÓN
# ══════════════════════════════════════════════════════════════════════════════

def _try_decompress(data: bytes) -> Optional[bytes]:
    for wbits in (-15, 15, 47):
        try:
            return zlib.decompress(data, wbits)
        except zlib.error:
            continue
    return None

def _decode_safe(data: bytes) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return data.decode(enc, errors="replace")
        except Exception:
            continue
    return data.decode("latin-1", errors="replace")

# ══════════════════════════════════════════════════════════════════════════════
#  MOTOR TOUNICODE — Decodificación de texto con encoding custom
#  Este es el núcleo que convierte <01><02><03> → texto legible real
# ══════════════════════════════════════════════════════════════════════════════

def parse_tounicode_cmap(cmap_data: str) -> Dict[str, str]:
    """
    Parsea una tabla ToUnicode CMap y devuelve un diccionario
    {hex_glyph -> carácter_unicode}.
    
    Soporta:
      - beginbfchar / endbfchar   (mapeos individuales)
      - beginbfrange / endbfrange (rangos de mapeo)
    """
    mapping: Dict[str, str] = {}

    # ── beginbfchar / endbfchar ───────────────────────────────────────────────
    # Formato: <GID> <Unicode>
    # Ej:  <01> <0048>   →  glifo 01 = 'H'
    bfchar_blocks = re.findall(
        r'beginbfchar(.*?)endbfchar', cmap_data, re.DOTALL
    )
    for block in bfchar_blocks:
        for m in re.finditer(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block):
            gid  = m.group(1).upper()
            uval = m.group(2).upper()
            try:
                # Unicode puede ser un codepoint de 4 dígitos o más
                char = chr(int(uval, 16))
                mapping[gid] = char
            except (ValueError, OverflowError):
                pass

    # ── beginbfrange / endbfrange ─────────────────────────────────────────────
    # Formato: <GID_start> <GID_end> <Unicode_start>
    # Ej:  <20> <39> <0020>  →  glifos 20-39 mapean a U+0020..U+0039
    bfrange_blocks = re.findall(
        r'beginbfrange(.*?)endbfrange', cmap_data, re.DOTALL
    )
    for block in bfrange_blocks:
        # Rango simple: <start> <end> <unicode_base>
        for m in re.finditer(
            r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block
        ):
            gid_start  = int(m.group(1), 16)
            gid_end    = int(m.group(2), 16)
            uni_start  = int(m.group(3), 16)
            for offset in range(gid_end - gid_start + 1):
                gid_hex = format(gid_start + offset, '02X').upper()
                # Pad to same length as original
                gid_hex = gid_hex.zfill(len(m.group(1)))
                try:
                    mapping[gid_hex] = chr(uni_start + offset)
                except (ValueError, OverflowError):
                    pass

        # Rango con array de destinos: <start> <end> [<u1> <u2> ...]
        for m in re.finditer(
            r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[([^\]]+)\]', block
        ):
            gid_start = int(m.group(1), 16)
            targets   = re.findall(r'<([0-9A-Fa-f]+)>', m.group(3))
            for offset, t in enumerate(targets):
                gid_hex = format(gid_start + offset, '02X').upper()
                gid_hex = gid_hex.zfill(len(m.group(1)))
                try:
                    mapping[gid_hex] = chr(int(t, 16))
                except (ValueError, OverflowError):
                    pass

    return mapping


def extract_all_tounicode_maps(pdf_bytes: bytes) -> Dict[str, Dict[str, str]]:
    """
    Extrae TODOS los mapas ToUnicode del PDF.
    Devuelve {font_name_or_id: {gid: char}}.
    También construye un mapa global combinado para uso rápido.
    """
    maps: Dict[str, Dict[str, str]] = {}
    combined: Dict[str, str] = {}

    # Buscar todos los objetos que contienen /ToUnicode seguido de un stream
    # Patrón: ... /ToUnicode N 0 R ... o el stream directamente en el objeto
    stream_pat = re.compile(rb'stream\r?\n(.*?)endstream', re.DOTALL)

    for i, m in enumerate(stream_pat.finditer(pdf_bytes)):
        raw = m.group(1)

        # Intentar descomprimir
        dec = _try_decompress(raw)
        text = _decode_safe(dec if dec else raw)

        # Verificar que es un CMap (ToUnicode)
        if 'beginbfchar' not in text and 'beginbfrange' not in text:
            continue

        font_map = parse_tounicode_cmap(text)
        if font_map:
            key = f"stream_{i}"
            maps[key] = font_map
            combined.update(font_map)  # merge al mapa global

    return maps, combined


# ══════════════════════════════════════════════════════════════════════════════
#  MOTOR DE VALIDACIÓN LINGÜÍSTICA — APIs gratuitas para seleccionar
#  la mejor decodificación basada en coherencia real del texto
# ══════════════════════════════════════════════════════════════════════════════

# Cache para evitar llamadas repetidas a la API con el mismo texto
_LANG_CACHE: Dict[str, float] = {}
_LANG_CACHE_LOCK = threading.Lock()

# Controla si la API está disponible (se desactiva si falla repetidamente)
_LANGUAGETOOL_AVAILABLE = True
_LANGUAGETOOL_FAILURES  = 0
_DATAMUSE_AVAILABLE     = True

# Frecuencias típicas de letras en español/catalán para scoring local sin API
_FREQ_ES = set('aeorisntldcumpbghqyvfjzxkw')
_COMMON_ES_WORDS = {
    'de','la','el','en','y','que','del','al','los','las','una','un','con','por',
    'se','es','su','les','per','tot','però','quan','com','hem','han','era','son',
    'general','gestió','ingressos','dret','públic','ordenança','article','punt',
    'any','anys','dia','dies','mes','mesos','any','número','import','euros',
    'recaptació','inspecció','tributs','taxa','impost','pagament','multa',
    'contribuent','administració','ajuntament','municipal','territori',
}


def _score_text_local(text: str) -> float:
    """
    Puntuación local rápida sin API:
      - % de caracteres alfabéticos/espacios  (queremos texto, no símbolos)
      - % de letras frecuentes en español
      - Bonus si contiene palabras comunes en español/catalán
      - Penaliza secuencias de sustitucion tipo \\xNN
    Devuelve un float entre 0.0 (basura) y 1.0 (texto perfecto).
    """
    if not text or len(text.strip()) < 2:
        return 0.0

    t = text.lower()
    total = max(len(t), 1)

    # Penalizar fuertemente si hay muchos \xNN (hex anotado = último recurso)
    xnn_count = len(re.findall(r'\\x[0-9a-f]{2}', t))
    if xnn_count > total * 0.1:
        return 0.05

    # % de caracteres alfanuméricos + espacios + puntuación básica
    alpha = sum(1 for c in t if c.isalpha() or c == ' ')
    alpha_ratio = alpha / total

    # % de letras frecuentes en español entre las letras presentes
    letters = [c for c in t if c.isalpha()]
    if letters:
        freq_ratio = sum(1 for c in letters if c in _FREQ_ES) / len(letters)
    else:
        freq_ratio = 0.0

    # Bonus por palabras reales en español/catalán
    words = set(re.findall(r'[a-zàáâãäåæçèéêëìíîïðñòóôõöùúûüýþÿ]+', t))
    word_bonus = min(0.3, len(words & _COMMON_ES_WORDS) * 0.06)

    # Penalizar exceso de caracteres de control/nulos/reemplazos
    replacements = sum(1 for c in text if c in '\x00\ufffd\x01\x02\x03')
    rep_penalty  = min(0.5, replacements / total)

    score = (alpha_ratio * 0.4 + freq_ratio * 0.3 + word_bonus) - rep_penalty
    return max(0.0, min(1.0, score))


def _score_text_languagetool(text: str, lang: str = 'auto') -> float:
    """
    Llama a la API gratuita de LanguageTool para verificar si el texto
    es lingüísticamente coherente. Devuelve un float entre 0.0 y 1.0.
    
    Endpoint: https://api.languagetool.org/v2/check
    No requiere API key para textos cortos (límite generoso en tier gratuito).
    """
    global _LANGUAGETOOL_AVAILABLE, _LANGUAGETOOL_FAILURES

    if not _LANGUAGETOOL_AVAILABLE:
        return -1.0  # API desactivada — usar scoring local

    text_stripped = text.strip()
    if len(text_stripped) < 5 or len(text_stripped) > 500:
        return -1.0  # Demasiado corto o largo para la API

    cache_key = f"lt:{text_stripped[:100]}"
    with _LANG_CACHE_LOCK:
        if cache_key in _LANG_CACHE:
            return _LANG_CACHE[cache_key]

    try:
        payload = urllib.parse.urlencode({
            'text': text_stripped,
            'language': lang,
            'enabledOnly': 'false',
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://api.languagetool.org/v2/check',
            data=payload,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'PDFForensicsPro/3.1',
            },
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        matches   = data.get('matches', [])
        words     = len(text_stripped.split())
        # Menos errores por palabra = mejor texto
        # Si tiene 0 errores → score 1.0; si tiene 1 error por palabra → score 0.0
        error_rate = len(matches) / max(words, 1)
        score = max(0.0, 1.0 - min(error_rate, 1.0))

        # También penalizar si el idioma detectado es "desconocido"
        detected_lang = data.get('language', {}).get('detectedLanguage', {}).get('code', '')
        if detected_lang and detected_lang.startswith('zxx'):  # zxx = "sin contenido lingüístico"
            score *= 0.3

        with _LANG_CACHE_LOCK:
            _LANG_CACHE[cache_key] = score
        _LANGUAGETOOL_FAILURES = 0
        return score

    except Exception:
        _LANGUAGETOOL_FAILURES += 1
        if _LANGUAGETOOL_FAILURES >= 3:
            _LANGUAGETOOL_AVAILABLE = False
            print(f"  {Y}⚠ LanguageTool API no disponible — usando scoring local{RST}")
        return -1.0


def _score_text_datamuse(text: str) -> float:
    """
    Usa la API gratuita de Datamuse para verificar cuántas palabras
    del texto existen en el diccionario. Sin API key.
    
    Endpoint: https://api.datamuse.com/words?sp=...&max=1
    """
    global _DATAMUSE_AVAILABLE
    if not _DATAMUSE_AVAILABLE:
        return -1.0

    words = re.findall(r'[a-zA-Zàáâãäåæçèéêëìíîïòóôõöùúûü]{3,}', text.lower())
    if not words:
        return -1.0

    # Solo comprobar las primeras 4 palabras para no abusar de la API
    sample = words[:4]
    found  = 0
    try:
        for word in sample:
            cache_key = f"dm:{word}"
            with _LANG_CACHE_LOCK:
                if cache_key in _LANG_CACHE:
                    if _LANG_CACHE[cache_key] > 0:
                        found += 1
                    continue

            url = f"https://api.datamuse.com/words?sp={urllib.parse.quote(word)}&max=1"
            req = urllib.request.Request(url, headers={'User-Agent': 'PDFForensicsPro/3.1'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            score_val = 1.0 if data else 0.0
            with _LANG_CACHE_LOCK:
                _LANG_CACHE[cache_key] = score_val
            if data:
                found += 1

        return found / max(len(sample), 1)

    except Exception:
        _DATAMUSE_AVAILABLE = False
        return -1.0


def _best_score(text: str) -> float:
    """
    Scoring combinado: local siempre, API solo si el texto es largo
    y ambiguo (no se llama por cada string del PDF).
    """
    return _score_text_local(text)


# Contador global para limitar llamadas a la API
_API_CALL_COUNT = 0
_API_CALL_LOCK  = threading.Lock()
_API_MAX_CALLS  = 30   # máximo de llamadas a LanguageTool por análisis


def _api_score_if_budget(text: str) -> float:
    """
    Llama a LanguageTool solo si queda presupuesto de API y el texto
    tiene al menos 15 caracteres con palabras reales.
    Devuelve -1.0 si no se puede usar la API.
    """
    global _API_CALL_COUNT
    words = re.findall(r'[a-zA-Zàáâãäåæçèéêëìíîïòóôõöùúûü]{3,}', text)
    if len(words) < 2 or len(text.strip()) < 10:
        return -1.0
    with _API_CALL_LOCK:
        if _API_CALL_COUNT >= _API_MAX_CALLS:
            return -1.0
        _API_CALL_COUNT += 1
    return _score_text_languagetool(text)


def _is_readable(text: str, min_ratio: float = 0.55) -> bool:
    """
    Evaluación básica de legibilidad — ahora usa scoring local mejorado
    además del conteo de imprimibles. Mantenida por compatibilidad.
    """
    if not text:
        return False
    printable = sum(1 for c in text if c.isprintable() and c not in '·□▯')
    ratio_ok = printable / max(len(text), 1) >= min_ratio
    return ratio_ok


def _hex_to_bytes(hex_sequence: str) -> Optional[bytes]:
    """Convierte hex limpio a bytes, devuelve None si falla."""
    try:
        return bytes.fromhex(hex_sequence)
    except Exception:
        return None


# Tabla PDFDocEncoding compartida
_PDFDOC_EXTRA = {
    0x18: '\u02d8', 0x19: '\u02c7', 0x1a: '\u02c6', 0x1b: '\u02d9',
    0x1c: '\u02dd', 0x1d: '\u02db', 0x1e: '\u02da', 0x1f: '\u02dc',
    0x80: '\u2022', 0x81: '\u2020', 0x82: '\u2021', 0x83: '\u2026',
    0x84: '\u2014', 0x85: '\u2013', 0x86: '\u0192', 0x87: '\u2044',
    0x88: '\u2039', 0x89: '\u203a', 0x8a: '\u2212', 0x8b: '\u2030',
    0x8c: '\u201e', 0x8d: '\u201c', 0x8e: '\u201d', 0x8f: '\u2018',
    0x90: '\u2019', 0x91: '\u201a', 0x92: '\u2122', 0x93: '\ufb01',
    0x94: '\ufb02', 0x95: '\u0141', 0x96: '\u0152', 0x97: '\u0160',
    0x98: '\u0178', 0x99: '\u017d', 0x9a: '\u0131', 0x9b: '\u0142',
    0x9c: '\u0153', 0x9d: '\u0161', 0x9e: '\u017e', 0xa0: '\u20ac',
}


def decode_hex_universal(hex_sequence: str, tounicode: Dict[str, str]) -> str:
    """
    Motor de decodificación universal para strings hexadecimales de PDF.

    Diferencia clave respecto a v3.0:
      → Ya NO para en la primera estrategia que tenga caracteres imprimibles.
      → Genera TODOS los candidatos posibles y los puntúa lingüísticamente
        usando APIs gratuitas (LanguageTool + Datamuse) + heurística local.
      → Devuelve el candidato con la puntuación más alta.

    Estrategias evaluadas:
      1. ToUnicode CMap  (el más preciso cuando el mapa cubre los glifos)
      2. UTF-16BE con y sin BOM
      3. CP1252 / Latin-1 / ISO-8859-1
      4. ASCII imprimible puro
      5. PDFDocEncoding
      6. UTF-8 estricto
      7. Hex anotado legible (último recurso)
    """
    hex_sequence = hex_sequence.replace(' ', '').upper()
    if not hex_sequence:
        return ''
    if len(hex_sequence) % 2 != 0:
        hex_sequence = '0' + hex_sequence

    candidates: List[Tuple[str, str]] = []  # (nombre_estrategia, texto_candidato)

    # ── Estrategia 1: ToUnicode CMap ─────────────────────────────────────────
    if tounicode:
        for width in [2, 1]:
            chars = []
            ok_count = 0
            for j in range(0, len(hex_sequence), width * 2):
                gid = hex_sequence[j : j + width * 2]
                if len(gid) < width * 2:
                    break
                mapped = tounicode.get(gid) or tounicode.get(gid.lstrip('0') or '0')
                if mapped:
                    chars.append(mapped)
                    ok_count += 1
                else:
                    chars.append('·')
            if ok_count > 0 and ok_count >= len(chars) * 0.3:
                candidate = ''.join(chars).strip('·').strip()
                if candidate:
                    candidates.append((f'ToUnicode-w{width}', candidate))

    raw_bytes = _hex_to_bytes(hex_sequence)
    if raw_bytes is None:
        return ''

    # ── Estrategia 2: UTF-16BE ────────────────────────────────────────────────
    for with_bom in [True, False]:
        try:
            src = raw_bytes[2:] if (with_bom and raw_bytes[:2] == b'\xfe\xff') else raw_bytes
            candidate = src.decode('utf-16-be', errors='replace')
            candidate = ''.join(c for c in candidate if c.isprintable() or c in ' \t\n').strip()
            if candidate:
                candidates.append(('UTF-16BE', candidate))
        except Exception:
            pass

    # ── Estrategia 3: CP1252 / Latin-1 / ISO-8859-1 ──────────────────────────
    for enc in ('cp1252', 'latin-1', 'iso-8859-1'):
        try:
            candidate = raw_bytes.decode(enc, errors='replace')
            printable = ''.join(c for c in candidate if c.isprintable() or c == ' ').strip()
            if printable:
                candidates.append((enc, printable))
        except Exception:
            pass

    # ── Estrategia 4: ASCII imprimible ───────────────────────────────────────
    ascii_chars = [chr(b) for b in raw_bytes if 0x20 <= b <= 0x7E]
    if ascii_chars:
        candidates.append(('ASCII', ''.join(ascii_chars).strip()))

    # ── Estrategia 5: PDFDocEncoding ─────────────────────────────────────────
    try:
        pdfdoc_chars = []
        for b in raw_bytes:
            if b == 0xAD:
                pdfdoc_chars.append('\u00ad')
            elif b in _PDFDOC_EXTRA:
                pdfdoc_chars.append(_PDFDOC_EXTRA[b])
            elif b >= 0x20:
                pdfdoc_chars.append(chr(b))
        candidate = ''.join(pdfdoc_chars).strip()
        if candidate:
            candidates.append(('PDFDocEncoding', candidate))
    except Exception:
        pass

    # ── Estrategia 6: UTF-8 ──────────────────────────────────────────────────
    try:
        candidate = raw_bytes.decode('utf-8', errors='strict').strip()
        if candidate:
            candidates.append(('UTF-8', candidate))
    except Exception:
        pass

    # ── Estrategia 7: Hex anotado (último recurso) ───────────────────────────
    annotated = ''.join(chr(b) if 0x20 <= b <= 0x7E else f'\\x{b:02x}' for b in raw_bytes)
    candidates.append(('hex-annotated', annotated))

    if not candidates:
        return ''

    # ── SELECCIÓN: scoring local rápido para todos los candidatos ─────────────
    scored: List[Tuple[float, str, str]] = []
    for name, text in candidates:
        scored.append((_score_text_local(text), name, text))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_name, best_text = scored[0]

    # Si el mejor candidato sigue siendo muy malo → devolver hex anotado
    if best_score < 0.15:
        return annotated

    # Si los dos primeros candidatos están muy cerca (diferencia < 0.12)
    # Y el texto tiene suficiente longitud para merecer la API → desempatar con API
    if (len(scored) > 1
            and (scored[0][0] - scored[1][0]) < 0.12
            and len(best_text.strip()) >= 15):
        # Solo llamar a la API si queda presupuesto (máx. 30 llamadas por PDF)
        api_scores = []
        for sc, name, text in scored[:3]:  # solo los 3 mejores candidatos
            lt = _api_score_if_budget(text)
            if lt >= 0:
                api_scores.append((lt * 0.6 + sc * 0.4, name, text))
            else:
                api_scores.append((sc, name, text))
        if api_scores:
            api_scores.sort(key=lambda x: x[0], reverse=True)
            best_score, best_name, best_text = api_scores[0]

    return best_text


def decode_pdf_string_literal(s: str) -> str:
    """
    Decodifica un string literal de PDF (entre paréntesis).
    Maneja escapes PDF estándar, Unicode BOM, y texto plano.
    
    Soporta:
      - Escapes: \\n \\r \\t \\b \\f \\\\ \\( \\)
      - Octal:   \\ddd
      - UTF-16BE con BOM (\\xFE\\xFF...)
      - UTF-8
      - Latin-1 / CP1252
    """
    if not s:
        return s

    # Procesar escapes PDF
    result_bytes = bytearray()
    i = 0
    raw = s.encode('latin-1', errors='replace')
    while i < len(raw):
        if raw[i:i+1] == b'\\' and i + 1 < len(raw):
            next_b = raw[i+1:i+2]
            if next_b == b'n':
                result_bytes.append(0x0A); i += 2
            elif next_b == b'r':
                result_bytes.append(0x0D); i += 2
            elif next_b == b't':
                result_bytes.append(0x09); i += 2
            elif next_b == b'b':
                result_bytes.append(0x08); i += 2
            elif next_b == b'f':
                result_bytes.append(0x0C); i += 2
            elif next_b in (b'\\', b'(', b')'):
                result_bytes.append(raw[i+1]); i += 2
            elif next_b in b'01234567':
                # Octal: hasta 3 dígitos
                oct_str = raw[i+1:i+4].decode('latin-1')
                oct_match = re.match(r'([0-7]{1,3})', oct_str)
                if oct_match:
                    result_bytes.append(int(oct_match.group(1), 8) & 0xFF)
                    i += 1 + len(oct_match.group(1))
                else:
                    result_bytes.append(raw[i]); i += 1
            else:
                result_bytes.append(raw[i]); i += 1
        else:
            result_bytes.append(raw[i]); i += 1

    data = bytes(result_bytes)

    # Detectar UTF-16BE con BOM
    if data[:2] == b'\xfe\xff':
        try:
            return data[2:].decode('utf-16-be', errors='replace').strip()
        except Exception:
            pass

    # Intentar UTF-8
    try:
        candidate = data.decode('utf-8', errors='strict').strip()
        if _is_readable(candidate, 0.60):
            return candidate
    except Exception:
        pass

    # Latin-1 / CP1252
    for enc in ('cp1252', 'latin-1'):
        try:
            candidate = data.decode(enc, errors='replace').strip()
            if candidate and _is_readable(candidate, 0.65):
                return candidate
        except Exception:
            pass

    return data.decode('latin-1', errors='replace').strip()


def decode_hex_string_with_tounicode(
    hex_sequence: str,
    tounicode: Dict[str, str],
    glyph_width: int = 2
) -> str:
    """
    Decodifica una secuencia hex usando el motor universal.
    Mantiene compatibilidad con el código existente.
    """
    return decode_hex_universal(hex_sequence, tounicode)


def decode_tj_operator(tj_content: str, tounicode: Dict[str, str]) -> str:
    """
    Decodifica el contenido de un operador TJ o Tj.
    
    Maneja:
      - Strings hex:   <01021A1B>
      - Strings texto: (Hello)
      - Arrays TJ:     [<01> 6 <02> -3 <03>]
    """
    result_parts = []

    # Extraer todas las partes: hex strings y strings de texto
    parts = re.findall(r'<([0-9A-Fa-f\s]+)>|\(([^)]*)\)', tj_content)

    for hex_part, text_part in parts:
        if hex_part:
            hex_clean = hex_part.replace(' ', '').upper()
            decoded = decode_hex_universal(hex_clean, tounicode)
            if decoded:
                result_parts.append(decoded)
        elif text_part:
            # String literal: decodificar con motor completo
            decoded = decode_pdf_string_literal(text_part)
            if decoded.strip():
                result_parts.append(decoded)

    # Si no hubo partes, intentar decodificación directa de todo el contenido
    if not result_parts:
        # Buscar hex sueltos
        hex_matches = re.findall(r'<([0-9A-Fa-f\s]{2,})>', tj_content)
        for hx in hex_matches:
            decoded = decode_hex_universal(hx.replace(' ', ''), tounicode)
            if decoded:
                result_parts.append(decoded)

    return ''.join(result_parts).strip()


def decode_full_stream_text(stream_text: str, tounicode: Dict[str, str]) -> List[str]:
    """
    Extrae y decodifica todo el texto de un stream PDF usando ToUnicode.
    Busca bloques BT...ET y opera sobre Tj / TJ.
    
    Retorna lista de strings de texto legible.
    """
    decoded_texts = []

    bt_blocks = re.findall(r'BT(.*?)ET', stream_text, re.DOTALL)

    for block in bt_blocks:
        # TJ operator (array): [<..> kern <..> ...]TJ
        for m in re.finditer(r'\[([^\]]+)\]\s*TJ', block):
            decoded = decode_tj_operator(m.group(1), tounicode)
            if decoded and len(decoded.strip()) > 0:
                decoded_texts.append(decoded)

        # Tj operator (single string): (<...>)Tj o <...>Tj
        for m in re.finditer(r'<([0-9A-Fa-f\s]+)>\s*Tj', block):
            decoded = decode_hex_string_with_tounicode(
                m.group(1).replace(' ', ''), tounicode
            )
            if decoded:
                decoded_texts.append(decoded)

        # Tj con string de texto plano: (texto)Tj — motor universal
        for m in re.finditer(r'\(([^)]{1,500})\)\s*Tj', block):
            t = decode_pdf_string_literal(m.group(1))
            if t and t.strip():
                decoded_texts.append(t.strip())

    return [t for t in decoded_texts if t.strip()]


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 01 · METADATOS EXTENDIDOS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_metadata(pdf_path: str, pdf_bytes: bytes, result: PDFForensicResult):
    section("MÓDULO 01 · Metadatos extendidos", "📋")

    # ── Metadatos via pypdf ───────────────────────────────────────────────────
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)

        result.metadata["encrypted"]  = reader.is_encrypted
        result.metadata["page_count"] = len(reader.pages)

        if reader.is_encrypted:
            result.add("metadata", "HIGH", "PDF CIFRADO",
                        "El documento está encriptado — puede ocultar contenido")

        meta = reader.metadata or {}
        docinfo = {}
        for k, v in meta.items():
            if v:
                docinfo[str(k)] = str(v)

        result.metadata["docinfo"] = docinfo

        if docinfo:
            hit("Metadatos DocInfo", "", "INFO")
            for k, v in docinfo.items():
                print(f"      {DIM}{k}: {Y}{v}{RST}")

        # Detectar discordancias de fecha (señal de manipulación)
        creation = docinfo.get("/CreationDate", "")
        modification = docinfo.get("/ModDate", "")
        if creation and modification and creation > modification:
            result.add("metadata", "HIGH",
                        "INCONSISTENCIA DE FECHAS",
                        f"Fecha de creación ({creation}) posterior a fecha de modificación ({modification})")

        # Productor / Creator sospechosos
        producer = docinfo.get("/Producer", "")
        creator  = docinfo.get("/Creator", "")
        for field, val in [("Producer", producer), ("Creator", creator)]:
            if val:
                info(f"{field}: {val}")
                result.metadata[field.lower()] = val
                # Detectar herramientas de edición conocidas
                for tool in ["Adobe Acrobat", "Ghostscript", "iTextSharp", "FPDF",
                              "LibreOffice", "Nitro", "Foxit", "PDFsam", "qpdf",
                              "pdftk", "cpdf", "pdfcrop", "pdflatex"]:
                    if tool.lower() in val.lower():
                        result.add("metadata", "INFO",
                                    f"Herramienta detectada: {field}",
                                    f"{val}")
                        break

    except ImportError:
        info("pypdf no disponible")
    except Exception as e:
        result.add_error(f"metadata/pypdf: {e}")

    # ── Metadatos XMP via pikepdf ─────────────────────────────────────────────
    try:
        import pikepdf
        pdf = pikepdf.open(pdf_path)
        try:
            with pdf.open_metadata() as meta:
                xmp = dict(meta)
                if xmp:
                    result.metadata["xmp"] = {k: str(v) for k, v in xmp.items()}
                    hit("Metadatos XMP", f"{len(xmp)} entradas", "INFO")
                    for k, v in xmp.items():
                        print(f"      {DIM}{k}: {Y}{str(v)}{RST}")
                    # XMP con fechas adicionales
                    for k in ["xmp:CreateDate", "xmp:ModifyDate", "xmp:MetadataDate"]:
                        if k in xmp:
                            result.add("metadata", "INFO", f"Fecha XMP: {k}", str(xmp[k]))
        finally:
            pdf.close()
    except ImportError:
        info("pikepdf no disponible para XMP")
    except Exception as e:
        result.add_error(f"metadata/xmp: {e}")

    # ── Metadatos raw en bytes ────────────────────────────────────────────────
    xmp_raw = re.search(rb'<x:xmpmeta.*?</x:xmpmeta>', pdf_bytes, re.DOTALL)
    if xmp_raw:
        xmp_text = xmp_raw.group(0).decode("utf-8", errors="replace")
        result.metadata["xmp_raw_length"] = len(xmp_text)
        # Buscar campos sensibles en XMP
        for tag in ["dc:creator", "dc:title", "dc:description", "xmpMM:DocumentID",
                     "xmpMM:InstanceID", "xmpMM:History"]:
            m = re.search(rf'<{re.escape(tag)}[^>]*>(.*?)</{re.escape(tag)}>', xmp_text, re.DOTALL)
            if m:
                val = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                if val:
                    result.add("metadata", "INFO", f"XMP campo: {tag}", val)

    ok("Módulo 01 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 02 · ACTUALIZACIONES INCREMENTALES
# ══════════════════════════════════════════════════════════════════════════════

def analyze_incremental_updates(pdf_bytes: bytes, result: PDFForensicResult):
    section("MÓDULO 02 · Actualizaciones incrementales (versiones ocultas)", "🔄")

    eof_positions = [m.start() for m in re.finditer(rb'%%EOF', pdf_bytes)]
    result.metadata["versions"] = len(eof_positions)

    if len(eof_positions) > 1:
        result.add("incremental", "HIGH",
                    "MÚLTIPLES VERSIONES DEL PDF",
                    f"Se detectaron {len(eof_positions)} versiones — contenido anterior puede estar oculto")

        for i, pos in enumerate(eof_positions):
            info(f"Versión {i+1}: termina en byte {pos:,}")

            if i > 0:
                prev_pos = eof_positions[i - 1]
                delta = pdf_bytes[prev_pos + 5 : pos + 5]

                # Objetos modificados en esta actualización
                obj_nums = [int(o) for o in re.findall(rb'(\d+)\s+\d+\s+obj', delta)]
                if obj_nums:
                    result.add("incremental", "MED",
                                f"Versión {i+1}: Objetos modificados",
                                f"IDs: {obj_nums}")

                # Texto en el delta
                strings_raw = re.findall(rb'\(([^)]{3,150})\)', delta)
                texts = []
                for s in strings_raw:
                    try:
                        dec = _decode_safe(s)
                        if re.search(r'[a-zA-Z]{2,}', dec) and not any(
                            kw in dec for kw in ["Type", "Font", "Pages", "Catalog", "Resources"]
                        ):
                            texts.append(dec)
                    except Exception:
                        pass

                if texts:
                    combined = " | ".join(texts)
                    result.add("incremental", "HIGH",
                                f"Versión {i+1}: Texto anterior (posiblemente borrado)",
                                combined)

                # Buscar xref para esta actualización
                xref_in_delta = re.findall(rb'(\d{10})\s+(\d{5})\s+([fn])', delta)
                free_in_delta = [(int(off), int(gen)) for off, gen, st in xref_in_delta if st == b'f']
                if free_in_delta:
                    result.add("incremental", "HIGH",
                                f"Versión {i+1}: Objetos marcados como borrados",
                                f"{len(free_in_delta)} objetos eliminados en esta versión")
    else:
        info("PDF de versión única (sin actualizaciones incrementales)")

    ok("Módulo 02 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 03 · OBJETOS ELIMINADOS (XREF FREE)
# ══════════════════════════════════════════════════════════════════════════════

def analyze_deleted_objects(pdf_bytes: bytes, result: PDFForensicResult):
    section("MÓDULO 03 · Objetos eliminados (xref free entries)", "🗑️")

    deleted = []

    # ── Xref tabla clásica ────────────────────────────────────────────────────
    xref_pos = pdf_bytes.rfind(b'xref')
    if xref_pos != -1:
        xref_data = pdf_bytes[xref_pos:]
        lines = xref_data.split(b'\n')
        current_obj = 0
        for line in lines:
            line = line.strip()
            if line == b'trailer':
                break
            m = re.match(rb'(\d{10})\s+(\d{5})\s+([fn])', line)
            if m:
                offset, gen, status = int(m.group(1)), int(m.group(2)), m.group(3)
                if status == b'f' and offset != 0:
                    # Intentar recuperar contenido del offset anterior
                    preview = ""
                    try:
                        snippet = pdf_bytes[offset:offset + 1024]
                        decompressed = _try_decompress(snippet)
                        if decompressed:
                            preview = _decode_safe(decompressed).strip()
                        else:
                            preview = _decode_safe(snippet).strip()
                        # Extraer strings del contenido recuperado
                        txt_matches = re.findall(r'\(([^)]{3,})\)', preview)
                        if txt_matches:
                            preview = " | ".join(txt_matches)
                    except Exception:
                        pass

                    result.add("deleted_objects", "HIGH",
                                f"Objeto #{current_obj} ELIMINADO (xref free)",
                                f"Offset previo: {offset}, Gen: {gen}" +
                                (f" | Contenido recuperado: {preview}" if preview else ""))
                    deleted.append((current_obj, offset, gen))
                current_obj += 1
            elif re.match(rb'\d+\s+\d+$', line):
                parts = line.split()
                if len(parts) == 2:
                    current_obj = int(parts[0])
    else:
        # ── Xref stream (PDF 1.5+) ────────────────────────────────────────────
        info("No se encontró tabla xref clásica — puede usar xref stream (PDF 1.5+)")
        all_objs = set(int(m.group(1)) for m in re.finditer(rb'(\d+)\s+\d+\s+obj', pdf_bytes))
        info(f"Objetos encontrados en binario: {len(all_objs)}")

        # Buscar objetos huérfanos comparando con el número total
        if all_objs:
            max_obj = max(all_objs)
            expected = set(range(1, max_obj + 1))
            missing = expected - all_objs
            if missing and len(missing) < 100:
                result.add("deleted_objects", "MED",
                            "Posibles objetos faltantes en xref stream",
                            f"IDs no encontrados como objetos: {sorted(missing)}")

    if not deleted:
        info("No se encontraron objetos marcados como eliminados en xref")
    else:
        info(f"Total objetos eliminados: {len(deleted)}")

    ok("Módulo 03 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 04 · STREAMS Y OPERADORES DE TEXTO
# ══════════════════════════════════════════════════════════════════════════════

def analyze_streams(pdf_bytes: bytes, result: PDFForensicResult,
                    tounicode: Dict[str, str] = None) -> List[Tuple]:
    section("MÓDULO 04 · Descompresión de streams y operadores de texto", "📦")

    stream_pat = re.compile(rb'stream\r?\n(.*?)endstream', re.DOTALL)
    all_streams = stream_pat.findall(pdf_bytes)
    info(f"Streams encontrados: {len(all_streams)}")

    all_text_ops = []
    suspicious_streams = []

    for i, raw in enumerate(all_streams):
        text = None

        # Intentar descomprimir
        decompressed = _try_decompress(raw)
        if decompressed:
            text = _decode_safe(decompressed)
        else:
            # Intentar como texto plano
            decoded = _decode_safe(raw)
            if re.search(r'BT.*?ET', decoded, re.DOTALL):
                text = decoded

        if not text:
            continue

        # Extraer operadores Tj / TJ con decodificación ToUnicode
        ops = _extract_text_operators(text, i, tounicode or {})
        if ops:
            all_text_ops.extend(ops)

        # Detectar contenido sospechoso
        suspicious = _check_stream_suspicious(text, i, result, tounicode or {})
        if suspicious:
            suspicious_streams.append((i, text))

        # Detectar JavaScript en streams
        if re.search(r'\beval\b|\bfunction\b|\bdocument\b|\bapp\.alert\b', text, re.IGNORECASE):
            result.add("javascript", "CRITICAL",
                        f"JavaScript detectado en stream #{i}",
                        text.strip())

    info(f"Operadores de texto extraídos: {len(all_text_ops)}")
    info(f"Streams sospechosos: {len(suspicious_streams)}")
    ok("Módulo 04 completado")
    return all_text_ops, suspicious_streams


def _extract_text_operators(stream_text: str, idx: int,
                             tounicode: Dict[str, str] = None) -> List[Tuple]:
    """
    Extrae y decodifica operadores de texto Tj/TJ.
    Usa el mapa ToUnicode para convertir glifos hex en texto legible.
    """
    results = []
    tu = tounicode or {}

    # TJ arrays: [<hex> kern <hex> kern ...]TJ  — el formato más común en PDFs con encoding custom
    for m in re.finditer(r'\[([^\]]{1,2000})\]\s*TJ', stream_text):
        raw = m.group(1)
        decoded = decode_tj_operator(raw, tu)
        if not decoded:
            plain = re.findall(r'\(([^)]*)\)', raw)
            decoded = ''.join(plain)
        if decoded.strip():
            results.append(("TJ", decoded.strip(), idx))

    # Tj hex: <...>Tj
    for m in re.finditer(r'<([0-9A-Fa-f\s]{2,200})>\s*Tj', stream_text):
        decoded = decode_hex_string_with_tounicode(m.group(1).replace(' ', ''), tu)
        if decoded.strip():
            results.append(("Tj_hex", decoded.strip(), idx))

    # Tj texto plano: (texto)Tj — decodificar con motor universal
    for m in re.finditer(r'\(([^)]{1,500})\)\s*Tj', stream_text):
        t = decode_pdf_string_literal(m.group(1))
        if t and t.strip():
            results.append(("Tj", t.strip(), idx))

    return results


def _check_stream_suspicious(text: str, idx: int, result: PDFForensicResult,
                              tounicode: Dict[str, str] = None) -> bool:
    found = False
    tu = tounicode or {}

    checks = [
        (r'\b1\s+1\s+1\s+rg\b',               "CRITICAL", "Color texto BLANCO (invisible sobre fondo blanco)"),
        (r'\b1\s+g\b',                          "HIGH",     "Escala de grises blanco"),
        (r'/ca\s+0\b',                          "HIGH",     "Alpha=0 (texto completamente transparente)"),
        (r'\b3\s+Tr\b',                         "HIGH",     "Render mode 3 (texto invisible)"),
        (r'\b0\s+Tz\b',                         "HIGH",     "Escala horizontal de texto = 0 (invisible)"),
        (r'\b0\s+Tf\b',                         "MED",      "Tamaño de fuente = 0"),
        (r'/Opacity\s+0\b',                     "HIGH",     "Opacidad = 0 (transparente)"),
        (r'1\s+1\s+1\s+RG\b',                  "HIGH",     "Color trazo BLANCO"),
    ]

    for pattern, level, desc in checks:
        if re.search(pattern, text):
            result.add("invisible_text", level,
                        f"Stream #{idx}: {desc}",
                        _extract_nearby_text(text, pattern, tu))
            found = True

    # Texto fuera de página
    for m in re.finditer(r'(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s+Td', text):
        x, y = float(m.group(1)), float(m.group(2))
        if x < -200 or y < -200 or x > 3000 or y > 3000:
            result.add("invisible_text", "HIGH",
                        f"Stream #{idx}: Texto fuera de página",
                        f"Td {x} {y}")
            found = True
            break

    # Rectángulos negros (redacciones)
    black_rects = re.findall(
        r'(?:0\s+g|0\s+0\s+0\s+rg)[^a-z]*?(\d+\.?\d*)\s+(\d+\.?\d*)\s+(\d+\.?\d*)\s+(\d+\.?\d*)\s+re\s+f',
        text
    )
    for rect in black_rects:
        w, h = float(rect[2]), float(rect[3])
        if w > 20 and h > 5:
            result.add("redaction", "HIGH",
                        f"Stream #{idx}: Rectángulo negro (posible redacción)",
                        f"x={rect[0]} y={rect[1]} ancho={w:.0f} alto={h:.0f} pts")
            found = True

    # Rectángulos blancos sobre texto
    white_rects = re.findall(r'1\s+1\s+1\s+rg.*?re\s+f', text, re.DOTALL)
    if white_rects:
        result.add("redaction", "HIGH",
                    f"Stream #{idx}: Rectángulos blancos sobre texto",
                    f"{len(white_rects)} rectángulos blancos encontrados")
        found = True

    return found


def _extract_nearby_text(stream: str, pattern: str,
                         tounicode: Dict[str, str] = None) -> str:
    """
    Extrae el texto real de la zona afectada del stream.
    Intenta decodificar operadores TJ/Tj y strings hex antes de devolver raw.
    """
    tu = tounicode or {}

    # 1. Buscar y decodificar todos los bloques BT...ET del stream
    decoded_parts = []
    for bt_block in re.finditer(r'BT(.*?)ET', stream, re.DOTALL):
        block = bt_block.group(1)
        decoded = decode_full_stream_text(block, tu)
        if decoded:
            decoded_parts.extend(decoded)

    if decoded_parts:
        text_out = " ".join(decoded_parts).strip()
        if text_out and _score_text_local(text_out) > 0.15:
            return f"[TEXTO DECODIFICADO] {text_out}"

    # 2. Intentar decodificar hex strings UTF-16BE con BOM (FEFF...)
    feff_matches = re.findall(r'FEFF([0-9A-Fa-f]{4,})', stream)
    if feff_matches:
        titles = []
        for hx in feff_matches[:5]:
            try:
                b = bytes.fromhex(hx)
                t = b.decode('utf-16-be', errors='replace').strip()
                t = ''.join(c for c in t if c.isprintable())
                if t and len(t) > 2:
                    titles.append(t)
            except Exception:
                pass
        if titles:
            return f"[UTF-16BE] {' | '.join(titles)}"

    # 3. Contexto raw con hex decodificado inline
    m = re.search(pattern, stream)
    if not m:
        return ""
    start = max(0, m.start() - 150)
    end   = min(len(stream), m.end() + 150)
    context = stream[start:end].strip()

    def replace_hex(hm):
        raw = hm.group(1).replace(' ', '')
        decoded = decode_hex_universal(raw, tu)
        if decoded and _score_text_local(decoded) > 0.2:
            return f'[{decoded}]'
        return hm.group(0)

    return re.sub(r'<([0-9A-Fa-f\s]{2,200})>', replace_hex, context)


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 05 · TEXTO INVISIBLE (ANÁLISIS PROFUNDO)
# ══════════════════════════════════════════════════════════════════════════════

def analyze_invisible_text(pdf_bytes: bytes, result: PDFForensicResult,
                            tounicode: Dict[str, str] = None):
    section("MÓDULO 05 · Texto invisible (colores, transparencia, render mode)", "👻")
    tu = tounicode or {}

    stream_pat = re.compile(rb'stream\r?\n(.*?)endstream', re.DOTALL)
    bt_found = 0

    for i, m in enumerate(stream_pat.finditer(pdf_bytes)):
        raw = m.group(1)
        decompressed = _try_decompress(raw)
        text = _decode_safe(decompressed if decompressed else raw)

        bt_blocks = re.findall(r'BT(.*?)ET', text, re.DOTALL)
        for block in bt_blocks:
            bt_found += 1
            invisible_reason = None

            if re.search(r'\b1\s+1\s+1\s+[Rr][Gg]\b', block):
                invisible_reason = "Color blanco (1 1 1 rg)"
            elif re.search(r'\b3\s+[Tt][Rr]\b', block):
                invisible_reason = "Render mode 3 (invisible)"
            elif re.search(r'/ca\s*0\b', block, re.IGNORECASE):
                invisible_reason = "Alpha canal = 0"
            elif re.search(r'\b0\s+[Tt][Zz]\b', block):
                invisible_reason = "Escala horizontal 0 (Tz 0)"

            if invisible_reason:
                # ── Decodificar con ToUnicode (prioridad máxima) ──────────────
                decoded_texts = decode_full_stream_text(block, tu)
                text_content  = " ".join(decoded_texts).strip()

                # Fallback 1: strings de texto plano entre paréntesis
                if not text_content:
                    extracted = re.findall(r'\(([^)]{1,200})\)', block)
                    decoded_literals = [decode_pdf_string_literal(s) for s in extracted]
                    text_content = " ".join(t for t in decoded_literals if t.strip())

                # Fallback 2: decodificación universal de hex con motor completo
                if not text_content:
                    hex_raw = re.findall(r'<([0-9A-Fa-f\s]{2,100})>', block)
                    decoded_hexes = []
                    for hx in hex_raw:
                        decoded = decode_hex_universal(hx.replace(' ', ''), {})
                        if decoded and decoded.strip():
                            decoded_hexes.append(decoded)
                    if decoded_hexes:
                        text_content = " ".join(decoded_hexes)

                # Fallback 3: hex anotado como último recurso (siempre algo visible)
                if not text_content:
                    hex_raw = re.findall(r'<([0-9A-Fa-f\s]{2,100})>', block)
                    if hex_raw:
                        text_content = "[hex sin decodificación posible]: " + " | ".join(hex_raw)

                if text_content:
                    result.add("invisible_text", "CRITICAL",
                                f"TEXTO INVISIBLE EXTRAÍDO [{invisible_reason}]",
                                text_content,
                                raw_data=block)

    info(f"Bloques BT...ET analizados: {bt_found}")
    ok("Módulo 05 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 06 · REDACCIONES
# ══════════════════════════════════════════════════════════════════════════════

def analyze_redactions(pdf_bytes: bytes, result: PDFForensicResult):
    section("MÓDULO 06 · Detección de redacciones y censura", "⬛")

    # Anotaciones /Redact formales
    if re.search(rb'/Subtype\s*/Redact', pdf_bytes):
        result.add("redaction", "CRITICAL",
                    "ANOTACIONES REDACT FORMALES",
                    "El PDF contiene marcas de redacción de Adobe Acrobat")

        # Overlay text
        for ot in re.findall(rb'/OverlayText\s*\(([^)]*)\)', pdf_bytes):
            result.add("redaction", "HIGH",
                        "Overlay text en redacción",
                        ot.decode("latin-1", errors="replace"))

        # QuadPoints (coordenadas de redacciones)
        quad_matches = re.findall(rb'/QuadPoints\s*\[([^\]]+)\]', pdf_bytes)
        if quad_matches:
            result.add("redaction", "INFO",
                        "Coordenadas de redacciones (QuadPoints)",
                        f"{len(quad_matches)} regiones redactadas")

    # Rectángulos negros en flujo de contenido
    black_fill_pattern = re.compile(
        rb'(?:0\s+g|0\s+0\s+0\s+rg)\s*\n?'
        rb'(\d+\.?\d*)\s+(\d+\.?\d*)\s+(\d+\.?\d*)\s+(\d+\.?\d*)\s+re\s+f'
    )
    fills = black_fill_pattern.findall(pdf_bytes)
    real_redactions = [(x, y, w, h) for x, y, w, h in
                       [(float(a), float(b), float(c), float(d)) for a, b, c, d in fills]
                       if w > 20 and h > 5]
    if real_redactions:
        result.add("redaction", "HIGH",
                    "Rectángulos negros que cubren texto",
                    f"{len(real_redactions)} rectángulos sospechosos detectados. "
                    f"Ej: {real_redactions[0]}")

    ok("Módulo 06 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 07 · CAPAS OCG OCULTAS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_ocg_layers(pdf_path: str, result: PDFForensicResult):
    section("MÓDULO 07 · Capas OCG ocultas (Optional Content Groups)", "🎭")

    try:
        import pikepdf
        pdf = pikepdf.open(pdf_path)
        try:
            root = pdf.Root
            if '/OCProperties' not in root:
                info("Sin capas OCG en este PDF")
                return

            result.add("ocg_layers", "HIGH",
                        "PDF CON CAPAS OPCIONALES",
                        "El documento tiene capas de contenido que pueden estar ocultas")

            ocg_props = root['/OCProperties']

            # Listar todas las capas
            if '/OCGs' in ocg_props:
                for ocg_obj in ocg_props['/OCGs']:
                    try:
                        name = str(ocg_obj.get('/Name', '(sin nombre)'))
                        intent = str(ocg_obj.get('/Intent', 'View'))
                        result.add("ocg_layers", "MED",
                                    f"Capa OCG: {name}",
                                    f"Intent: {intent}")
                    except Exception:
                        pass

            # Configuración por defecto
            if '/D' in ocg_props:
                default_cfg = ocg_props['/D']
                # Capas apagadas por defecto
                if '/OFF' in default_cfg:
                    off_layers = []
                    for off_ocg in default_cfg['/OFF']:
                        try:
                            name = str(off_ocg.get('/Name', '(sin nombre)'))
                            off_layers.append(name)
                        except Exception:
                            pass
                    if off_layers:
                        result.add("ocg_layers", "CRITICAL",
                                    "CAPAS OCULTAS POR DEFECTO (OFF)",
                                    f"Capas no visibles al abrir: {off_layers}")

                # BaseState
                base = str(default_cfg.get('/BaseState', 'ON'))
                if base == 'OFF':
                    result.add("ocg_layers", "CRITICAL",
                                "BaseState = OFF",
                                "Todas las capas están ocultas por defecto")
        finally:
            pdf.close()
    except ImportError:
        info("pikepdf no disponible para análisis de capas")
    except Exception as e:
        result.add_error(f"ocg: {e}")

    ok("Módulo 07 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 08 · ANOTACIONES OCULTAS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_annotations(pdf_path: str, result: PDFForensicResult):
    section("MÓDULO 08 · Anotaciones ocultas y contenido embebido", "💬")

    try:
        import pikepdf
        pdf = pikepdf.open(pdf_path)
        try:
            for page_num, page in enumerate(pdf.pages):
                if '/Annots' not in page:
                    continue
                for annot in page['/Annots']:
                    try:
                        subtype  = str(annot.get('/Subtype', ''))
                        flags    = int(annot.get('/F', 0))
                        contents = str(annot.get('/Contents', ''))
                        rc       = str(annot.get('/RC', ''))
                        subject  = str(annot.get('/Subj', ''))

                        page_label = page_num + 1

                        # Flag 2=Hidden, Flag 6=NoView, Flag 7=Print invisible
                        if flags & 2:
                            result.add("annotations", "HIGH",
                                        f"Pág {page_label}: Anotación OCULTA (Hidden flag)",
                                        f"Tipo: {subtype}, Flags: {flags}")
                        if flags & 64:
                            result.add("annotations", "MED",
                                        f"Pág {page_label}: Anotación NoView",
                                        f"Tipo: {subtype}")

                        if contents and len(contents.strip()) > 2:
                            result.add("annotations", "MED",
                                        f"Pág {page_label}: Contenido de anotación",
                                        contents,
                                        page=page_label)
                        if rc and len(rc.strip()) > 2:
                            # Limpiar HTML del RichText
                            clean_rc = re.sub(r'<[^>]+>', '', rc).strip()
                            if clean_rc:
                                result.add("annotations", "MED",
                                            f"Pág {page_label}: RichText en anotación",
                                            clean_rc,
                                            page=page_label)
                        if subject:
                            result.add("annotations", "INFO",
                                        f"Pág {page_label}: Asunto de anotación",
                                        subject)
                    except Exception:
                        pass
        finally:
            pdf.close()
    except ImportError:
        info("pikepdf no disponible para anotaciones")
    except Exception as e:
        result.add_error(f"annotations: {e}")

    ok("Módulo 08 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 09 · JAVASCRIPT Y ACCIONES AUTOMÁTICAS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_javascript(pdf_bytes: bytes, result: PDFForensicResult):
    section("MÓDULO 09 · JavaScript embebido y acciones automáticas", "⚡")

    # Detectar /JavaScript
    if re.search(rb'/JavaScript', pdf_bytes):
        result.add("javascript", "CRITICAL",
                    "JAVASCRIPT ENCONTRADO EN EL PDF",
                    "El documento contiene código JavaScript embebido")

    # Extraer código JavaScript
    js_streams = re.findall(rb'/JS\s*\(([^)]{1,2000})\)', pdf_bytes)
    js_streams += re.findall(rb'/JS\s*<<[^>]*>>', pdf_bytes)

    for js in js_streams:
        js_text = _decode_safe(js)
        result.add("javascript", "CRITICAL",
                    "Código JavaScript extraído",
                    js_text)

    # Acciones automáticas (/AA, /OpenAction)
    for action_type in [b'/OpenAction', b'/AA', b'/AcroForm']:
        if action_type in pdf_bytes:
            result.add("javascript", "HIGH",
                        f"Acción automática: {action_type.decode()}",
                        "El PDF ejecuta acciones al abrirse o interactuar")

    # Buscar JavaScript en streams comprimidos
    stream_pat = re.compile(rb'stream\r?\n(.*?)endstream', re.DOTALL)
    for raw in stream_pat.findall(pdf_bytes):
        dec = _try_decompress(raw)
        text = _decode_safe(dec if dec else raw)
        # Patrones JS sospechosos
        if any(re.search(p, text) for p in [
            r'\beval\s*\(', r'app\.alert\s*\(', r'this\.exportDataObject',
            r'util\.printf', r'String\.fromCharCode', r'document\.write'
        ]):
            result.add("javascript", "CRITICAL",
                        "Patrón JavaScript sospechoso en stream",
                        text)

    if not any(f.module == "javascript" for f in result.findings):
        info("No se encontró JavaScript")

    ok("Módulo 09 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 10 · ARCHIVOS ADJUNTOS EMBEBIDOS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_embedded_files(pdf_bytes: bytes, pdf_path: str, result: PDFForensicResult):
    section("MÓDULO 10 · Archivos adjuntos embebidos", "📎")

    # Buscar EmbeddedFile en el PDF
    if re.search(rb'/EmbeddedFile', pdf_bytes):
        result.add("embedded_files", "HIGH",
                    "ARCHIVOS EMBEBIDOS DETECTADOS",
                    "El PDF contiene archivos adjuntos")

    # Extraer nombres de archivos adjuntos
    names = re.findall(rb'/UF\s*\(([^)]+)\)', pdf_bytes)
    names += re.findall(rb'/F\s*\(([^)]+)\)', pdf_bytes)

    seen_names = set()
    for name_raw in names:
        name = _decode_safe(name_raw)
        if name not in seen_names and re.search(r'\.[a-zA-Z]{2,5}$', name):
            seen_names.add(name)
            # Extensiones peligrosas
            ext = name.rsplit('.', 1)[-1].lower()
            dangerous = ext in ['exe', 'bat', 'cmd', 'js', 'vbs', 'ps1', 'sh',
                                  'dll', 'scr', 'com', 'msi', 'jar', 'py']
            level = "CRITICAL" if dangerous else "HIGH"
            result.add("embedded_files", level,
                        f"Archivo adjunto: {name}",
                        f"Extensión: .{ext}" + (" ⚠ PELIGROSA" if dangerous else ""))

    try:
        import pikepdf
        pdf = pikepdf.open(pdf_path)
        try:
            root = pdf.Root
            if '/Names' in root:
                names_dict = root['/Names']
                if '/EmbeddedFiles' in names_dict:
                    ef = names_dict['/EmbeddedFiles']
                    if '/Names' in ef:
                        ef_names = list(ef['/Names'])
                        info(f"Archivos embebidos via Names tree: {len(ef_names)//2}")
                        for j in range(0, len(ef_names) - 1, 2):
                            fname = str(ef_names[j])
                            result.add("embedded_files", "HIGH",
                                        f"Adjunto via Names: {fname}", "")
        finally:
            pdf.close()
    except Exception:
        pass

    ok("Módulo 10 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 11 · FUENTES Y ENCODINGS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_fonts(pdf_bytes: bytes, result: PDFForensicResult):
    section("MÓDULO 11 · Fuentes, encodings y glifos no estándar", "🔤")

    # Fuentes con ToUnicode (podría ocultar texto mediante remapeo)
    to_unicode_count = len(re.findall(rb'/ToUnicode', pdf_bytes))
    if to_unicode_count > 0:
        result.add("fonts", "INFO",
                    "Fuentes con mapa ToUnicode",
                    f"{to_unicode_count} fuentes con ToUnicode (remapeo de caracteres)")

    # Fuentes embebidas
    embedded_fonts = re.findall(rb'/FontFile[23]?\s', pdf_bytes)
    result.metadata["embedded_fonts"] = len(embedded_fonts)
    info(f"Fuentes con archivo embebido: {len(embedded_fonts)}")

    # Diferencias en encoding (puede camuflar texto)
    differences = re.findall(rb'/Differences\s*\[([^\]]{1,500})\]', pdf_bytes)
    if differences:
        result.add("fonts", "MED",
                    "Diferencias de encoding en fuentes",
                    f"{len(differences)} fuentes con tabla /Differences (posible camouflage de texto)")
        for diff in differences:
            diff_text = _decode_safe(diff)
            if re.search(r'/[A-Za-z]', diff_text):
                result.add("fonts", "INFO",
                            "Encoding personalizado",
                            diff_text)

    # Fuentes con nombres sospechosos
    font_names = re.findall(rb'/BaseFont\s*/([^\s/>\[]+)', pdf_bytes)
    for fn in font_names:
        name = fn.decode("latin-1", errors="replace")
        # Nombres de fuentes que ocultan información
        if re.search(r'[Ii]nvisible|[Hh]idden|[Ww]hite|0000', name):
            result.add("fonts", "HIGH",
                        "Fuente con nombre sospechoso",
                        f"BaseFont: {name}")

    ok("Módulo 11 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 12 · FORM XOBJECTS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_form_xobjects(pdf_path: str, result: PDFForensicResult):
    section("MÓDULO 12 · Form XObjects con texto oculto", "📝")

    try:
        import pikepdf
        pdf = pikepdf.open(pdf_path)
        try:
            for page_num, page in enumerate(pdf.pages):
                try:
                    resources = page.get('/Resources', {})
                    if '/XObject' not in resources:
                        continue
                    for name, xobj in resources['/XObject'].items():
                        try:
                            if str(xobj.get('/Subtype', '')) != '/Form':
                                continue
                            raw = bytes(xobj.read_raw_bytes())
                            dec = _try_decompress(raw)
                            text = _decode_safe(dec if dec else raw)

                            tj = re.findall(r'\(([^)]{2,200})\)\s*Tj', text)
                            if tj:
                                combined = " ".join(tj)
                                result.add("form_xobjects", "HIGH",
                                            f"Pág {page_num+1}: Texto en Form XObject '{name}'",
                                            combined,
                                            page=page_num + 1)
                        except Exception:
                            pass
                except Exception:
                    pass
        finally:
            pdf.close()
    except ImportError:
        info("pikepdf no disponible")
    except Exception as e:
        result.add_error(f"form_xobjects: {e}")

    ok("Módulo 12 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 13 · FIRMAS DIGITALES
# ══════════════════════════════════════════════════════════════════════════════

def analyze_signatures(pdf_bytes: bytes, result: PDFForensicResult):
    section("MÓDULO 13 · Firmas digitales y certificados", "🔏")

    if re.search(rb'/Sig\b', pdf_bytes) or re.search(rb'/DocMDP', pdf_bytes):
        result.add("signatures", "INFO",
                    "FIRMAS DIGITALES DETECTADAS",
                    "El PDF contiene una o más firmas digitales")

    # Restricciones de modificación
    if re.search(rb'/DocMDP', pdf_bytes):
        # P=1: sin cambios, P=2: solo comentarios, P=3: relleno de formularios
        p_match = re.search(rb'/P\s+(\d+)', pdf_bytes)
        if p_match:
            p_val = int(p_match.group(1))
            perms = {1: "Sin cambios permitidos", 2: "Solo anotaciones",
                      3: "Relleno de formularios permitido"}
            result.add("signatures", "INFO",
                        "Permisos de certificación DocMDP",
                        f"P={p_val}: {perms.get(p_val, 'Desconocido')}")

    # Firmas rotas (contenido modificado después de firmar)
    sig_ranges = re.findall(rb'/ByteRange\s*\[([^\]]+)\]', pdf_bytes)
    if sig_ranges:
        for sr in sig_ranges:
            parts = sr.split()
            if len(parts) >= 4:
                try:
                    b0, l0, b1, l1 = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                    covered = b0 + l0 + l1
                    if covered < len(pdf_bytes):
                        result.add("signatures", "HIGH",
                                    "Contenido fuera del rango de firma",
                                    f"Bytes cubiertos: {covered:,} / Tamaño total: {len(pdf_bytes):,} — "
                                    f"Hay {len(pdf_bytes) - covered:,} bytes NO firmados")
                except Exception:
                    pass

    # Nombre del firmante
    for field in [b'/CN=', b'/Name']:
        matches = re.findall(field + rb'\s*\(([^)]{2,200})\)', pdf_bytes)
        for m in matches:
            result.add("signatures", "INFO",
                        "Nombre en firma digital",
                        _decode_safe(m))

    ok("Módulo 13 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 14 · STEGANOGRAFÍA (HINTS)
# ══════════════════════════════════════════════════════════════════════════════

def analyze_steganography_hints(pdf_bytes: bytes, result: PDFForensicResult):
    section("MÓDULO 14 · Indicadores de steganografía en imágenes", "🕵️")

    # Detectar imágenes JPEG embebidas
    jpeg_starts = [m.start() for m in re.finditer(rb'\xff\xd8\xff', pdf_bytes)]
    if jpeg_starts:
        info(f"Imágenes JPEG detectadas: {len(jpeg_starts)}")
        result.metadata["jpeg_images"] = len(jpeg_starts)

        for pos in jpeg_starts:
            end = pdf_bytes.find(b'\xff\xd9', pos)
            if end == -1:
                continue
            jpeg_data = pdf_bytes[pos:end + 2]

            # JFIF / EXIF
            if b'Exif' in jpeg_data:
                result.add("steganography", "INFO",
                            "EXIF en imagen JPEG embebida",
                            f"Imagen en offset {pos:,} contiene metadata EXIF")

            # Comentarios JPEG (potencial canal oculto)
            comments = re.findall(b'\xff\xfe(.{2})(.*?)(?=\xff)', jpeg_data)
            for length_bytes, comment in comments:
                length = struct.unpack('>H', length_bytes)[0] - 2
                if comment and length > 0:
                    comment_text = _decode_safe(comment)
                    if comment_text.strip():
                        result.add("steganography", "MED",
                                    "Comentario en imagen JPEG",
                                    comment_text)

            # Tamaño anómalo (imagen enorme puede ocultar datos)
            if len(jpeg_data) > 1_000_000:
                result.add("steganography", "MED",
                            "Imagen JPEG de gran tamaño",
                            f"Tamaño: {len(jpeg_data):,} bytes — puede ocultar datos")

    # Streams de imagen PNG
    png_starts = [m.start() for m in re.finditer(rb'\x89PNG\r\n\x1a\n', pdf_bytes)]
    if png_starts:
        info(f"Imágenes PNG detectadas: {len(png_starts)}")
        result.metadata["png_images"] = len(png_starts)
        for pos in png_starts:
            # Buscar chunk tEXt (texto en PNG)
            chunk_start = pos + 8
            while chunk_start < len(pdf_bytes) - 12:
                length = struct.unpack('>I', pdf_bytes[chunk_start:chunk_start+4])[0]
                chunk_type = pdf_bytes[chunk_start+4:chunk_start+8]
                if chunk_type in (b'tEXt', b'iTXt', b'zTXt'):
                    chunk_data = pdf_bytes[chunk_start+8:chunk_start+8+min(length,500)]
                    result.add("steganography", "MED",
                                "Texto en chunk PNG",
                                _decode_safe(chunk_data))
                if chunk_type == b'IEND' or length > 10_000_000:
                    break
                chunk_start += 12 + length

    ok("Módulo 14 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 15 · SCAN RAW DE BYTES
# ══════════════════════════════════════════════════════════════════════════════

def analyze_raw_bytes(pdf_bytes: bytes, result: PDFForensicResult):
    section("MÓDULO 15 · Scan raw del binario (URLs, emails, hashes, datos)", "🔍")

    # ── Decodificar strings UTF-16BE (FEFF BOM) — títulos, marcadores, etc. ──
    feff_decoded = []
    for m in re.finditer(rb'<FEFF([0-9A-Fa-f\s]+)>', pdf_bytes):
        hx = m.group(1).replace(b' ', b'').decode('ascii', errors='ignore')
        if len(hx) < 4:
            continue
        try:
            b = bytes.fromhex(hx)
            t = b.decode('utf-16-be', errors='replace').strip()
            t = ''.join(c for c in t if c.isprintable() and c not in '\x00\ufffd')
            if t and len(t) >= 3:
                feff_decoded.append(t)
        except Exception:
            pass

    # Deduplicar preservando orden
    seen_feff = set()
    feff_unique = []
    for t in feff_decoded:
        if t not in seen_feff:
            seen_feff.add(t)
            feff_unique.append(t)

    if feff_unique:
        result.add("raw_scan", "INFO",
                   f"Títulos/marcadores UTF-16BE decodificados ({len(feff_unique)})",
                   " | ".join(feff_unique))
        for t in feff_unique[:20]:
            print(f"      {G}→ {t}{RST}")

    # ── Extraer strings ASCII legibles ────────────────────────────────────────
    strings_raw = re.findall(rb'[ -~]{6,}', pdf_bytes)

    pdf_structural = {
        b'endobj', b'endstream', b'stream', b'xref', b'trailer',
        b'startxref', b'%%EOF', b'/Length', b'/Filter', b'/Type',
        b'FlateDecode', b'/Page', b'/Font', b'/Resources', b'/Catalog',
        b'/Pages', b'/MediaBox', b'/ProcSet', b'/PDF', b'/Text',
    }

    interesting = []
    seen = set()
    for s in strings_raw:
        s = s.strip()
        if len(s) < 6 or s in seen:
            continue
        seen.add(s)
        if any(kw in s for kw in pdf_structural) and len(s) < 25:
            continue
        # Filtrar strings que son simplemente hex puro (son los bloques UTF-16BE sin decodificar)
        decoded_s = _decode_safe(s)
        if re.match(r'^[0-9A-Fa-f\s<>/]+$', decoded_s.strip()):
            continue
        if re.search(rb'[a-zA-Z]{3,}', s):
            interesting.append(decoded_s)

    # Clasificar hallazgos
    urls    = [s for s in interesting if re.search(r'https?://|ftp://|www\.', s, re.I)]
    emails  = [s for s in interesting if re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', s)]
    phones  = [s for s in interesting if re.search(r'\+?[\d\s\-]{9,15}', s) and
               re.search(r'\d{5,}', s) and not re.search(r'FEFF|[0-9A-Fa-f]{8,}', s)]
    ips     = [s for s in interesting if re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', s)]
    hashes_found = [s for s in interesting if re.search(r'^[0-9a-f]{32,}$', s.strip(), re.I)]
    dates   = [s for s in interesting if re.search(
                r'\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}|\d{4}[/\-]\d{2}[/\-]\d{2}', s)]
    base64_candidates = [s for s in interesting if
                          re.match(r'^[A-Za-z0-9+/]{40,}={0,2}$', s.strip())]

    for category, items, level, label in [
        (urls,              urls,              "HIGH",   "URLs"),
        (emails,            emails,            "HIGH",   "Emails"),
        (ips,               ips,               "MED",    "IPs"),
        (phones,            phones,            "LOW",    "Teléfonos"),
        (hashes_found,      hashes_found,      "MED",    "Posibles hashes"),
        (base64_candidates, base64_candidates, "MED",    "Strings Base64"),
        (dates,             dates,             "INFO",   "Fechas"),
    ]:
        if items:
            if label == "Strings Base64":
                decoded_b64 = []
                import base64 as _b64
                for item in items:
                    try:
                        raw_b64 = _b64.b64decode(item.strip() + '==', validate=False)
                        for enc in ('utf-8', 'latin-1', 'cp1252'):
                            try:
                                decoded_text = raw_b64.decode(enc, errors='strict')
                                if _is_readable(decoded_text, 0.60) and decoded_text.strip():
                                    decoded_b64.append(f"{item[:30]}… → [{decoded_text.strip()[:100]}]")
                                    break
                            except Exception:
                                pass
                        else:
                            decoded_b64.append(item)
                    except Exception:
                        decoded_b64.append(item)
                combined = " | ".join(decoded_b64)
            else:
                combined = " | ".join(items)
            result.add("raw_scan", level, f"{label} encontrados en binario", combined)
            for item in items:
                print(f"      {G}{item}{RST}")

    info(f"Total strings únicos extraídos: {len(interesting)}")
    ok("Módulo 15 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 16 · ANÁLISIS XREF STREAM (PDF 1.5+)
# ══════════════════════════════════════════════════════════════════════════════

def analyze_xref_stream(pdf_bytes: bytes, result: PDFForensicResult):
    section("MÓDULO 16 · Análisis de cross-reference stream (PDF 1.5+)", "📊")

    # Detectar xref streams
    xref_stream_pattern = re.compile(rb'(\d+)\s+\d+\s+obj\s*<<[^>]*?/Type\s*/XRef', re.DOTALL)
    matches = list(xref_stream_pattern.finditer(pdf_bytes))

    if not matches:
        info("No se detectaron xref streams (PDF usa tabla clásica)")
        return

    info(f"Xref streams encontrados: {len(matches)}")

    for m in matches:
        obj_id = int(m.group(1))
        obj_start = m.start()
        obj_end = pdf_bytes.find(b'endobj', obj_start)
        if obj_end == -1:
            continue

        obj_data = pdf_bytes[obj_start:obj_end]

        # Extraer tamaños de columnas W
        w_match = re.search(rb'/W\s*\[([^\]]+)\]', obj_data)
        index_match = re.search(rb'/Index\s*\[([^\]]+)\]', obj_data)
        size_match  = re.search(rb'/Size\s+(\d+)', obj_data)

        if size_match:
            result.add("xref_stream", "INFO",
                        f"Xref stream en objeto #{obj_id}",
                        f"Tamaño declarado: {size_match.group(1).decode()} objetos")

        if w_match:
            info(f"  Columnas W: {w_match.group(1).decode()}")

        # Intentar descomprimir el stream del xref
        stream_m = re.search(rb'stream\r?\n(.*?)endstream', obj_data, re.DOTALL)
        if stream_m:
            compressed = stream_m.group(1)
            dec = _try_decompress(compressed)
            if dec and w_match:
                # Parsear entradas del xref para encontrar free entries
                try:
                    w_vals = [int(x) for x in w_match.group(1).split()]
                    if len(w_vals) == 3 and all(w <= 8 for w in w_vals):
                        entry_size = sum(w_vals)
                        if entry_size > 0:
                            free_count = 0
                            for j in range(0, len(dec) - entry_size + 1, entry_size):
                                entry = dec[j:j + entry_size]
                                if len(entry) < entry_size:
                                    break
                                field0 = int.from_bytes(entry[:w_vals[0]], 'big') if w_vals[0] > 0 else 1
                                if field0 == 0:  # Type 0 = free
                                    free_count += 1
                            if free_count > 0:
                                result.add("xref_stream", "HIGH",
                                            f"Objeto #{obj_id}: Entradas free en xref stream",
                                            f"{free_count} objetos eliminados/libres detectados")
                except Exception:
                    pass

    ok("Módulo 16 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 17 · EXTRACCIÓN CON PDFMINER
# ══════════════════════════════════════════════════════════════════════════════

def analyze_with_pdfminer(pdf_path: str, result: PDFForensicResult):
    section("MÓDULO 17 · pdfminer: extracción con posición y análisis de layout", "📐")

    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextBox, LTChar, LTFigure
    except ImportError:
        info("pdfminer no disponible")
        return

    suspicious = []

    try:
        for page_num, layout in enumerate(extract_pages(pdf_path)):
            page_w = layout.width
            page_h = layout.height

            for elem in layout:
                if isinstance(elem, LTTextBox):
                    x0, y0, x1, y1 = elem.bbox
                    text = elem.get_text().strip()

                    # Fuera de la página
                    if x0 < -30 or y0 < -30 or x1 > page_w + 30 or y1 > page_h + 30:
                        suspicious.append(
                            f"Pág {page_num+1}: TEXTO FUERA DE PÁGINA "
                            f"en ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}): «{text}»"
                        )
                        result.add("pdfminer", "HIGH",
                                    f"Pág {page_num+1}: Texto fuera de página",
                                    f"Coords ({x0:.0f},{y0:.0f}) / página {page_w:.0f}x{page_h:.0f}: «{text}»",
                                    page=page_num + 1)

                    # Caracteres con tamaño ~0
                    for line in elem:
                        if hasattr(line, '__iter__'):
                            for char in line:
                                if (isinstance(char, LTChar) and
                                        hasattr(char, 'size') and char.size < 0.5 and
                                        hasattr(char, '_text') and char._text.strip()):
                                    result.add("pdfminer", "HIGH",
                                                f"Pág {page_num+1}: Carácter con tamaño ~0",
                                                f"Char «{char._text}» size={char.size:.3f} "
                                                f"en ({char.x0:.0f},{char.y0:.0f})",
                                                page=page_num + 1)

    except Exception as e:
        result.add_error(f"pdfminer: {e}")

    if suspicious:
        hit("Elementos en posiciones anómalas", f"{len(suspicious)}", "HIGH")

    ok("Módulo 17 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 18 · EXTRACCIÓN ESTÁNDAR PYPDF
# ══════════════════════════════════════════════════════════════════════════════

def extract_standard_text(pdf_path: str, result: PDFForensicResult) -> List[Tuple[int, str]]:
    section("MÓDULO 18 · Extracción estándar de texto (pypdf)", "📄")

    all_text = []
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        total_chars = 0

        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
                if text.strip():
                    all_text.append((i + 1, text))
                    total_chars += len(text)
            except Exception as e:
                result.add_error(f"pypdf page {i+1}: {e}")

        info(f"Texto extraído: {total_chars:,} caracteres en {len(all_text)} páginas")
        result.metadata["extractable_text_chars"] = total_chars
        result.metadata["pages_with_text"] = len(all_text)

    except ImportError:
        info("pypdf no disponible")
    except Exception as e:
        result.add_error(f"pypdf: {e}")

    ok("Módulo 18 completado")
    return all_text


# ══════════════════════════════════════════════════════════════════════════════
#  GENERACIÓN DEL INFORME HTML
# ══════════════════════════════════════════════════════════════════════════════

LEVEL_COLORS = {
    "CRITICAL": ("#ff1744", "#fff"),
    "HIGH":     ("#ff6b35", "#fff"),
    "MED":      ("#ffd600", "#111"),
    "LOW":      ("#00e676", "#111"),
    "INFO":     ("#40c4ff", "#111"),
}

LEVEL_ORDER = {"CRITICAL": 0, "HIGH": 1, "MED": 2, "LOW": 3, "INFO": 4}


def _badge(level: str) -> str:
    bg, fg = LEVEL_COLORS.get(level, ("#888", "#fff"))
    return (f'<span style="background:{bg};color:{fg};'
            f'padding:2px 8px;border-radius:12px;font-size:11px;'
            f'font-weight:bold;letter-spacing:0.5px">{html_module.escape(level)}</span>')


def _risk_color(label: str) -> str:
    return {"CRÍTICO": "#ff1744", "ALTO": "#ff6b35",
            "MEDIO": "#ffd600", "BAJO": "#00e676", "LIMPIO": "#4caf50"}.get(label, "#888")


def generate_html_report(result: PDFForensicResult, output_dir: str) -> str:
    """Genera el informe HTML forense para un PDF."""

    # Ordenar hallazgos por severidad
    sorted_findings = sorted(result.findings, key=lambda f: LEVEL_ORDER.get(f.level, 99))

    # Contar por módulo
    modules_summary: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for f in result.findings:
        modules_summary[f.module][f.level] += 1

    # Construir filas de la tabla
    rows_html = ""
    for i, f in enumerate(sorted_findings, 1):
        page_label = f"Pág {f.page}" if f.page else "—"
        detail_esc = html_module.escape(f.detail if f.detail else "")
        title_esc  = html_module.escape(f.title)
        module_esc = html_module.escape(f.module.replace("_", " ").upper())
        rows_html += f"""
        <tr>
          <td style="text-align:center;color:#888;font-size:12px">{i}</td>
          <td>{_badge(f.level)}</td>
          <td style="font-size:11px;color:#aaa">{module_esc}</td>
          <td style="font-weight:600">{title_esc}</td>
          <td style="font-size:12px;color:#ccc;word-break:break-all">{detail_esc}</td>
          <td style="font-size:11px;color:#888">{page_label}</td>
        </tr>"""

    # Módulos resumen
    mod_cards = ""
    for mod, counts in sorted(modules_summary.items()):
        total = sum(counts.values())
        worst = min(counts.keys(), key=lambda l: LEVEL_ORDER.get(l, 99))
        bg, fg = LEVEL_COLORS.get(worst, ("#555", "#fff"))
        mod_cards += f"""
        <div style="background:#1e1e2e;border-left:4px solid {bg};
                     padding:10px 16px;border-radius:6px;min-width:160px">
          <div style="font-size:10px;color:#888;text-transform:uppercase;
                       letter-spacing:1px">{html_module.escape(mod.replace('_',' '))}</div>
          <div style="font-size:22px;font-weight:800;color:{bg}">{total}</div>
          <div style="font-size:11px;color:{fg};background:{bg};
                       display:inline-block;padding:1px 6px;border-radius:10px">{worst}</div>
        </div>"""

    # Gráfico de severidad (barras CSS puras)
    level_counts = result.summary_by_level()
    max_count = max(level_counts.values(), default=1)
    bars_html = ""
    for level in ["CRITICAL", "HIGH", "MED", "LOW", "INFO"]:
        cnt = level_counts.get(level, 0)
        if cnt == 0:
            continue
        pct = int((cnt / max_count) * 200)
        bg, _ = LEVEL_COLORS[level]
        bars_html += f"""
        <div style="display:flex;align-items:center;gap:10px;margin:4px 0">
          <div style="width:70px;font-size:11px;color:#aaa;text-align:right">{level}</div>
          <div style="background:{bg};height:18px;width:{pct}px;
                       border-radius:3px;transition:width 0.5s"></div>
          <div style="font-size:13px;font-weight:700;color:{bg}">{cnt}</div>
        </div>"""

    # Metadatos
    meta_rows = ""
    flat_meta = {k: str(v) for k, v in result.metadata.items()
                  if not isinstance(v, dict) and v}
    for k, v in flat_meta.items():
        meta_rows += f"""
        <tr>
          <td style="color:#888;font-size:12px;padding:4px 8px;
                      white-space:nowrap">{html_module.escape(k)}</td>
          <td style="font-size:12px;padding:4px 8px;word-break:break-all">
            {html_module.escape(v)}</td>
        </tr>"""

    risk_color = _risk_color(result.risk_label)
    duration   = ""
    if result.finished_at:
        secs = (result.finished_at - result.started_at).total_seconds()
        duration = f"{secs:.1f}s"

    # Construir JSON de hallazgos (para el sello)
    findings_json = json.dumps(
        [f.to_dict() for f in result.findings],
        ensure_ascii=False, indent=2
    )
    report_hash = _sha512_str(findings_json + result.file_sha512)

    generated_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Informe Forense · {html_module.escape(result.pdf_name)}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0d0d1a;
      color: #e0e0e0;
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      font-size: 14px;
      line-height: 1.5;
    }}
    .header {{
      background: linear-gradient(135deg, #1a0035 0%, #0d0d3a 50%, #001a35 100%);
      border-bottom: 2px solid #7c4dff;
      padding: 32px 40px;
    }}
    .header h1 {{
      font-size: 28px;
      font-weight: 800;
      color: #fff;
      letter-spacing: -0.5px;
    }}
    .header h1 span {{ color: #7c4dff; }}
    .header .subtitle {{
      color: #888;
      font-size: 13px;
      margin-top: 4px;
    }}
    .container {{ max-width: 1400px; margin: 0 auto; padding: 32px 40px; }}
    .card {{
      background: #12122a;
      border: 1px solid #222244;
      border-radius: 10px;
      padding: 24px;
      margin-bottom: 24px;
    }}
    .card h2 {{
      font-size: 15px;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: #7c4dff;
      margin-bottom: 16px;
      border-bottom: 1px solid #222244;
      padding-bottom: 10px;
    }}
    .risk-badge {{
      display: inline-block;
      font-size: 28px;
      font-weight: 900;
      color: {risk_color};
      background: {risk_color}22;
      border: 2px solid {risk_color};
      padding: 8px 24px;
      border-radius: 8px;
      letter-spacing: 2px;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
      gap: 12px;
    }}
    .meta-item {{
      background: #0d0d1a;
      border: 1px solid #222244;
      border-radius: 6px;
      padding: 10px 14px;
    }}
    .meta-label {{ font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
    .meta-value {{ font-size: 13px; color: #e0e0e0; font-weight: 600; word-break: break-all; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{
      background: #1a1a3a;
      padding: 10px 12px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: #7c4dff;
      text-align: left;
      border-bottom: 2px solid #7c4dff44;
    }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #1a1a2e; vertical-align: top; }}
    tr:hover td {{ background: #15153a; }}
    .hash-block {{
      background: #080814;
      border: 1px solid #7c4dff44;
      border-radius: 6px;
      padding: 16px;
      font-family: 'Courier New', monospace;
      font-size: 11px;
      color: #7c4dff;
      word-break: break-all;
      line-height: 1.8;
    }}
    .hash-label {{ color: #555; font-size: 10px; text-transform: uppercase; }}
    .modules-grid {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .total-chip {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: #1a1a3a;
      border: 1px solid #333;
      border-radius: 20px;
      padding: 4px 12px;
      font-size: 12px;
    }}
    .footer {{
      border-top: 1px solid #222244;
      padding: 24px 40px;
      text-align: center;
      color: #444;
      font-size: 11px;
    }}
  </style>
</head>
<body>

<div class="header">
  <h1><span>PDF</span> Forensics PRO · Informe Forense</h1>
  <div class="subtitle">
    {html_module.escape(TOOL_NAME)} v{VERSION} &nbsp;·&nbsp;
    Generado: {generated_at} &nbsp;·&nbsp;
    Duración: {duration}
  </div>
</div>

<div class="container">

  <!-- ── RESUMEN EJECUTIVO ──────────────────────────────────── -->
  <div class="card">
    <h2>📋 Resumen Ejecutivo</h2>
    <div style="display:flex;align-items:flex-start;gap:40px;flex-wrap:wrap">
      <div>
        <div style="font-size:12px;color:#666;margin-bottom:6px">NIVEL DE RIESGO</div>
        <div class="risk-badge">{result.risk_label}</div>
        <div style="font-size:12px;color:#666;margin-top:6px">
          Score: {result.risk_score}/100
        </div>
      </div>
      <div>
        <div style="font-size:12px;color:#666;margin-bottom:8px">DISTRIBUCIÓN DE HALLAZGOS</div>
        {bars_html}
      </div>
      <div>
        <div style="font-size:12px;color:#666;margin-bottom:8px">ESTADÍSTICAS</div>
        <div class="total-chip">
          <span style="color:#7c4dff;font-weight:700">{len(result.findings)}</span>
          <span>hallazgos totales</span>
        </div>
        <br><br>
        <div class="total-chip" style="margin-top:6px">
          <span style="color:#ff6b35;font-weight:700">
            {sum(1 for f in result.findings if f.level in ('CRITICAL','HIGH'))}
          </span>
          <span>críticos/altos</span>
        </div>
      </div>
    </div>
  </div>

  <!-- ── IDENTIFICACIÓN DEL FICHERO ────────────────────────── -->
  <div class="card">
    <h2>📁 Identificación del Archivo</h2>
    <div class="meta-grid">
      <div class="meta-item">
        <div class="meta-label">Nombre</div>
        <div class="meta-value">{html_module.escape(result.pdf_name)}</div>
      </div>
      <div class="meta-item">
        <div class="meta-label">Ruta completa</div>
        <div class="meta-value">{html_module.escape(result.pdf_path)}</div>
      </div>
      <div class="meta-item">
        <div class="meta-label">Tamaño</div>
        <div class="meta-value">{result.file_size:,} bytes</div>
      </div>
      <div class="meta-item">
        <div class="meta-label">MD5</div>
        <div class="meta-value" style="font-family:monospace;font-size:11px">
          {result.file_md5}
        </div>
      </div>
      <div class="meta-item" style="grid-column:span 2">
        <div class="meta-label">SHA-512 (archivo)</div>
        <div class="meta-value" style="font-family:monospace;font-size:10px;
                                        word-break:break-all">
          {result.file_sha512}
        </div>
      </div>
    </div>
  </div>

  <!-- ── MÓDULOS ACTIVADOS ──────────────────────────────────── -->
  <div class="card">
    <h2>⚙️ Hallazgos por Módulo</h2>
    <div class="modules-grid">
      {mod_cards}
    </div>
  </div>

  <!-- ── METADATOS ──────────────────────────────────────────── -->
  {'<div class="card"><h2>📋 Metadatos del Documento</h2><table><tbody>' + meta_rows + '</tbody></table></div>' if meta_rows else ''}

  <!-- ── TABLA DE HALLAZGOS ─────────────────────────────────── -->
  <div class="card">
    <h2>🔎 Tabla Completa de Hallazgos ({len(sorted_findings)} registros)</h2>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Nivel</th>
            <th>Módulo</th>
            <th>Título</th>
            <th>Detalle</th>
            <th>Página</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>

  <!-- ── SELLO DE INTEGRIDAD SHA-512 ───────────────────────── -->
  <div class="card">
    <h2>🔐 Sello de Integridad Forense (SHA-512)</h2>
    <p style="color:#888;font-size:12px;margin-bottom:12px">
      Este hash certifica la integridad del informe. Cualquier modificación
      posterior invalidará el sello. Calculado sobre el conjunto de hallazgos
      + hash SHA-512 del archivo analizado.
    </p>
    <div class="hash-block">
      <div class="hash-label">SHA-512 DEL INFORME</div>
      {report_hash}<br><br>
      <div class="hash-label">SHA-512 DEL ARCHIVO PDF</div>
      {result.file_sha512}<br><br>
      <div class="hash-label">FECHA Y HORA DE GENERACIÓN (UTC)</div>
      {generated_at}<br><br>
      <div class="hash-label">HERRAMIENTA</div>
      {TOOL_NAME} v{VERSION}
    </div>
  </div>

  {'<div class="card" style="border-color:#ff444433"><h2 style="color:#ff4444">⚠ Errores durante el análisis</h2>' + "".join(f"<div style='color:#ff8888;font-size:12px;padding:3px 0'>• {html_module.escape(e)}</div>" for e in result.errors) + "</div>" if result.errors else ""}

</div>

<div class="footer">
  {TOOL_NAME} v{VERSION} · Informe forense generado automáticamente ·
  Hash de integridad SHA-512 incluido · {generated_at}
</div>

</body>
</html>"""

    # Guardar
    safe_name = re.sub(r'[^\w\-.]', '_', result.pdf_name)
    report_filename = f"{safe_name}_forensic_report.html"
    report_path = Path(output_dir) / report_filename
    report_path.write_text(html, encoding="utf-8")

    # Guardar también el JSON de hallazgos
    json_path = Path(output_dir) / f"{safe_name}_findings.json"
    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    ok(f"Informe HTML: {report_path}")
    ok(f"JSON hallazgos: {json_path}")

    return str(report_path), report_hash


# ══════════════════════════════════════════════════════════════════════════════
#  INFORME CONSOLIDADO MULTI-PDF (BATCH)
# ══════════════════════════════════════════════════════════════════════════════

def generate_batch_report(results: List[PDFForensicResult],
                           output_dir: str,
                           report_hashes: Dict[str, str]) -> str:
    """Genera el informe maestro de análisis batch."""

    total_findings = sum(len(r.findings) for r in results)
    total_critical = sum(
        sum(1 for f in r.findings if f.level in ("CRITICAL", "HIGH"))
        for r in results
    )

    # Tabla de PDFs
    pdf_rows = ""
    for r in sorted(results, key=lambda x: -x.risk_score):
        rc = _risk_color(r.risk_label)
        safe_name = re.sub(r'[^\w\-.]', '_', r.pdf_name)
        report_link = f"{safe_name}_forensic_report.html"
        pdf_rows += f"""
        <tr>
          <td><a href="{html_module.escape(report_link)}"
                 style="color:#7c4dff">{html_module.escape(r.pdf_name)}</a></td>
          <td style="text-align:center;font-weight:700;color:{rc}">{r.risk_label}</td>
          <td style="text-align:center;color:{rc}">{r.risk_score}</td>
          <td style="text-align:center">{len(r.findings)}</td>
          <td style="text-align:center;color:#ff4444">
            {sum(1 for f in r.findings if f.level in ('CRITICAL','HIGH'))}
          </td>
          <td style="text-align:center;color:#888;font-size:11px">
            {r.file_size:,}
          </td>
          <td style="font-family:monospace;font-size:10px;color:#555;
                      word-break:break-all">{r.file_sha512}…</td>
        </tr>"""

    # Hash del informe batch
    combined_hashes = "|".join(sorted(report_hashes.values()))
    batch_hash = _sha512_str(combined_hashes)
    generated_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # Resumen de todos los hallazgos
    all_findings_by_level: Dict[str, int] = defaultdict(int)
    for r in results:
        for f in r.findings:
            all_findings_by_level[f.level] += 1

    level_summary = ""
    for level in ["CRITICAL", "HIGH", "MED", "LOW", "INFO"]:
        cnt = all_findings_by_level.get(level, 0)
        bg, fg = LEVEL_COLORS.get(level, ("#888", "#fff"))
        level_summary += f"""
        <div style="background:{bg}22;border:1px solid {bg};border-radius:8px;
                     padding:12px 20px;text-align:center;min-width:100px">
          <div style="font-size:28px;font-weight:900;color:{bg}">{cnt}</div>
          <div style="font-size:11px;color:{bg};font-weight:700">{level}</div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Informe Batch · PDF Forensics PRO</title>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{
      background:#0d0d1a; color:#e0e0e0;
      font-family:'Segoe UI',system-ui,sans-serif; font-size:14px;
    }}
    .header {{
      background:linear-gradient(135deg,#1a0035 0%,#0d0d3a 50%,#001a35 100%);
      border-bottom:3px solid #7c4dff; padding:32px 40px;
    }}
    .header h1 {{ font-size:30px; font-weight:900; color:#fff; }}
    .header h1 span {{ color:#7c4dff; }}
    .container {{ max-width:1400px; margin:0 auto; padding:32px 40px; }}
    .card {{
      background:#12122a; border:1px solid #222244;
      border-radius:10px; padding:24px; margin-bottom:24px;
    }}
    .card h2 {{
      font-size:15px; text-transform:uppercase; letter-spacing:1px;
      color:#7c4dff; margin-bottom:16px;
      border-bottom:1px solid #222244; padding-bottom:10px;
    }}
    table {{ width:100%; border-collapse:collapse; }}
    th {{
      background:#1a1a3a; padding:10px 12px;
      font-size:11px; text-transform:uppercase;
      letter-spacing:0.8px; color:#7c4dff; text-align:left;
    }}
    td {{ padding:10px 12px; border-bottom:1px solid #1a1a2e; vertical-align:top; }}
    tr:hover td {{ background:#15153a; }}
    .hash-block {{
      background:#080814; border:1px solid #7c4dff44;
      border-radius:6px; padding:16px;
      font-family:'Courier New',monospace; font-size:11px;
      color:#7c4dff; word-break:break-all; line-height:1.8;
    }}
    .hash-label {{ color:#555; font-size:10px; text-transform:uppercase; }}
    .footer {{
      border-top:1px solid #222244; padding:24px 40px;
      text-align:center; color:#444; font-size:11px;
    }}
  </style>
</head>
<body>

<div class="header">
  <h1><span>PDF</span> Forensics PRO · Informe Batch Consolidado</h1>
  <div style="color:#888;font-size:13px;margin-top:6px">
    {len(results)} documentos analizados · {generated_at}
  </div>
</div>

<div class="container">

  <div class="card">
    <h2>📊 Resumen Global</h2>
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">
      {level_summary}
    </div>
    <div style="color:#888;font-size:13px">
      <strong style="color:#fff">{len(results)}</strong> PDFs analizados &nbsp;·&nbsp;
      <strong style="color:#ff6b35">{total_findings}</strong> hallazgos totales &nbsp;·&nbsp;
      <strong style="color:#ff1744">{total_critical}</strong> críticos/altos
    </div>
  </div>

  <div class="card">
    <h2>📄 Tabla de Documentos Analizados</h2>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>Archivo</th>
            <th>Riesgo</th>
            <th>Score</th>
            <th>Hallazgos</th>
            <th>Críticos/Altos</th>
            <th>Tamaño (bytes)</th>
            <th>SHA-512 (parcial)</th>
          </tr>
        </thead>
        <tbody>{pdf_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>🔐 Sello de Integridad Maestro (SHA-512)</h2>
    <p style="color:#888;font-size:12px;margin-bottom:12px">
      Hash calculado sobre la combinación de todos los hashes individuales de
      informe. Garantiza que ningún informe individual fue alterado.
    </p>
    <div class="hash-block">
      <div class="hash-label">SHA-512 MAESTRO DEL BATCH</div>
      {batch_hash}<br><br>
      <div class="hash-label">HASHES INDIVIDUALES INCLUIDOS</div>
      {"<br>".join(f"· {html_module.escape(name)}: {h}…" for name, h in sorted(report_hashes.items()))}<br><br>
      <div class="hash-label">FECHA Y HORA DE GENERACIÓN (UTC)</div>
      {generated_at}<br><br>
      <div class="hash-label">HERRAMIENTA</div>
      {TOOL_NAME} v{VERSION}
    </div>
  </div>

</div>

<div class="footer">
  {TOOL_NAME} v{VERSION} · Informe batch consolidado · Sello SHA-512 incluido · {generated_at}
</div>

</body>
</html>"""

    batch_path = Path(output_dir) / "BATCH_FORENSIC_REPORT.html"
    batch_path.write_text(html, encoding="utf-8")

    # JSON maestro
    batch_json = {
        "tool":         TOOL_NAME,
        "version":      VERSION,
        "generated_at": generated_at,
        "batch_sha512": batch_hash,
        "total_pdfs":   len(results),
        "total_findings": total_findings,
        "documents":    [r.to_dict() for r in results],
    }
    json_batch_path = Path(output_dir) / "BATCH_FINDINGS.json"
    json_batch_path.write_text(
        json.dumps(batch_json, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    ok(f"Informe batch: {batch_path}")
    ok(f"JSON batch:    {json_batch_path}")
    return str(batch_path), batch_hash


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO 19 · DECODIFICACIÓN UNIVERSAL DE STRINGS RESIDUALES
#  Pasa por TODO el binario del PDF buscando strings que otros módulos
#  no hayan podido convertir a texto legible, y los decodifica con el
#  motor universal (hex, base64, literal, UTF-16BE, Latin-1…)
# ══════════════════════════════════════════════════════════════════════════════

def analyze_undecoded_strings(pdf_bytes: bytes, result: PDFForensicResult,
                               tounicode: Dict[str, str]):
    section("MÓDULO 19 · Decodificación universal de strings residuales", "🔓")

    import base64 as _b64

    decoded_items = []
    seen = set()

    # ── 1. Strings hex en todos los streams ──────────────────────────────────
    stream_pat = re.compile(rb'stream\r?\n(.*?)endstream', re.DOTALL)
    for i, m in enumerate(stream_pat.finditer(pdf_bytes)):
        raw = m.group(1)
        dec = _try_decompress(raw)
        text = _decode_safe(dec if dec else raw)

        # Hex strings <...> que aún no han sido decodificados
        for hm in re.finditer(r'<([0-9A-Fa-f\s]{4,200})>', text):
            hx = hm.group(1).replace(' ', '')
            if hx in seen or len(hx) < 4:
                continue
            seen.add(hx)
            decoded = decode_hex_universal(hx, tounicode)
            if decoded and decoded.strip() and _is_readable(decoded, 0.55):
                # Filtrar resultados que son solo operadores PDF
                if not re.fullmatch(r'[A-Za-z\s/\-]{0,8}', decoded.strip()):
                    decoded_items.append(("hex_stream", decoded.strip(), f"stream#{i}"))

        # Strings literales (...) con escapes o codificaciones especiales
        for lm in re.finditer(r'\(([^)]{4,300})\)', text):
            raw_lit = lm.group(1)
            if raw_lit in seen:
                continue
            seen.add(raw_lit)
            decoded = decode_pdf_string_literal(raw_lit)
            # Solo reportar si el resultado difiere significativamente del original
            if (decoded and decoded.strip() and
                    _is_readable(decoded, 0.60) and
                    decoded.strip() != raw_lit.strip()):
                decoded_items.append(("literal_stream", decoded.strip(), f"stream#{i}"))

    # ── 2. Strings hex fuera de streams (en el cuerpo del PDF) ───────────────
    outside_streams = re.sub(rb'stream\r?\n.*?endstream', b'', pdf_bytes, flags=re.DOTALL)
    for hm in re.finditer(rb'<([0-9A-Fa-f]{4,200})>', outside_streams):
        hx = hm.group(1).decode('ascii')
        if hx in seen:
            continue
        seen.add(hx)
        decoded = decode_hex_universal(hx, tounicode)
        if decoded and decoded.strip() and _is_readable(decoded, 0.60):
            if not re.fullmatch(r'[A-Za-z\s/\-]{0,8}', decoded.strip()):
                decoded_items.append(("hex_body", decoded.strip(), "body"))

    # ── 3. Strings base64 en todo el PDF ─────────────────────────────────────
    b64_pat = re.compile(r'[A-Za-z0-9+/]{40,}={0,2}')
    text_full = _decode_safe(pdf_bytes)
    for bm in b64_pat.finditer(text_full):
        candidate = bm.group(0)
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            raw_b64 = _b64.b64decode(candidate + '==', validate=False)
            for enc in ('utf-8', 'latin-1'):
                try:
                    decoded = raw_b64.decode(enc, errors='strict')
                    if _is_readable(decoded, 0.65) and decoded.strip():
                        decoded_items.append(("base64", decoded.strip()[:200], "body"))
                        break
                except Exception:
                    pass
        except Exception:
            pass

    # ── Reportar hallazgos únicos ─────────────────────────────────────────────
    if decoded_items:
        # Deduplicar por contenido decodificado
        seen_decoded = set()
        unique = []
        for kind, text, location in decoded_items:
            key = text[:80]
            if key not in seen_decoded:
                seen_decoded.add(key)
                unique.append((kind, text, location))

        info(f"Strings adicionales decodificados: {len(unique)}")
        for kind, text, location in unique:
            label = f"[{kind}@{location}]"
            print(f"  {C}{label:30}{RST} {W}{text[:120]}{RST}")

        # Agrupar en hallazgo consolidado si hay muchos
        if len(unique) > 0:
            combined = " | ".join(f"{t}" for _, t, _ in unique[:50])
            result.add("universal_decode", "MED" if len(unique) > 5 else "LOW",
                        f"Strings decodificados por motor universal ({len(unique)} únicos)",
                        combined)
    else:
        info("No se encontraron strings adicionales decodificables")

    ok("Módulo 19 completado")


# ══════════════════════════════════════════════════════════════════════════════
#  ANÁLISIS DE UN SOLO PDF
# ══════════════════════════════════════════════════════════════════════════════

def analyze_pdf(pdf_path: str, output_dir: str, verbose: bool = False) -> PDFForensicResult:
    """Ejecuta todos los módulos de análisis sobre un PDF."""

    print(f"\n{M}{BOLD}{'═'*65}{RST}")
    print(f"{M}{BOLD}  ANALIZANDO: {W}{pdf_path}{RST}")
    print(f"{M}{BOLD}{'═'*65}{RST}")

    result = PDFForensicResult(pdf_path)
    print(f"  {DIM}SHA-512: {result.file_sha512}…{RST}")
    print(f"  {DIM}MD5:     {result.file_md5}{RST}")
    print(f"  {DIM}Tamaño:  {result.file_size:,} bytes{RST}")

    # Leer bytes crudos
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    # Verificar que es un PDF real
    if not pdf_bytes.startswith(b'%PDF-'):
        result.add("structure", "CRITICAL",
                    "CABECERA PDF INVÁLIDA",
                    f"El archivo no comienza con %PDF-. Header: {pdf_bytes[:20]!r}")

    # Detectar versión del PDF
    version_match = re.match(rb'%PDF-(\d+\.\d+)', pdf_bytes)
    if version_match:
        result.metadata["pdf_version"] = version_match.group(1).decode()
        info(f"Versión PDF: {result.metadata['pdf_version']}")

    # ── PASO PREVIO CRÍTICO: Extraer todos los mapas ToUnicode ───────────────
    section("PRE-ANÁLISIS · Extracción de mapas ToUnicode (decodificador)", "🗺️")
    try:
        font_maps, combined_tounicode = extract_all_tounicode_maps(pdf_bytes)
        result.metadata["tounicode_maps"] = len(font_maps)
        if font_maps:
            total_entries = sum(len(m) for m in font_maps.values())
            ok(f"Mapas ToUnicode extraídos: {len(font_maps)} fuentes, "
               f"{total_entries} entradas de glifos → Unicode")
            info("El texto codificado como <01><02>... ahora será traducido a texto real")
        else:
            warn("No se encontraron mapas ToUnicode — el texto hex puede no ser decodificable")
            combined_tounicode = {}
    except Exception as e:
        result.add_error(f"ToUnicode extraction: {e}")
        combined_tounicode = {}
        font_maps = {}

    # ── Ejecutar módulos ──────────────────────────────────────────────────────
    try: analyze_metadata(pdf_path, pdf_bytes, result)
    except Exception as e: result.add_error(f"M01: {traceback.format_exc()}")

    try: analyze_incremental_updates(pdf_bytes, result)
    except Exception as e: result.add_error(f"M02: {traceback.format_exc()}")

    try: analyze_deleted_objects(pdf_bytes, result)
    except Exception as e: result.add_error(f"M03: {traceback.format_exc()}")

    try: text_ops, suspicious = analyze_streams(pdf_bytes, result, combined_tounicode)
    except Exception as e:
        result.add_error(f"M04: {traceback.format_exc()}")
        text_ops, suspicious = [], []

    try: analyze_invisible_text(pdf_bytes, result, combined_tounicode)
    except Exception as e: result.add_error(f"M05: {traceback.format_exc()}")

    try: analyze_redactions(pdf_bytes, result)
    except Exception as e: result.add_error(f"M06: {traceback.format_exc()}")

    try: analyze_ocg_layers(pdf_path, result)
    except Exception as e: result.add_error(f"M07: {traceback.format_exc()}")

    try: analyze_annotations(pdf_path, result)
    except Exception as e: result.add_error(f"M08: {traceback.format_exc()}")

    try: analyze_javascript(pdf_bytes, result)
    except Exception as e: result.add_error(f"M09: {traceback.format_exc()}")

    try: analyze_embedded_files(pdf_bytes, pdf_path, result)
    except Exception as e: result.add_error(f"M10: {traceback.format_exc()}")

    try: analyze_fonts(pdf_bytes, result)
    except Exception as e: result.add_error(f"M11: {traceback.format_exc()}")

    try: analyze_form_xobjects(pdf_path, result)
    except Exception as e: result.add_error(f"M12: {traceback.format_exc()}")

    try: analyze_signatures(pdf_bytes, result)
    except Exception as e: result.add_error(f"M13: {traceback.format_exc()}")

    try: analyze_steganography_hints(pdf_bytes, result)
    except Exception as e: result.add_error(f"M14: {traceback.format_exc()}")

    try: analyze_raw_bytes(pdf_bytes, result)
    except Exception as e: result.add_error(f"M15: {traceback.format_exc()}")

    try: analyze_xref_stream(pdf_bytes, result)
    except Exception as e: result.add_error(f"M16: {traceback.format_exc()}")

    try: analyze_with_pdfminer(pdf_path, result)
    except Exception as e: result.add_error(f"M17: {traceback.format_exc()}")

    try: extract_standard_text(pdf_path, result)
    except Exception as e: result.add_error(f"M18: {traceback.format_exc()}")

    try: analyze_undecoded_strings(pdf_bytes, result, combined_tounicode)
    except Exception as e: result.add_error(f"M19: {traceback.format_exc()}")

    # Streams sospechosos
    if suspicious:
        section("Streams sospechosos adicionales", "⚠️")
        for idx, text in suspicious:
            # Intentar decodificar con ToUnicode primero
            decoded = decode_full_stream_text(text, combined_tounicode)
            if decoded:
                combined_text = " ".join(decoded)
                result.add("streams", "HIGH",
                            f"Texto decodificado de stream sospechoso #{idx}",
                            combined_text)
            else:
                # Motor universal: strings literales con decode completo
                txts_raw = re.findall(r'\(([^)]{3,})\)', text)
                txts = [decode_pdf_string_literal(s) for s in txts_raw]
                txts = [t for t in txts if t.strip()]
                # Motor universal: hex strings
                if not txts:
                    hex_matches = re.findall(r'<([0-9A-Fa-f\s]{4,})>', text)
                    for hx in hex_matches:
                        decoded_hx = decode_hex_universal(hx.replace(' ', ''), combined_tounicode)
                        if decoded_hx and decoded_hx.strip():
                            txts.append(decoded_hx)
                if txts:
                    result.add("streams", "HIGH",
                                f"Contenido de stream sospechoso #{idx}",
                                " | ".join(txts))

    # ── Mostrar texto extraído de operadores con decodificación ───────────────
    if text_ops:
        section("TEXTO EXTRAÍDO Y DECODIFICADO DE OPERADORES PDF", "📖")
        unique_texts = []
        seen_texts = set()
        for op_type, text_val, stream_idx in text_ops:
            clean = text_val.strip()
            if clean and clean not in seen_texts and len(clean) > 1:
                seen_texts.add(clean)
                unique_texts.append((op_type, clean, stream_idx))

        info(f"Strings únicos decodificados: {len(unique_texts)}")
        for op_type, t, sidx in unique_texts:
            label = f"[{op_type} s#{sidx}]"
            print(f"  {G}{label:15}{RST} {W}{t}{RST}")

    result.finish()

    # Resumen en consola
    section("RESUMEN FINAL", "📋")
    risk_c = _risk_color(result.risk_label)
    print(f"\n  Nivel de riesgo: {risk_c}{BOLD}{result.risk_label}{RST} "
          f"(score: {result.risk_score}/100)")
    print(f"  Hallazgos totales: {BOLD}{len(result.findings)}{RST}")

    by_level = result.summary_by_level()
    for level in ["CRITICAL", "HIGH", "MED", "LOW", "INFO"]:
        cnt = by_level.get(level, 0)
        if cnt:
            print(f"    {Y}{level}: {cnt}{RST}")

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description=f"{TOOL_NAME} v{VERSION} · Análisis forense de PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Ejemplos:
          python3 pdf_forensics_pro.py documento.pdf
          python3 pdf_forensics_pro.py /ruta/a/carpeta/
          python3 pdf_forensics_pro.py /ruta/ --output-dir /informes --verbose
        """)
    )
    parser.add_argument("target",
                         help="Archivo PDF o directorio con PDFs a analizar")
    parser.add_argument("--output-dir", "-o", default="/forensic_reports",
                         help="Directorio de salida (default: /forensic_reports)")
    parser.add_argument("--verbose", "-v", action="store_true",
                         help="Modo verbose (más detalles)")
    parser.add_argument("--version", action="version",
                         version=f"{TOOL_NAME} {VERSION}")
    args = parser.parse_args()

    banner()

    target = Path(args.target)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Recopilar PDFs a analizar ─────────────────────────────────────────────
    if target.is_file():
        if target.suffix.lower() != '.pdf':
            print(f"{R}Error: El archivo no tiene extensión .pdf{RST}")
            sys.exit(1)
        pdf_files = [target]
    elif target.is_dir():
        pdf_files = sorted(target.rglob("*.pdf"))
        if not pdf_files:
            print(f"{Y}No se encontraron archivos PDF en {target}{RST}")
            sys.exit(0)
        print(f"{G}{BOLD}Se encontraron {len(pdf_files)} archivos PDF{RST}")
    else:
        print(f"{R}Error: {target} no es un archivo ni directorio válido{RST}")
        sys.exit(1)

    # ── Analizar cada PDF ─────────────────────────────────────────────────────
    all_results: List[PDFForensicResult] = []
    report_hashes: Dict[str, str] = {}

    for i, pdf_path in enumerate(pdf_files, 1):
        if len(pdf_files) > 1:
            print(f"\n{C}[{i}/{len(pdf_files)}]{RST}", end=" ")
            progress(i, len(pdf_files), pdf_path.name)
            print()

        try:
            result = analyze_pdf(str(pdf_path), str(output_dir), args.verbose)
            all_results.append(result)

            # Generar informe HTML individual
            report_path, report_hash = generate_html_report(result, str(output_dir))
            report_hashes[result.pdf_name] = report_hash

        except Exception as e:
            print(f"\n{R}Error analizando {pdf_path.name}: {e}{RST}")
            if args.verbose:
                traceback.print_exc()

    # ── Informe batch si hay más de un PDF ────────────────────────────────────
    if len(all_results) > 1:
        section("GENERANDO INFORME CONSOLIDADO BATCH", "📦")
        batch_path, batch_hash = generate_batch_report(
            all_results, str(output_dir), report_hashes
        )
        print(f"\n{M}{BOLD}{'═'*65}{RST}")
        print(f"{M}{BOLD}  ANÁLISIS BATCH COMPLETADO{RST}")
        print(f"{M}{'═'*65}{RST}")
        print(f"  PDFs analizados: {BOLD}{len(all_results)}{RST}")
        print(f"  Informe maestro: {G}{batch_path}{RST}")
        print(f"  Sello SHA-512:   {DIM}{batch_hash}…{RST}")
    elif all_results:
        r = all_results[0]
        rh = list(report_hashes.values())[0]
        print(f"\n{M}{BOLD}{'═'*65}{RST}")
        print(f"{M}{BOLD}  ANÁLISIS COMPLETADO{RST}")
        print(f"{M}{'═'*65}{RST}")
        print(f"  Riesgo:        {_risk_color(r.risk_label)}{BOLD}{r.risk_label}{RST}")
        print(f"  Hallazgos:     {BOLD}{len(r.findings)}{RST}")
        print(f"  Informe:       {G}{output_dir}{RST}")
        print(f"  Sello SHA-512: {DIM}{rh}…{RST}")

    print(f"\n{DIM}Directorio de salida: {output_dir}{RST}\n")


if __name__ == "__main__":
    main()
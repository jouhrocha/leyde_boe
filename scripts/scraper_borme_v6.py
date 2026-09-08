#!/usr/bin/env python3
"""
SCRAPER BORME v6 - MÁXIMA VELOCIDAD + UI RICA
==============================================
Optimizaciones vs v5:
  1. Sin intentos de PDF por defecto (--con-pdf para activarlo) — era el cuello de botella principal
  2. RETRY_ATTEMPTS=2 y delays mínimos — menos tiempo perdido en fallos
  3. JITTER casi nulo — el BOE no tiene rate limit agresivo
  4. Semáforo separado por día — los días paralelos no se bloquean entre sí
  5. Timeout reducido a 15s — los fallos se detectan antes
  6. HTTP/1.1 keep-alive real con pool de conexiones por hilo
  7. Parseo HTML con lxml (más rápido que html.parser)
  8. Modo --solo-indices: solo descarga el índice sin parsear (útil para debug)

Uso rápido:
    pip install requests beautifulsoup4 lxml rich
    python scraper_borme_v6.py --workers 50 --dias-paralelo 6

Con PDFs (más lento pero más completo):
    python scraper_borme_v6.py --con-pdf --workers 30 --dias-paralelo 4
"""

import sqlite3, os, re, time, random, threading, argparse, io
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
import xml.etree.ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    Progress, BarColumn, TextColumn, TimeElapsedColumn,
    TimeRemainingColumn, SpinnerColumn, MofNCompleteColumn, TaskProgressColumn
)
from rich.table import Table
from rich.text import Text
from rich import box

try:
    import pdfplumber
    PDF_INSTALADO = True
except ImportError:
    PDF_INSTALADO = False

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DB_PATH        = "registro_mercantil.db"
MAX_WORKERS    = 50       # ← subido de 20
DIAS_PARALELO  = 6        # ← subido de 3
TIMEOUT        = 15       # ← bajado de 45 (fallos rápidos)
RETRY_ATTEMPTS = 2        # ← bajado de 3
RETRY_DELAY    = 1.0      # ← bajado de 2.5
BATCH_SIZE     = 500
FECHA_INICIO   = date(2009, 1, 2)
JITTER_MIN     = 0.0      # ← eliminado
JITTER_MAX     = 0.05     # ← casi nulo
LOG_MAX        = 200
CON_PDF        = False    # se activa con --con-pdf

API_BORME_URL  = "https://www.boe.es/datosabiertos/api/borme/sumario/{fecha}"
TXT_URL        = "https://www.boe.es/diario_borme/txt.php?id={id}"
PDF_BASE       = "https://www.boe.es"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]
ACCEPT_LANGUAGES = [
    "es-ES,es;q=0.9,en;q=0.8",
    "es-ES,es;q=0.9",
    "ca-ES,ca;q=0.9,es;q=0.8,en;q=0.7",
]

# ─── ESTADO GLOBAL ────────────────────────────────────────────────────────────
db_lock        = threading.Lock()
_stats_lock    = threading.Lock()
_log_lock      = threading.Lock()
_dias_lock     = threading.Lock()
_log_lines     = deque(maxlen=LOG_MAX)
_dias_activos  = {}
fallidas_lista = []
fallidas_lock  = threading.Lock()

_stats = {
    "total_bd": 0, "insertados": 0, "fallidas": 0,
    "pdf_ok": 0, "html_ok": 0, "peticiones": 0,
    "bytes_mb": 0.0, "dias_ok": 0, "dias_sin_borme": 0,
    "ultimo": "", "ultimo_error": "", "inicio": time.time(),
    "errores_por_tipo": {},
}

TOR_ENABLED = False
TOR_PROXIES = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}

# ─── SESIONES HTTP (una por hilo para máximo rendimiento) ─────────────────────
_thread_local = threading.local()

def _get_session() -> requests.Session:
    """Devuelve una sesión HTTP reutilizable por hilo (más eficiente que una global)."""
    if not hasattr(_thread_local, 'session'):
        retry = Retry(
            total=RETRY_ATTEMPTS,
            backoff_factor=RETRY_DELAY,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=4,
            pool_maxsize=4,
        )
        s = requests.Session()
        s.mount("https://", adapter)
        s.mount("http://",  adapter)
        _thread_local.session = s
    return _thread_local.session


def _headers(accept_xml=False, accept_pdf=False) -> dict:
    ua   = random.choice(USER_AGENTS)
    lang = random.choice(ACCEPT_LANGUAGES)
    is_ff = "Firefox" in ua
    if accept_pdf:
        accept = "application/pdf,*/*;q=0.8"
    elif accept_xml:
        accept = "application/xml,text/xml;q=0.9,*/*;q=0.8"
    elif is_ff:
        accept = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    else:
        accept = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    h = {
        "User-Agent": ua, "Accept": accept, "Accept-Language": lang,
        "Accept-Encoding": "gzip, deflate, br", "Connection": "keep-alive",
        "Referer": "https://www.boe.es/diario_borme/",
        "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }
    if not is_ff:
        h["Sec-Fetch-User"] = "?1"
    return h


def _get(url, accept_xml=False, accept_pdf=False) -> requests.Response:
    if JITTER_MAX > 0:
        time.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
    s = _get_session()
    kwargs = dict(
        headers=_headers(accept_xml=accept_xml, accept_pdf=accept_pdf),
        timeout=TIMEOUT,
        stream=False,
    )
    if TOR_ENABLED:
        kwargs["proxies"] = TOR_PROXIES
    resp = s.get(url, **kwargs)
    with _stats_lock:
        _stats["peticiones"]  += 1
        _stats["bytes_mb"]    += len(resp.content) / 1_048_576
    return resp


# ─── BD ───────────────────────────────────────────────────────────────────────

def inicializar_db(conn):
    c = conn.cursor()
    c.executescript("""
        PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;
        PRAGMA cache_size=-131072; PRAGMA temp_store=MEMORY;
        PRAGMA mmap_size=536870912; PRAGMA page_size=8192;
        PRAGMA wal_autocheckpoint=1000;
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS entradas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            borme_id TEXT, fecha TEXT, seccion TEXT, subseccion TEXT,
            provincia TEXT, empresa TEXT, nif TEXT, num_registro TEXT DEFAULT '',
            actos TEXT, datos_registrales TEXT, texto TEXT, url TEXT,
            fuente TEXT DEFAULT 'html',
            UNIQUE(borme_id, empresa)
        )""")
    for col, dflt in [("num_registro","''"), ("fuente","'html'")]:
        try:
            c.execute(f"ALTER TABLE entradas ADD COLUMN {col} TEXT DEFAULT {dflt}")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    for idx, col in [
        ("idx_fecha","fecha"), ("idx_empresa","empresa"), ("idx_nif","nif"),
        ("idx_provincia","provincia"), ("idx_borme_id","borme_id"),
    ]:
        c.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON entradas({col})")
    c.execute("""
        CREATE TABLE IF NOT EXISTS fechas_procesadas (
            fecha TEXT PRIMARY KEY, n_insertadas INTEGER, procesada_en TEXT
        )""")
    conn.commit()
    c.execute("SELECT COUNT(*) FROM entradas")
    with _stats_lock:
        _stats["total_bd"] = c.fetchone()[0]
    return c


def fecha_ya_procesada(c, fecha_str):
    c.execute("SELECT 1 FROM fechas_procesadas WHERE fecha=?", (fecha_str,))
    return c.fetchone() is not None


def marcar_fecha_procesada(conn, c, fecha_str, n):
    with db_lock:
        c.execute("INSERT OR REPLACE INTO fechas_procesadas VALUES(?,?,?)",
                  (fecha_str, n, datetime.now().isoformat()))
        conn.commit()


def _flush_batch(conn, c, batch):
    if not batch:
        return 0
    insertados = 0
    with db_lock:
        try:
            for d in batch:
                c.execute("""
                    INSERT OR IGNORE INTO entradas
                    (borme_id,fecha,seccion,subseccion,provincia,empresa,nif,
                     num_registro,actos,datos_registrales,texto,url,fuente)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    d['borme_id'], d['fecha'], d['seccion'], d['subseccion'],
                    d['provincia'], d['empresa'], d['nif'], d.get('num_registro',''),
                    d['actos'], d['datos_registrales'], d['texto'], d['url'],
                    d.get('fuente','html'),
                ))
                if c.rowcount:
                    insertados += 1
                    with _stats_lock:
                        _stats["insertados"] += 1
                        _stats["total_bd"]   += 1
                        _stats["ultimo"]      = d['empresa'][:55]
                        if d.get('fuente') == 'pdf':
                            _stats["pdf_ok"] += 1
                        else:
                            _stats["html_ok"] += 1
            conn.commit()
        except sqlite3.Error as e:
            _log(f"[red]BD error: {e}[/red]")
    return insertados


# ─── LOG ──────────────────────────────────────────────────────────────────────

def _log(msg: str, empresa: str = ""):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[dim]{ts}[/dim] {msg}"
    with _log_lock:
        _log_lines.append(line)


def _log_err(msg: str, tipo: str = ""):
    ts = datetime.now().strftime("%H:%M:%S")
    with _log_lock:
        _log_lines.append(f"[dim]{ts}[/dim] [red]✗[/red] [dim]{msg[:90]}[/dim]")
    with _stats_lock:
        _stats["fallidas"]     += 1
        _stats["ultimo_error"]  = msg[:70]
        if tipo:
            _stats["errores_por_tipo"][tipo] = _stats["errores_por_tipo"].get(tipo, 0) + 1
    with fallidas_lock:
        fallidas_lista.append(msg)


# ─── ÍNDICE DEL DÍA ───────────────────────────────────────────────────────────

def _xtxt(elem, tag):
    node = elem.find(tag)
    return (node.text or '').strip() if node is not None else ''


def obtener_entradas_fecha(fecha: date) -> list:
    url = API_BORME_URL.format(fecha=fecha.strftime("%Y%m%d"))
    try:
        resp = _get(url, accept_xml=True)
        if resp.status_code in (404, 204):
            return []
        if resp.status_code == 429:
            time.sleep(30); return obtener_entradas_fecha(fecha)
        if resp.status_code != 200:
            return []
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            return []
        code = root.find('.//code')
        if code is not None and code.text and code.text.strip() != '200':
            return []
        entradas  = []
        fecha_iso = fecha.strftime("%Y-%m-%d")
        for seccion in root.iter('seccion'):
            cs = seccion.get('codigo','')
            for item in seccion.iter('item'):
                borme_id = _xtxt(item,'identificador')
                titulo   = _xtxt(item,'titulo')
                url_pdf  = _xtxt(item,'url_pdf')
                url_html = _xtxt(item,'url_html')
                url_xml  = _xtxt(item,'url_xml')
                for attr in ('url_pdf','url_html','url_xml'):
                    pass
                if url_pdf  and url_pdf.startswith('/'):  url_pdf  = PDF_BASE + url_pdf
                if url_html and url_html.startswith('/'): url_html = PDF_BASE + url_html
                if url_xml  and url_xml.startswith('/'):  url_xml  = PDF_BASE + url_xml
                if not url_html and borme_id:
                    url_html = TXT_URL.format(id=borme_id)
                subseccion = ''
                for apt in seccion.iter('apartado'):
                    if item in list(apt.iter('item')):
                        subseccion = apt.get('nombre',''); break
                entradas.append({
                    'borme_id': borme_id, 'fecha': fecha_iso, 'seccion': cs,
                    'subseccion': subseccion,
                    'provincia': titulo if cs in ('A','B') else '',
                    'titulo': titulo,
                    'url_pdf': url_pdf, 'url_html': url_html, 'url_xml': url_xml,
                })
        return entradas
    except Exception:
        return []


# ─── PDF (opcional) ───────────────────────────────────────────────────────────

def extraer_texto_pdf(pdf_bytes):
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return '\n'.join(p.extract_text(x_tolerance=2,y_tolerance=2) or '' for p in pdf.pages)
    except Exception:
        return ''


def parsear_pdf_seccion_ab(texto, entry):
    """
    Parsea el texto extraído de un PDF de Sección A del BORME.

    Formato REAL del PDF (confirmado con contenido real):
        14367 - TITANES FILM 2023 SL. Constitución. ... Datos registrales. T 6434, F 133, S 8, H MA178509, I/A 1 (20.12.23).
        14368 - AUDAZES CLUB SL. Nombramientos. ... Datos registrales. T 5978, F 91, S 8, H MA157664, I/A 2 (3.01.24).

    NOTA: El NIF NO está en el PDF, solo en la página HTML individual.
          La hoja registral (H XXXXX) es el identificador único real.
    """
    if not texto:
        return []

    registros = []
    provincia = entry.get('provincia', '')

    # Dividir por bloques de empresa: cada entrada empieza con N - NOMBRE.
    bloques = re.split(r'(?=\d{4,6}\s+-\s+)', texto.strip())

    for bloque in bloques:
        bloque = bloque.strip()
        if not bloque:
            continue

        # Extraer número de entrada y nombre de empresa
        m = re.match(r'^(\d{4,6})\s+-\s+(.+?)\.(.+)$', bloque, re.DOTALL)
        if not m:
            continue

        empresa = m.group(2).strip()
        cuerpo  = m.group(3).strip()

        # Datos registrales: T X, L X, F X, S 8, H HOJA, I/A X (fecha)
        datos_reg = ''
        m_reg = re.search(r'[Dd]atos\s+registrales\.?\s*(.+?)(?=\d{4,6}\s+-|\Z)', cuerpo, re.DOTALL)
        if m_reg:
            datos_reg = ' '.join(m_reg.group(1).split()).rstrip('.')

        # Hoja registral (identificador único de la empresa en el registro)
        hoja = ''
        m_h = re.search(r',\s*H\s+([A-Z]{0,3}\s*\d+)', datos_reg)
        if m_h:
            hoja = m_h.group(1).strip().replace(' ', '')

        # Tomo y folio
        tomo = folio = ''
        m_t = re.search(r'\bT\s+(\d+)', datos_reg)
        m_f = re.search(r'\bF\s+(\d+)', datos_reg)
        if m_t: tomo  = m_t.group(1)
        if m_f: folio = m_f.group(1)

        num_registro = ''
        if tomo and folio and hoja:
            num_registro = f"T{tomo}-F{folio}-H{hoja}"

        # Fecha de inscripción
        fecha_insc = ''
        m_fi = re.search(r'\((\d+\.\d+\.\d+)\)', datos_reg)
        if m_fi:
            fecha_insc = m_fi.group(1)

        # Actos: todo el cuerpo antes de "Datos registrales"
        actos_raw = re.split(r'[Dd]atos\s+registrales', cuerpo)[0].strip().rstrip('.')
        # Separar actos por punto seguido de mayúscula
        actos_lista = [a.strip() for a in re.split(r'\.\s+(?=[A-Z])', actos_raw) if a.strip()]
        actos = ' | '.join(actos_lista)

        if not empresa:
            continue

        registros.append({
            'borme_id':          entry['borme_id'],
            'fecha':             entry['fecha'],
            'seccion':           entry['seccion'],
            'subseccion':        entry.get('subseccion', ''),
            'provincia':         provincia,
            'empresa':           empresa,
            'nif':               '',          # No está en el PDF
            'num_registro':      num_registro,
            'actos':             actos,
            'datos_registrales': datos_reg,
            'texto':             bloque[:2000],
            'url':               entry.get('url_pdf') or entry.get('url_html', ''),
            'fuente':            'pdf',
        })

    return registros


# ─── DESCARGA A/B ─────────────────────────────────────────────────────────────

def descargar_seccion_ab(entry) -> tuple:
    # Intentar PDF primero (solo si --con-pdf)
    if CON_PDF and PDF_INSTALADO and entry.get('url_pdf'):
        try:
            resp = _get(entry['url_pdf'], accept_pdf=True)
            if resp.status_code == 200 and resp.content:
                texto = extraer_texto_pdf(resp.content)
                regs  = parsear_pdf_seccion_ab(texto, entry)
                if regs:
                    _log(f"[cyan]PDF[/cyan] {entry.get('provincia','')[:20]} → [green]{len(regs)}[/green]")
                    return regs, ''
        except Exception:
            pass

    # HTML (camino principal)
    url = entry.get('url_html','')
    if not url:
        return [], 'Sin URL'
    try:
        resp = _get(url)
        if resp.status_code == 429:
            time.sleep(20)
            resp = _get(url)
        if resp.status_code != 200:
            return [], f'HTTP {resp.status_code}'
        regs = parsear_html_ab(resp.text, entry)
        return (regs, '') if regs else ([], 'Sin datos HTML')
    except requests.Timeout:
        return [], 'Timeout'
    except Exception as e:
        return [], f'Error: {e}'


def parsear_html_ab(html, entry):
    try:
        soup = BeautifulSoup(html, 'lxml')
    except Exception:
        soup = BeautifulSoup(html, 'html.parser')

    registros = []
    provincia = entry.get('provincia','')
    if not provincia:
        enc = soup.find(class_='encabezado')
        if enc: provincia = enc.get_text(strip=True)

    doc = (soup.find('div',id='documento') or
           soup.find('div',class_='sumario-borme') or soup.body)
    if not doc: return []

    dispos = doc.find_all('div',class_='dispo') or [doc]
    for dispo in dispos:
        r = _parsear_parrafos(dispo.find_all('p'), entry['borme_id'], provincia, entry)
        if r: registros.append(r)
    return registros


def _parsear_parrafos(parrafos, borme_id, provincia, entry):
    empresa = ''; nif = ''; actos = ''; datos_reg = ''; textos = []
    for p in parrafos:
        tp = p.get_text(separator=' ', strip=True)
        if not tp: continue
        textos.append(tp)
        if p.find('b') and not empresa:
            m = re.search(r'\(([A-Z0-9]{8,9})\)', tp)
            if m: nif = m.group(1)
            empresa = re.sub(r'\s*\(.*?\)\s*','',tp).strip()
            continue
        if re.search(r'[Aa]ctos?\s+inscritos?', tp):
            m = re.search(r'[Aa]ctos?\s+inscritos?[:\s]+(.+)', tp)
            if m: actos = ' | '.join(a.strip() for a in re.split(r'[.;]',m.group(1)) if a.strip())
        if re.search(r'[Dd]atos?\s+registrales?', tp):
            m = re.search(r'[Dd]atos?\s+registrales?\.?\s*(.+)', tp)
            if m: datos_reg = m.group(1).strip()
    if not empresa: return None
    tc = ' '.join(textos)
    if not nif:
        m = re.search(r'\b([A-Z]\d{7}[A-Z0-9]|\d{8}[A-Z])\b', tc)
        if m: nif = m.group(1)
    num_reg = ''
    m = re.search(r'[Tt]omo\s+(\d+)[,\s]+[Ff]olio\s+(\d+)[,\s]+[Hh]oja\s+([A-Z0-9\-]+)', tc)
    if m: num_reg = f"T{m.group(1)}-F{m.group(2)}-H{m.group(3)}"
    return {
        'borme_id': borme_id, 'fecha': entry['fecha'], 'seccion': entry['seccion'],
        'subseccion': entry.get('subseccion',''), 'provincia': provincia,
        'empresa': empresa, 'nif': nif, 'num_registro': num_reg,
        'actos': actos, 'datos_registrales': datos_reg,
        'texto': tc[:2000], 'url': entry['url_html'], 'fuente': 'html',
    }


# ─── DESCARGA C ───────────────────────────────────────────────────────────────

def descargar_seccion_c(entry) -> tuple:
    url = entry.get('url_html','')
    if not url: return [], 'Sin URL'
    try:
        resp = _get(url)
        if resp.status_code == 429:
            time.sleep(20); resp = _get(url)
        if resp.status_code != 200:
            return [], f'HTTP {resp.status_code}'
        r = parsear_html_c(resp.text, entry)
        return ([r],'') if r else ([],'Sin datos')
    except requests.Timeout:
        return [], 'Timeout'
    except Exception as e:
        return [], f'Error: {e}'


def parsear_html_c(html, entry):
    try:
        soup = BeautifulSoup(html, 'lxml')
    except Exception:
        soup = BeautifulSoup(html, 'html.parser')
    empresa = entry.get('titulo','').strip()
    nif = ''
    for sel in ['p b','h3','h4','.titulo-disposicion']:
        el = soup.select_one(sel)
        if el:
            cab = el.get_text(strip=True)
            m = re.search(r'\(([A-Z0-9]{8,9})\)', cab)
            if m: nif = m.group(1)
            if not empresa or len(empresa) < 3:
                empresa = re.sub(r'\s*\(.*?\)\s*','',cab).strip()
            break
    doc = (soup.find('div',id='documento') or soup.find('div',class_='dispo') or soup.body)
    parrafos = []
    if doc:
        for p in doc.find_all('p'):
            t = p.get_text(separator=' ',strip=True)
            if t: parrafos.append(t)
    tc = '\n'.join(parrafos).strip()
    actos = ''
    m = re.search(r'[Aa]ctos?\s+inscritos?[:\s]+([^.]+(?:\.[^.]+){0,5})', tc)
    if m: actos = ' | '.join(a.strip() for a in re.split(r'[.;]',m.group(1)) if a.strip())
    datos_reg = ''
    m = re.search(r'[Dd]atos?\s+registrales?\.?\s*(.+?)(?:\n|$)', tc)
    if m: datos_reg = m.group(1).strip()
    if not nif:
        m = re.search(r'\b([A-Z]\d{7}[A-Z0-9]|\d{8}[A-Z])\b', html)
        if m: nif = m.group(1)
    if not empresa: return None
    return {
        'borme_id': entry['borme_id'], 'fecha': entry['fecha'],
        'seccion': entry['seccion'], 'subseccion': entry.get('subseccion',''),
        'provincia': entry.get('provincia',''), 'empresa': empresa, 'nif': nif,
        'num_registro': '', 'actos': actos, 'datos_registrales': datos_reg,
        'texto': tc[:2000], 'url': entry['url_html'], 'fuente': 'html',
    }


# ─── PIPELINE FLAT: sin dia_executor, todos los bloques en un único pool ──────

def _fetch_index(fecha: date) -> tuple:
    """Stage 1: solo descarga el índice del día (rápido, llamada API)."""
    fecha_str = fecha.strftime("%Y-%m-%d")
    entradas  = obtener_entradas_fecha(fecha)
    return fecha_str, entradas


def _descargar_bloque(fecha_str: str, entry: dict) -> tuple:
    """Stage 2: descarga un bloque individual y devuelve (fecha_str, lista, error)."""
    if entry['seccion'] in ('A', 'B'):
        lista, err = descargar_seccion_ab(entry)
    else:
        lista, err = descargar_seccion_c(entry)
    return fecha_str, lista, err, entry


def run_pipeline(pendientes, conn, c, args, progress, task_id):
    """
    Arquitectura FLAT de 2 etapas:

    Etapa 1 — Índices (rápido, paralelo):
        Hasta INDEX_WORKERS peticiones API simultáneas.
        En cuanto llega el índice de un día, sus bloques se mandan a la etapa 2.
        NO hay que esperar a que terminen todos los días para empezar a descargar.

    Etapa 2 — Bloques (HTTP, paralelo):
        Un único pool de WORKERS threads procesa bloques de TODOS los días a la vez.
        Cuando todos los bloques de un día acaban → se marca como procesado y
        el slot de "día activo" queda libre para el siguiente.

    El resultado: los días se solapan completamente — siempre hay DIAS_PARALELO
    días con bloques en vuelo simultáneamente.
    """
    from queue import Queue

    INDEX_WORKERS = min(args.dias_paralelo * 2, 20)  # hilos para la etapa 1
    WINDOW        = args.dias_paralelo                # días activos máximos simultáneos

    # Estado por día
    day_state   = {}   # fecha_str → {total, done, ok, fail, insertados, batch}
    day_lock    = threading.Lock()

    # Cola de índices listos para procesar
    index_q: Queue = Queue()

    # ── Etapa 1: fetcher de índices ───────────────────────────────────────────
    def _index_worker(fecha):
        fecha_str, entradas = _fetch_index(fecha)
        index_q.put((fecha_str, entradas))

    idx_executor = ThreadPoolExecutor(max_workers=INDEX_WORKERS)

    # Enviamos los primeros WINDOW días de golpe; el resto los iremos añadiendo
    # conforme se liberen slots
    dias_iter      = iter(pendientes)
    dias_en_vuelo  = 0   # índices solicitados pero cuyo día aún no terminó de procesar

    # Llenamos la ventana inicial
    for _ in range(min(WINDOW * 3, len(pendientes))):
        f = next(dias_iter, None)
        if f is None: break
        idx_executor.submit(_index_worker, f)
        dias_en_vuelo += 1

    # ── Etapa 2: pool único de bloques ────────────────────────────────────────
    blk_executor  = ThreadPoolExecutor(max_workers=args.workers)
    block_futures = {}   # future → (fecha_str, entry)

    dias_totales_pendientes = len(pendientes)
    dias_completados        = 0

    def _marcar_dia_si_completo(fecha_str):
        """Llama a flush + marcar + advance si el día ha terminado todos sus bloques."""
        nonlocal dias_completados
        with day_lock:
            s = day_state.get(fecha_str)
            if s is None or s["done"] < s["total"]:
                return
            batch = s.pop("batch", [])

        if batch:
            _flush_batch(conn, c, batch)

        marcar_fecha_procesada(conn, c, fecha_str, day_state[fecha_str]["insertados"])
        progress.advance(task_id)
        with _stats_lock:
            _stats["dias_ok"] += 1
        with _dias_lock:
            _dias_activos.pop(fecha_str, None)
        with day_lock:
            day_state.pop(fecha_str, None)

        dias_completados += 1

        # Pedir el siguiente día de la cola si quedan
        next_f = next(dias_iter, None)
        if next_f is not None:
            idx_executor.submit(_index_worker, next_f)

    # ── Loop principal ────────────────────────────────────────────────────────
    dias_procesados_total = 0

    while dias_completados < dias_totales_pendientes:

        # Vaciar la cola de índices listos
        while not index_q.empty():
            fecha_str, entradas = index_q.get_nowait()
            dias_procesados_total += 1

            if not entradas:
                marcar_fecha_procesada(conn, c, fecha_str, 0)
                progress.advance(task_id)
                with _stats_lock:
                    _stats["dias_sin_borme"] += 1
                _log(f"[dim]── {fecha_str} sin publicación[/dim]")
                dias_completados += 1
                # Pedir el siguiente
                next_f = next(dias_iter, None)
                if next_f is not None:
                    idx_executor.submit(_index_worker, next_f)
                continue

            n_bloques = len(entradas)
            with day_lock:
                day_state[fecha_str] = {
                    "total": n_bloques, "done": 0,
                    "ok": 0, "fail": 0,
                    "insertados": 0, "batch": [],
                }
            with _dias_lock:
                _dias_activos[fecha_str] = {
                    "total": n_bloques, "done": 0, "ok": 0, "fail": 0
                }

            # Enviar todos los bloques al executor de bloques
            for e in entradas:
                fut = blk_executor.submit(_descargar_bloque, fecha_str, e)
                block_futures[fut] = (fecha_str, e)

        # Procesar bloques completados (sin bloquear mucho tiempo)
        done_futs = [f for f in list(block_futures) if f.done()]
        for fut in done_futs:
            fecha_str, entry = block_futures.pop(fut)
            try:
                _, lista, error, _ = fut.result()
            except Exception as ex:
                lista, error = [], str(ex)

            with day_lock:
                s = day_state.get(fecha_str)
                if s is None:
                    continue
                s["done"] += 1

            with _dias_lock:
                if fecha_str in _dias_activos:
                    _dias_activos[fecha_str]["done"] += 1

            if not lista:
                _log_err(f"{entry['borme_id']} {entry.get('provincia','')[:18]} — {error}")
                with day_lock:
                    if fecha_str in day_state:
                        day_state[fecha_str]["fail"] += 1
                with _dias_lock:
                    if fecha_str in _dias_activos:
                        _dias_activos[fecha_str]["fail"] += 1
            else:
                with day_lock:
                    if fecha_str in day_state:
                        day_state[fecha_str]["ok"]    += 1
                        day_state[fecha_str]["batch"].extend(lista)
                with _dias_lock:
                    if fecha_str in _dias_activos:
                        _dias_activos[fecha_str]["ok"] += 1
                for d in lista:
                    _log(f"[green]+[/green] [bold]{d['empresa'][:45]}[/bold] "
                         f"[dim]{d.get('provincia','')[:18]} · {d['fecha']}[/dim]")

            # Flush batch si es grande
            batch_to_flush = []
            with day_lock:
                s = day_state.get(fecha_str)
                if s and len(s.get("batch", [])) >= BATCH_SIZE:
                    batch_to_flush = s["batch"][:]
                    s["batch"] = []
            if batch_to_flush:
                n = _flush_batch(conn, c, batch_to_flush)
                with day_lock:
                    if fecha_str in day_state:
                        day_state[fecha_str]["insertados"] += n

            # Comprobar si el día terminó
            _marcar_dia_si_completo(fecha_str)

        time.sleep(0.05)  # evitar busy-wait

    # Cleanup
    idx_executor.shutdown(wait=False)
    blk_executor.shutdown(wait=False)


# ─── PANELES UI ───────────────────────────────────────────────────────────────

def _panel_stats(total_dias):
    with _stats_lock:
        s = dict(_stats)
    elapsed  = max(1, time.time() - s["inicio"])
    regs_s   = s["insertados"] / elapsed
    dias_ok  = s["dias_ok"] + s["dias_sin_borme"]
    pct      = dias_ok / total_dias * 100 if total_dias > 0 else 0

    t = Table(box=None, padding=(0,2), show_header=False, expand=True)
    t.add_column("k", style="dim", width=18)
    t.add_column("v", style="bold", width=14)
    t.add_column("k2", style="dim", width=18)
    t.add_column("v2", style="bold")

    t.add_row("Total en BD",     f"{s['total_bd']:,}",
              "Velocidad",       f"[yellow]{regs_s:.1f}[/yellow] reg/s")
    t.add_row("Nuevos sesión",   f"[green]{s['insertados']:,}[/green]",
              "Descargado",      f"{s['bytes_mb']:.1f} MB")
    t.add_row("Desde PDF",       f"[cyan]{s['pdf_ok']:,}[/cyan]",
              "HTTP requests",   f"{s['peticiones']:,}")
    t.add_row("Desde HTML",      f"{s['html_ok']:,}",
              "Fallidas",        f"[red]{s['fallidas']:,}[/red]")
    t.add_row("Días procesados", f"{dias_ok:,}/{total_dias:,} ({pct:.0f}%)",
              "Sin BORME",       f"{s['dias_sin_borme']:,}")
    if s["ultimo"]:
        t.add_row("Último", f"[green]{s['ultimo'][:45]}[/green]", "", "")
    if s["ultimo_error"]:
        t.add_row("Último error", f"[red dim]{s['ultimo_error'][:45]}[/red dim]", "", "")

    return Panel(t, title="[bold]📊 Estadísticas[/bold]", border_style="blue", padding=(0,1))


def _panel_activos():
    with _dias_lock:
        activos = dict(_dias_activos)
    if not activos:
        return Panel("[dim]Esperando...[/dim]",
                     title="[bold]⚡ Procesando[/bold]", border_style="yellow")
    t = Table(box=box.SIMPLE, padding=(0,1), show_header=True, expand=True)
    t.add_column("Fecha",  style="bold", width=12)
    t.add_column("Progreso", width=24)
    t.add_column("✓",  justify="right", style="green", width=6)
    t.add_column("✗",  justify="right", style="red",   width=6)
    t.add_column("Tot",justify="right", style="dim",   width=6)
    for fecha_str, info in activos.items():
        done  = info["done"]
        total = max(info["total"], 1)
        pct   = done / total
        filled = int(22 * pct)
        bar = f"[cyan]{'█'*filled}[/cyan][dim]{'░'*(22-filled)}[/dim] [dim]{pct*100:.0f}%[/dim]"
        t.add_row(fecha_str, bar, str(info["ok"]), str(info["fail"]), str(total))
    return Panel(t, title="[bold]⚡ Procesando ahora[/bold]", border_style="yellow", padding=(0,1))


def _panel_log():
    with _log_lock:
        lines = list(_log_lines)[-22:]
    txt = Text(overflow="fold")
    for line in lines:
        txt.append_text(Text.from_markup(line + "\n"))
    return Panel(txt, title="[bold]📋 Actividad[/bold]", border_style="dim", padding=(0,1))


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def generar_fechas(desde, hasta):
    fechas, actual = [], desde
    while actual <= hasta:
        if actual.weekday() < 5: fechas.append(actual)
        actual += timedelta(days=1)
    return fechas


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Scraper BORME v6 — Máxima velocidad')
    parser.add_argument('--desde',         default=FECHA_INICIO.strftime("%Y-%m-%d"))
    parser.add_argument('--hasta',         default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument('--workers',       type=int, default=MAX_WORKERS,
                        help='Hilos HTTP (default 50)')
    parser.add_argument('--dias-paralelo', type=int, default=DIAS_PARALELO,
                        help='Días en paralelo (default 6)')
    parser.add_argument('--db',            default=DB_PATH)
    parser.add_argument('--tor',           action='store_true')
    parser.add_argument('--con-pdf',       action='store_true',
                        help='Activar extracción PDF (más lento pero más completo)')
    args = parser.parse_args()

    global TOR_ENABLED, CON_PDF
    TOR_ENABLED = args.tor
    CON_PDF     = args.con_pdf and PDF_INSTALADO

    console = Console()

    if args.con_pdf and not PDF_INSTALADO:
        console.print("[yellow]⚠️  pdfplumber no instalado — solo HTML[/yellow]")
    if CON_PDF:
        console.print("[cyan]📄 Modo PDF activado[/cyan]")

    fecha_desde = datetime.strptime(args.desde, "%Y-%m-%d").date()
    fecha_hasta = datetime.strptime(args.hasta, "%Y-%m-%d").date()

    conn = sqlite3.connect(args.db, check_same_thread=False)
    c    = inicializar_db(conn)

    todas      = generar_fechas(fecha_desde, fecha_hasta)
    pendientes = [f for f in todas if not fecha_ya_procesada(c, f.strftime("%Y-%m-%d"))]

    if not pendientes:
        console.print("[green]✅ Todo ya descargado.[/green]")
        conn.close(); return

    console.print(f"\n[bold]BORME v6[/bold] — {len(pendientes):,} días pendientes de {len(todas):,}")
    console.print(f"Workers: [bold]{args.workers}[/bold] · "
                  f"Días paralelo: [bold]{args.dias_paralelo}[/bold] · "
                  f"PDF: [bold]{'sí' if CON_PDF else 'no'}[/bold]\n")

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        expand=True,
    )
    task_id = progress.add_task(
        f"BORME {args.desde[:4]}–{args.hasta[:4]}", total=len(pendientes))

    layout = Layout()
    layout.split_column(
        Layout(name="bar",    size=3),
        Layout(name="middle", size=16),
        Layout(name="log"),
    )
    layout["middle"].split_row(
        Layout(name="stats",   ratio=3),
        Layout(name="activos", ratio=2),
    )
    layout["bar"].update(progress)
    layout["stats"].update(_panel_stats(len(todas)))
    layout["activos"].update(_panel_activos())
    layout["log"].update(_panel_log())

    _done = threading.Event()

    def _ui_loop():
        while not _done.is_set():
            layout["stats"].update(_panel_stats(len(todas)))
            layout["activos"].update(_panel_activos())
            layout["log"].update(_panel_log())
            time.sleep(0.33)

    t_ui = threading.Thread(target=_ui_loop, daemon=True)

    with Live(layout, console=console, refresh_per_second=3, screen=False):
        t_ui.start()
        try:
            run_pipeline(pendientes, conn, c, args, progress, task_id)
        finally:
            _done.set()

    # FTS5
    console.print("\n[bold]🔍 Índice FTS5...[/bold]")
    try:
        c.execute("DROP TABLE IF EXISTS borme_busqueda")
        c.execute("""
            CREATE VIRTUAL TABLE borme_busqueda USING fts5(
                empresa, nif, actos, datos_registrales, texto,
                content='entradas', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )""")
        c.execute("""
            INSERT INTO borme_busqueda(rowid,empresa,nif,actos,datos_registrales,texto)
            SELECT id,empresa,nif,actos,datos_registrales,texto FROM entradas""")
        c.execute("INSERT INTO borme_busqueda(borme_busqueda) VALUES('optimize')")
        conn.commit()
        console.print("[green]✅ FTS5 listo[/green]")
    except sqlite3.Error as e:
        console.print(f"[yellow]FTS5 no disponible: {e}[/yellow]")

    # Stats finales
    c.execute("SELECT COUNT(*) FROM entradas"); total = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT empresa) FROM entradas WHERE empresa!=''"); emp = c.fetchone()[0]
    c.execute("SELECT MIN(fecha),MAX(fecha) FROM entradas"); fmin,fmax = c.fetchone() or ('—','—')
    c.execute("SELECT fuente,COUNT(*) FROM entradas GROUP BY fuente"); fuentes = dict(c.fetchall())
    tam = os.path.getsize(args.db)/1_048_576 if os.path.exists(args.db) else 0

    t = Table(title="✅ Completado", box=box.ROUNDED, border_style="green")
    t.add_column(""); t.add_column("", justify="right", style="bold")
    for k,v in [
        ("Entradas totales", f"{total:,}"), ("Empresas únicas", f"{emp:,}"),
        ("Período", f"{fmin} → {fmax}"), ("Desde PDF", f"{fuentes.get('pdf',0):,}"),
        ("Desde HTML", f"{fuentes.get('html',0):,}"), ("Tamaño BD", f"{tam:.1f} MB"),
    ]:
        t.add_row(k, v)
    console.print(t)

    if fallidas_lista:
        with open("borme_fallidas.txt","w",encoding="utf-8") as f:
            for line in fallidas_lista: f.write(line+"\n")
        console.print(f"[yellow]⚠️  {len(fallidas_lista):,} fallidas → borme_fallidas.txt[/yellow]")

    conn.close()


if __name__ == '__main__':
    main()

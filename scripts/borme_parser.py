"""
BORME Parser - adaptado de bormeparser (PabloCastellano/bormeparser)
para escribir en el schema de catalog.db

Tablas que rellena (desde cero si usas --limpiar):
  borme_anuncios  -> empresa, datos_registrales, actos_json, provincia, capital_importe
  empresas        -> nombre, provincia, fecha, capital, domicilio
  personas        -> nombre (dedup por nombre)
  empresa_persona -> id_borme, id_persona, cargo, fecha

Usa como fuente:
  borme_actos     -> url_pdf para descargar cada PDF

Dependencias:
  pip install bormeparser PyPDF2==2.11.0 requests

Uso habitual (reproceso completo desde cero):
  python borme_parser.py catalog.db --limpiar

Uso incremental (no toca lo ya parseado):
  python borme_parser.py catalog.db
"""

import json
import logging
import os
import re
import sqlite3
import tempfile

import requests

# ── bormeparser (PyPDF2 backend, código de PabloCastellano/bormeparser) ───────
from bormeparser.backends.pypdf2.parser import PyPDF2Parser
from bormeparser.borme import BormeActoCargo, BormeActoTexto

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── Regex para extraer capital y domicilio del texto de los actos ─────────────
RE_CAPITAL = re.compile(
    r'Capital(?:\s+(?:suscrito|resultante\s+suscrito))?[:\s]+'
    r'([\d.,]+)\s*(?:Euros?|€)',
    re.IGNORECASE
)
RE_DOMICILIO_CONST = re.compile(
    r'Domicilio:\s*(.+?)(?:\.\s*Capital|\.\s*$|$)',
    re.IGNORECASE
)


def _parse_importe(texto: str):
    m = RE_CAPITAL.search(texto)
    if not m:
        return None
    try:
        return float(m.group(1).replace('.', '').replace(',', '.'))
    except ValueError:
        return None


def _parse_domicilio(texto: str):
    m = RE_DOMICILIO_CONST.search(texto)
    if m:
        return m.group(1).strip().rstrip('.')
    return None


def _actos_to_json(actos: list):
    """
    Convierte la lista de BormeActo (de bormeparser) al JSON que guarda
    borme_anuncios.actos_json: [{tipo, texto}, ...]

    También extrae capital y domicilio si aparecen.
    """
    result = []
    capital = None
    domicilio = None

    for acto in actos:
        if isinstance(acto, BormeActoCargo):
            # acto.value es {cargo_str: set(nombres)}
            # Cada cargo va como entrada separada (igual que el parser original)
            if not acto.value:
                result.append({"tipo": acto.name, "texto": ""})
                continue
            for cargo_name, nombres in acto.value.items():
                texto_cargo = ";".join(sorted(nombres))
                result.append({"tipo": cargo_name, "texto": texto_cargo})

        elif isinstance(acto, BormeActoTexto):
            texto = acto.value or ""
            result.append({"tipo": acto.name, "texto": texto})

            if capital is None and acto.name in (
                "Ampliación de capital", "Reducción de capital",
                "Constitución", "Desembolso de dividendos pasivos"
            ):
                capital = _parse_importe(texto)

            if domicilio is None:
                if acto.name == "Cambio de domicilio social":
                    domicilio = texto.strip().rstrip('.')
                elif acto.name == "Constitución":
                    domicilio = _parse_domicilio(texto)

    return json.dumps(result, ensure_ascii=False), capital, domicilio


def _download_pdf(url: str, timeout: int = 30):
    """Descarga el PDF a un fichero temporal, devuelve la ruta o None si falla."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; borme-scraper/1.0)"}
        r = requests.get(url, timeout=timeout, headers=headers)
        r.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(r.content)
        tmp.close()
        return tmp.name
    except Exception as e:
        log.warning(f"  Error descargando {url}: {e}")
        return None


def _upsert_persona(cur: sqlite3.Cursor, nombre: str, cache: dict) -> int:
    """Devuelve el id de la persona, insertando si no existe. Usa cache en memoria."""
    nombre = nombre.strip()
    if nombre in cache:
        return cache[nombre]
    cur.execute("SELECT id FROM personas WHERE nombre = ?", (nombre,))
    row = cur.fetchone()
    if row:
        cache[nombre] = row[0]
        return row[0]
    cur.execute("INSERT INTO personas (nombre) VALUES (?)", (nombre,))
    pid = cur.lastrowid
    cache[nombre] = pid
    return pid


def procesar_acto(
    acto_id: str,
    fecha: str,
    url_pdf: str,
    provincia: str,
    cur: sqlite3.Cursor,
    persona_cache: dict,
) -> dict:
    """
    Descarga y parsea UN PDF de BORME-A e inserta los resultados.
    Devuelve stats: {ok, anuncios, personas, ep_rows, error}
    """
    stats = {"ok": False, "anuncios": 0, "personas": 0, "ep_rows": 0, "error": None}

    pdf_path = _download_pdf(url_pdf)
    if not pdf_path:
        stats["error"] = "download_failed"
        return stats

    try:
        parser = PyPDF2Parser(pdf_path)
        try:
            borme = parser.parse()
        except Exception as e:
            stats["error"] = f"parse_error: {e}"
            return stats

        for anuncio in borme.get_anuncios():
            anuncio_id = f"{acto_id}-{anuncio.id}"

            actos_json, capital, domicilio = _actos_to_json(anuncio.actos)

            # borme_anuncios
            cur.execute("""
                INSERT OR REPLACE INTO borme_anuncios
                  (id, acto_id, fecha, empresa, datos_registrales,
                   actos_json, provincia, capital_importe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                anuncio_id,
                acto_id,
                fecha,
                anuncio.empresa,
                anuncio.datos_registrales or None,
                actos_json,
                provincia,
                capital,
            ))

            # empresas
            cur.execute("""
                INSERT OR REPLACE INTO empresas
                  (id_borme, nombre, provincia, fecha, capital, domicilio)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                anuncio_id,
                anuncio.empresa,
                provincia,
                fecha,
                capital,
                domicilio,
            ))

            # personas + empresa_persona
            for acto in anuncio.actos:
                if not isinstance(acto, BormeActoCargo):
                    continue
                for cargo_name, nombres in acto.value.items():
                    for nombre in nombres:
                        nombre = nombre.strip()
                        if not nombre:
                            continue
                        pid = _upsert_persona(cur, nombre, persona_cache)
                        cur.execute("""
                            INSERT OR IGNORE INTO empresa_persona
                              (id_borme, id_persona, cargo, fecha)
                            VALUES (?, ?, ?, ?)
                        """, (anuncio_id, pid, cargo_name, fecha))
                        stats["ep_rows"] += 1
                        stats["personas"] += 1

            stats["anuncios"] += 1

        stats["ok"] = True

    finally:
        os.unlink(pdf_path)

    return stats


def run(
    db_path: str,
    limit: int = None,
    seccion: str = "1",
    limpiar: bool = False,
    dry_run: bool = False,
):
    """
    Punto de entrada principal.

    Para reproceso completo desde cero:
        python borme_parser.py catalog.db --limpiar

    Para incremental (no toca lo ya parseado):
        python borme_parser.py catalog.db

    Parámetros:
      db_path    ruta a catalog.db
      limit      número máximo de actos a procesar (None = todos)
      seccion    "1" = BORME-A (actos mercantiles), "2" = BORME-B
      limpiar    True = vacía las 4 tablas antes de empezar y reprocesa todo
      dry_run    True = descarga y parsea pero NO escribe en la DB
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    cur = con.cursor()

    # ── Limpieza completa ─────────────────────────────────────────────────────
    if limpiar and not dry_run:
        log.warning("LIMPIANDO TABLAS: empresa_persona, borme_anuncios, empresas, personas")
        for tabla in ("empresa_persona", "borme_anuncios", "empresas", "personas"):
            cur.execute(f"DELETE FROM {tabla}")
            log.warning(f"  {tabla}: {cur.rowcount} filas borradas")
        con.commit()
        log.info("Limpieza completada. Empezando desde cero.")

    # ── Decidir qué actos procesar ────────────────────────────────────────────
    if limpiar:
        # Todos los actos de la sección
        query = """
            SELECT id, fecha, url_pdf, titulo
            FROM borme_actos
            WHERE seccion = ?
              AND url_pdf != ''
            ORDER BY fecha
        """
    else:
        # Solo los actos cuyos anuncios NO están todos en borme_anuncios
        # (detección simple: acto_id no aparece en borme_anuncios)
        query = """
            SELECT id, fecha, url_pdf, titulo
            FROM borme_actos
            WHERE seccion = ?
              AND url_pdf != ''
              AND id NOT IN (SELECT DISTINCT acto_id FROM borme_anuncios)
            ORDER BY fecha
        """

    if limit:
        query += f" LIMIT {limit}"

    cur.execute(query, (seccion,))
    actos = cur.fetchall()
    log.info(f"Actos a procesar: {len(actos)}")

    persona_cache: dict = {}
    total_ok = 0
    total_err = 0
    total_anuncios = 0

    for i, (acto_id, fecha, url_pdf, provincia) in enumerate(actos, 1):
        log.info(f"[{i}/{len(actos)}] {acto_id}  {fecha}  {provincia}")

        if dry_run:
            log.info("  (dry-run, saltando escritura)")
            continue

        stats = procesar_acto(
            acto_id=acto_id,
            fecha=fecha,
            url_pdf=url_pdf,
            provincia=provincia,
            cur=cur,
            persona_cache=persona_cache,
        )

        if stats["ok"]:
            total_ok += 1
            total_anuncios += stats["anuncios"]
            log.info(
                f"  OK: {stats['anuncios']} anuncios | "
                f"{stats['ep_rows']} empresa_persona"
            )
        else:
            total_err += 1
            log.warning(f"  ERROR: {stats['error']}")

        # Commit parcial cada 10 actos
        if i % 10 == 0:
            con.commit()
            log.info(f"  >> Commit parcial [{i}]. OK={total_ok} ERR={total_err}")

    con.commit()
    con.close()

    log.info("=" * 60)
    log.info(f"TOTAL OK:       {total_ok}")
    log.info(f"TOTAL ERROR:    {total_err}")
    log.info(f"TOTAL ANUNCIOS: {total_anuncios}")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="BORME Parser -> catalog.db  (basado en bormeparser de PabloCastellano)"
    )
    p.add_argument("db", help="Ruta a catalog.db")
    p.add_argument(
        "--limit", type=int, default=None,
        help="Número máximo de actos a procesar (para pruebas)"
    )
    p.add_argument(
        "--seccion", default="1", choices=["1", "2"],
        help="Sección BORME: 1=A (actos mercantiles), 2=B"
    )
    p.add_argument(
        "--limpiar", action="store_true",
        help=(
            "Vacía borme_anuncios, empresas, personas, empresa_persona "
            "y reprocesa TODO desde cero. "
            "Úsalo cuando no te fíes de lo ya parseado."
        )
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Descarga y parsea pero no escribe nada en la DB"
    )
    args = p.parse_args()

    run(
        db_path=args.db,
        limit=args.limit,
        seccion=args.seccion,
        limpiar=args.limpiar,
        dry_run=args.dry_run,
    )

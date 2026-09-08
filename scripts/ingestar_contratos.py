"""
Descarga e ingesta de contratos públicos en catalog.db
Fuente: Plataforma de Contratación del Estado (PLACE) - Ministerio de Hacienda

Tablas creadas:
  contratos_menores  — contratos menores (< 15.000 € obras / < 5.000 € servicios)
  licitaciones       — licitaciones completas con adjudicatario y NIF

Uso:
  python ingestar_contratos.py
  python ingestar_contratos.py --solo-años 2022,2023,2024
  python ingestar_contratos.py --solo-menores
"""

import sqlite3
import zipfile
import csv
import io
import time
import logging
import argparse
import requests
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/contratos.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("contratos")

DB_PATH    = "data/catalog.db"
DEST_DIR   = Path("data/contratos")
TIMEOUT    = 60
CHUNK      = 256 * 1024

AÑOS = list(range(2012, datetime.now().year + 1))

# URLs de contratos menores por año
def url_menores(año):
    return f"https://contrataciondelestado.es/sindicacion/sindicacion_1044/contratosMenores{año}.zip"

# URL del fichero completo de licitaciones (se actualiza semanalmente)
URL_LICITACIONES = (
    "https://contrataciondelestado.es/sindicacion/sindicacion_1044/"
    "licitacionesPerfilesContratanteCompleto3.zip"
)

# ── Esquema ───────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous   = NORMAL;

CREATE TABLE IF NOT EXISTS contratos_menores (
    id                  TEXT PRIMARY KEY,   -- expediente + adjudicatario hash
    año                 INTEGER,
    num_expediente      TEXT,
    objeto              TEXT,
    tipo_contrato       TEXT,
    cpv                 TEXT,
    organo              TEXT,
    importe_sin_iva     REAL,
    importe_con_iva     REAL,
    fecha_adjudicacion  TEXT,
    adjudicatario       TEXT,
    nif_adjudicatario   TEXT,
    pais                TEXT,
    raw_csv             TEXT,               -- fila original para trazabilidad
    ingesta_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cm_nif      ON contratos_menores(nif_adjudicatario);
CREATE INDEX IF NOT EXISTS idx_cm_adj      ON contratos_menores(adjudicatario);
CREATE INDEX IF NOT EXISTS idx_cm_organo   ON contratos_menores(organo);
CREATE INDEX IF NOT EXISTS idx_cm_fecha    ON contratos_menores(fecha_adjudicacion);
CREATE INDEX IF NOT EXISTS idx_cm_año      ON contratos_menores(año);

CREATE TABLE IF NOT EXISTS licitaciones (
    id                  TEXT PRIMARY KEY,
    num_expediente      TEXT,
    objeto              TEXT,
    tipo_contrato       TEXT,
    cpv                 TEXT,
    organo              TEXT,
    presupuesto_base    REAL,
    valor_estimado      REAL,
    importe_adj         REAL,
    fecha_publicacion   TEXT,
    fecha_adjudicacion  TEXT,
    adjudicatario       TEXT,
    nif_adjudicatario   TEXT,
    estado              TEXT,
    procedimiento       TEXT,
    pais                TEXT,
    raw_csv             TEXT,
    ingesta_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_lic_nif    ON licitaciones(nif_adjudicatario);
CREATE INDEX IF NOT EXISTS idx_lic_adj    ON licitaciones(adjudicatario);
CREATE INDEX IF NOT EXISTS idx_lic_organo ON licitaciones(organo);
CREATE INDEX IF NOT EXISTS idx_lic_fecha  ON licitaciones(fecha_adjudicacion);

CREATE TABLE IF NOT EXISTS contratos_ingesta_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fuente      TEXT,
    filas_total INTEGER,
    filas_ok    INTEGER,
    filas_err   INTEGER,
    bytes       INTEGER,
    ingesta_at  TEXT DEFAULT (datetime('now'))
);
"""

# ── Mapeo de columnas — los CSV de PLACE usan nombres variables por año ────────

# Contratos menores
CM_COLS = {
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

# Licitaciones completas
LIC_COLS = {
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

def _get_val(row: dict, candidatos: list) -> str:
    """Busca el primer candidato que exista en el header del CSV."""
    for c in candidatos:
        if c in row:
            return (row[c] or "").strip()
    return ""


def _float(s: str) -> float | None:
    if not s:
        return None
    s = s.replace(".", "").replace(",", ".").replace("€", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _id(*partes) -> str:
    import hashlib
    return hashlib.md5("|".join(str(p) for p in partes).encode()).hexdigest()[:16]


def descargar(url: str, dest: Path) -> int:
    """Descarga url → dest, devuelve bytes. Salta si ya existe."""
    if dest.exists() and dest.stat().st_size > 1000:
        log.info(f"  Ya existe: {dest.name} ({dest.stat().st_size:,} B)")
        return dest.stat().st_size

    log.info(f"  Descargando: {url}")
    sess = requests.Session()
    sess.headers["User-Agent"] = "Mozilla/5.0 (datos-publicos-investigacion)"

    r = sess.get(url, stream=True, timeout=TIMEOUT)
    if r.status_code == 404:
        log.warning(f"  404 — no disponible: {url}")
        return 0
    r.raise_for_status()

    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(CHUNK):
            f.write(chunk)
            total += len(chunk)
    log.info(f"  Descargado: {dest.name} ({total:,} B)")
    return total


def parsear_csv_zip(zip_path: Path, encoding_intento=("utf-8", "latin-1", "cp1252")):
    """Extrae y parsea el primer CSV dentro de un ZIP. Devuelve lista de dicts."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                log.warning(f"  Sin CSV en {zip_path.name}")
                return []
            nombre_csv = csvs[0]
            log.info(f"  Parseando: {nombre_csv}")
            datos = zf.read(nombre_csv)
    except zipfile.BadZipFile:
        log.error(f"  ZIP corrupto: {zip_path}")
        return []

    for enc in encoding_intento:
        try:
            texto = datos.decode(enc)
            # Detectar separador
            sep = ";" if texto.count(";") > texto.count(",") else ","
            reader = csv.DictReader(io.StringIO(texto), delimiter=sep)
            rows = list(reader)
            log.info(f"  {len(rows):,} filas ({enc}, sep='{sep}')")
            return rows
        except (UnicodeDecodeError, Exception) as e:
            continue

    log.error(f"  No se pudo decodificar {zip_path.name}")
    return []


# ── Ingesta ───────────────────────────────────────────────────────────────────

def ingestar_menores(con: sqlite3.Connection, rows: list, año: int) -> tuple[int, int]:
    ok = err = 0
    batch = []
    for row in rows:
        try:
            adj  = _get_val(row, CM_COLS["adjudicatario"])
            exp  = _get_val(row, CM_COLS["num_expediente"])
            fecha = _get_val(row, CM_COLS["fecha_adjudicacion"])
            batch.append((
                _id(año, exp, adj),
                año,
                exp,
                _get_val(row, CM_COLS["objeto"]),
                _get_val(row, CM_COLS["tipo_contrato"]),
                _get_val(row, CM_COLS["cpv"]),
                _get_val(row, CM_COLS["organo"]),
                _float(_get_val(row, CM_COLS["importe_sin_iva"])),
                _float(_get_val(row, CM_COLS["importe_con_iva"])),
                fecha,
                adj,
                _get_val(row, CM_COLS["nif_adjudicatario"]),
                _get_val(row, CM_COLS["pais"]),
                str(dict(row))[:500],
            ))
            ok += 1
        except Exception as e:
            err += 1
            if err < 5:
                log.warning(f"  Fila ignorada: {e}")

    if batch:
        con.executemany("""
            INSERT OR IGNORE INTO contratos_menores
            (id, año, num_expediente, objeto, tipo_contrato, cpv, organo,
             importe_sin_iva, importe_con_iva, fecha_adjudicacion,
             adjudicatario, nif_adjudicatario, pais, raw_csv)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, batch)
        con.commit()

    return ok, err


def ingestar_licitaciones(con: sqlite3.Connection, rows: list) -> tuple[int, int]:
    ok = err = 0
    batch = []
    for row in rows:
        try:
            adj = _get_val(row, LIC_COLS["adjudicatario"])
            exp = _get_val(row, LIC_COLS["num_expediente"])
            batch.append((
                _id(exp, adj, _get_val(row, LIC_COLS["fecha_adjudicacion"])),
                exp,
                _get_val(row, LIC_COLS["objeto"]),
                _get_val(row, LIC_COLS["tipo_contrato"]),
                _get_val(row, LIC_COLS["cpv"]),
                _get_val(row, LIC_COLS["organo"]),
                _float(_get_val(row, LIC_COLS["presupuesto_base"])),
                _float(_get_val(row, LIC_COLS["valor_estimado"])),
                _float(_get_val(row, LIC_COLS["importe_adj"])),
                _get_val(row, LIC_COLS["fecha_publicacion"]),
                _get_val(row, LIC_COLS["fecha_adjudicacion"]),
                adj,
                _get_val(row, LIC_COLS["nif_adjudicatario"]),
                _get_val(row, LIC_COLS["estado"]),
                _get_val(row, LIC_COLS["procedimiento"]),
                _get_val(row, LIC_COLS["pais"]),
                str(dict(row))[:500],
            ))
            ok += 1
        except Exception as e:
            err += 1
            if err < 5:
                log.warning(f"  Fila ignorada: {e}")

    if batch:
        con.executemany("""
            INSERT OR IGNORE INTO licitaciones
            (id, num_expediente, objeto, tipo_contrato, cpv, organo,
             presupuesto_base, valor_estimado, importe_adj,
             fecha_publicacion, fecha_adjudicacion,
             adjudicatario, nif_adjudicatario, estado, procedimiento, pais, raw_csv)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, batch)
        con.commit()

    return ok, err


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solo-años",    default=None, help="Ej: 2022,2023,2024")
    parser.add_argument("--solo-menores", action="store_true")
    parser.add_argument("--solo-licitaciones", action="store_true")
    args = parser.parse_args()

    años = [int(a) for a in args.solo_años.split(",")] if args.solo_años else AÑOS

    Path("logs").mkdir(exist_ok=True)
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    log.info(f"BD: {DB_PATH}")

    total_menores = total_licitaciones = 0

    # ── Contratos menores por año ─────────────────────────────────────────────
    if not args.solo_licitaciones:
        log.info("\n=== CONTRATOS MENORES ===")
        for año in años:
            url  = url_menores(año)
            dest = DEST_DIR / f"contratos_menores_{año}.zip"

            bytes_dl = descargar(url, dest)
            if bytes_dl == 0 and not dest.exists():
                continue

            rows = parsear_csv_zip(dest)
            if not rows:
                continue

            ok, err = ingestar_menores(con, rows, año)
            total_menores += ok
            log.info(f"  [{año}] ✔ {ok:,} filas insertadas | ✗ {err} errores")

            con.execute("""
                INSERT INTO contratos_ingesta_log (fuente, filas_total, filas_ok, filas_err, bytes)
                VALUES (?, ?, ?, ?, ?)
            """, (f"menores_{año}", len(rows), ok, err, bytes_dl))
            con.commit()
            time.sleep(0.5)

    # ── Licitaciones completas ────────────────────────────────────────────────
    if not args.solo_menores:
        log.info("\n=== LICITACIONES COMPLETAS ===")
        dest_lic = DEST_DIR / "licitaciones_completas.zip"
        bytes_dl = descargar(URL_LICITACIONES, dest_lic)

        if dest_lic.exists():
            rows = parsear_csv_zip(dest_lic)
            if rows:
                ok, err = ingestar_licitaciones(con, rows)
                total_licitaciones = ok
                log.info(f"  ✔ {ok:,} licitaciones insertadas | ✗ {err} errores")
                con.execute("""
                    INSERT INTO contratos_ingesta_log (fuente, filas_total, filas_ok, filas_err, bytes)
                    VALUES (?, ?, ?, ?, ?)
                """, ("licitaciones_completas", len(rows), ok, err, bytes_dl))
                con.commit()

    # ── Resumen final ─────────────────────────────────────────────────────────
    cm  = con.execute("SELECT COUNT(*) FROM contratos_menores").fetchone()[0]
    lic = con.execute("SELECT COUNT(*) FROM licitaciones").fetchone()[0]
    con.close()

    print("\n" + "="*50)
    print(f"  Contratos menores en BD:  {cm:>10,}")
    print(f"  Licitaciones en BD:       {lic:>10,}")
    print(f"  Total registros:          {cm+lic:>10,}")
    print("="*50)
    print("\nListo. Ahora puedes cruzar con BORME:")
    print("  python cruzar_borme_contratos.py")


if __name__ == "__main__":
    main()

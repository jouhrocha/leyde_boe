"""
Capa de base de datos SQLite para datos.gob.es scraper.
"""

import sqlite3
import json
import logging
from pathlib import Path
from contextlib import contextmanager
from config.settings import DB_PATH

log = logging.getLogger("scraper.db")

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous   = NORMAL;
PRAGMA cache_size    = -32000;
PRAGMA temp_store    = MEMORY;
PRAGMA foreign_keys  = ON;

CREATE TABLE IF NOT EXISTS publishers (
    uri         TEXT PRIMARY KEY,
    name        TEXT,
    type        TEXT,
    scraped_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS themes (
    uri         TEXT PRIMARY KEY,
    label_es    TEXT,
    label_en    TEXT,
    sector_id   TEXT
);

CREATE TABLE IF NOT EXISTS datasets (
    id              TEXT PRIMARY KEY,
    uri             TEXT UNIQUE NOT NULL,
    title_es        TEXT,
    title_en        TEXT,
    description_es  TEXT,
    description_en  TEXT,
    publisher_uri   TEXT REFERENCES publishers(uri),
    license         TEXT,
    language        TEXT,
    spatial         TEXT,
    temporal        TEXT,
    issued          TEXT,
    modified        TEXT,
    valid           TEXT,
    accrual         TEXT,
    conforms_to     TEXT,
    references_uri  TEXT,
    keywords        TEXT,
    themes          TEXT,
    scraped_at      TEXT DEFAULT (datetime('now')),
    raw_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_datasets_publisher  ON datasets(publisher_uri);
CREATE INDEX IF NOT EXISTS idx_datasets_modified   ON datasets(modified);
CREATE INDEX IF NOT EXISTS idx_datasets_issued     ON datasets(issued);

CREATE TABLE IF NOT EXISTS distributions (
    id              TEXT PRIMARY KEY,
    uri             TEXT UNIQUE NOT NULL,
    dataset_id      TEXT NOT NULL REFERENCES datasets(id),
    title           TEXT,
    format          TEXT,
    media_type      TEXT,
    byte_size       TEXT,
    access_url      TEXT,
    download_url    TEXT,
    description     TEXT,
    license         TEXT,
    issued          TEXT,
    modified        TEXT,
    scraped_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_distrib_dataset ON distributions(dataset_id);
CREATE INDEX IF NOT EXISTS idx_distrib_format  ON distributions(format);

CREATE TABLE IF NOT EXISTS scrape_progress (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scrape_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    resource    TEXT,
    error       TEXT,
    occurred_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS borme_dias (
    fecha           TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'pending',
    numero          TEXT,
    n_actos         INTEGER DEFAULT 0,
    n_anuncios      INTEGER DEFAULT 0,
    local_path      TEXT,
    error           TEXT,
    intentos        INTEGER DEFAULT 0,
    descargado_at   TEXT,
    encolado_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_borme_status ON borme_dias(status);
CREATE INDEX IF NOT EXISTS idx_borme_fecha  ON borme_dias(fecha);

CREATE TABLE IF NOT EXISTS borme_actos (
    id          TEXT PRIMARY KEY,
    fecha       TEXT NOT NULL,
    seccion     TEXT,
    registro    TEXT,
    titulo      TEXT,
    url_pdf     TEXT,
    url_xml     TEXT,
    url_htm     TEXT,
    xml_status     TEXT DEFAULT 'pending',
    xml_local_path TEXT,
    FOREIGN KEY (fecha) REFERENCES borme_dias(fecha)
);

CREATE INDEX IF NOT EXISTS idx_borme_actos_fecha    ON borme_actos(fecha);
CREATE INDEX IF NOT EXISTS idx_borme_actos_registro ON borme_actos(registro);

CREATE TABLE IF NOT EXISTS borme_anuncios (
    id                TEXT PRIMARY KEY,
    acto_id           TEXT NOT NULL,
    fecha             TEXT NOT NULL,
    empresa           TEXT,
    datos_registrales TEXT,
    actos_json        TEXT,
    FOREIGN KEY (acto_id) REFERENCES borme_actos(id)
);

CREATE INDEX IF NOT EXISTS idx_borme_anuncios_acto    ON borme_anuncios(acto_id);
CREATE INDEX IF NOT EXISTS idx_borme_anuncios_empresa ON borme_anuncios(empresa);
CREATE INDEX IF NOT EXISTS idx_borme_anuncios_fecha   ON borme_anuncios(fecha);

CREATE TABLE IF NOT EXISTS downloaded_files (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    distribution_id      TEXT NOT NULL,
    dataset_id           TEXT NOT NULL,
    url                  TEXT NOT NULL UNIQUE,
    local_path           TEXT,
    format               TEXT,
    byte_size_expected   INTEGER,
    byte_size_actual     INTEGER,
    status               TEXT NOT NULL DEFAULT 'pending',
    error                TEXT,
    sha256               TEXT,
    started_at           TEXT,
    finished_at          TEXT,
    queued_at            TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dlfiles_status  ON downloaded_files(status);
CREATE INDEX IF NOT EXISTS idx_dlfiles_format  ON downloaded_files(format);
CREATE INDEX IF NOT EXISTS idx_dlfiles_dataset ON downloaded_files(dataset_id);

CREATE TABLE IF NOT EXISTS download_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,
    datasets_ok  INTEGER DEFAULT 0,
    datasets_err INTEGER DEFAULT 0,
    distribs_ok  INTEGER DEFAULT 0,
    pages_done   INTEGER DEFAULT 0,
    files_done   INTEGER DEFAULT 0,
    files_err    INTEGER DEFAULT 0,
    bytes_total  INTEGER DEFAULT 0,
    started_at   TEXT,
    updated_at   TEXT DEFAULT (datetime('now'))
);
"""

# Columnas conocidas de download_stats (las que update_stats puede tocar)
_STATS_COLUMNS = {
    "datasets_ok", "datasets_err", "distribs_ok", "pages_done",
    "files_done", "files_err", "bytes_total",
}


class CatalogDB:

    def __init__(self, path: str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_db()

    def _init_db(self):
        with self._conn() as con:
            con.executescript(SCHEMA_SQL)
        log.info(f"Base de datos lista en: {self.path}")

    def _migrate_db(self):
        """
        Migraciones incrementales para BDs creadas con versiones anteriores.
        Añade columnas/tablas que falten sin tocar datos existentes.
        """
        with self._conn() as con:
            # ── download_stats: columnas nuevas ───────────────────────────────
            existing_cols = {
                row[1]
                for row in con.execute(
                    "PRAGMA table_info(download_stats)"
                ).fetchall()
            }
            nuevas = [
                ("files_done",  "INTEGER DEFAULT 0"),
                ("files_err",   "INTEGER DEFAULT 0"),
                ("bytes_total", "INTEGER DEFAULT 0"),
            ]
            for col, typedef in nuevas:
                if col not in existing_cols:
                    con.execute(
                        f"ALTER TABLE download_stats ADD COLUMN {col} {typedef}"
                    )
                    log.info(f"Migración: download_stats.{col} añadida")

            # ── borme_dias y borme_actos (BDs sin BORME) ──────────────────────
            # Ya cubierto por CREATE TABLE IF NOT EXISTS en SCHEMA_SQL,
            # pero lo verificamos explícitamente para loguear.
            tablas = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "borme_dias" not in tablas:
                log.info("Migración: tabla borme_dias creada")
            if "borme_actos" not in tablas:
                log.info("Migración: tabla borme_actos creada")

            # ── borme_actos: columnas xml (BDs antiguas sin estas columnas) ───
            actos_cols = {
                row[1]
                for row in con.execute("PRAGMA table_info(borme_actos)").fetchall()
            }
            nuevas_actos = [
                ("xml_status",     "TEXT DEFAULT 'pending'"),
                ("xml_local_path", "TEXT"),
            ]
            for col, typedef in nuevas_actos:
                if col not in actos_cols:
                    con.execute(
                        f"ALTER TABLE borme_actos ADD COLUMN {col} {typedef}"
                    )
                    log.info(f"Migración: borme_actos.{col} añadida")

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # ── Datasets ──────────────────────────────────────────────────────────────

    def upsert_datasets(self, rows: list[dict]):
        if not rows:
            return
        sql = """
        INSERT OR REPLACE INTO datasets
            (id, uri, title_es, title_en, description_es, description_en,
             publisher_uri, license, language, spatial, temporal, issued,
             modified, valid, accrual, conforms_to, references_uri,
             keywords, themes, raw_json)
        VALUES
            (:id, :uri, :title_es, :title_en, :description_es, :description_en,
             :publisher_uri, :license, :language, :spatial, :temporal, :issued,
             :modified, :valid, :accrual, :conforms_to, :references_uri,
             :keywords, :themes, :raw_json)
        """
        with self._conn() as con:
            con.executemany(sql, rows)
        log.debug(f"Upsert {len(rows)} datasets OK")

    def count_datasets(self) -> int:
        with self._conn() as con:
            return con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]

    def dataset_exists(self, dataset_id: str) -> bool:
        with self._conn() as con:
            r = con.execute(
                "SELECT 1 FROM datasets WHERE id=?", (dataset_id,)
            ).fetchone()
        return r is not None

    # ── Distribuciones ────────────────────────────────────────────────────────

    def upsert_distributions(self, rows: list[dict]):
        if not rows:
            return
        sql = """
        INSERT OR REPLACE INTO distributions
            (id, uri, dataset_id, title, format, media_type, byte_size,
             access_url, download_url, description, license, issued, modified)
        VALUES
            (:id, :uri, :dataset_id, :title, :format, :media_type, :byte_size,
             :access_url, :download_url, :description, :license, :issued, :modified)
        """
        with self._conn() as con:
            con.executemany(sql, rows)

    def count_distributions(self) -> int:
        with self._conn() as con:
            return con.execute("SELECT COUNT(*) FROM distributions").fetchone()[0]

    # ── Publishers ────────────────────────────────────────────────────────────

    def upsert_publisher(self, uri: str, name: str = None):
        with self._conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO publishers (uri, name) VALUES (?,?)",
                (uri, name)
            )

    # ── Progreso ──────────────────────────────────────────────────────────────

    def set_progress(self, key: str, value):
        with self._conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO scrape_progress (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                (key, json.dumps(value))
            )

    def get_progress(self, key: str, default=None):
        with self._conn() as con:
            r = con.execute(
                "SELECT value FROM scrape_progress WHERE key=?", (key,)
            ).fetchone()
        return json.loads(r[0]) if r else default

    # ── Errores ───────────────────────────────────────────────────────────────

    def log_error(self, resource: str, error: str):
        with self._conn() as con:
            con.execute(
                "INSERT INTO scrape_errors (resource, error) VALUES (?,?)",
                (resource, error)
            )

    # ── Stats ─────────────────────────────────────────────────────────────────

    def update_stats(self, session_id: str, **kwargs):
        """
        Incrementa contadores en download_stats.
        Solo toca columnas conocidas (_STATS_COLUMNS); ignora el resto
        (borme_dias_ok, borme_actos_ok, etc. no van aquí).
        """
        # Filtrar solo columnas que existen en la tabla
        filtered = {k: v for k, v in kwargs.items() if k in _STATS_COLUMNS and v}
        sets = ", ".join(f"{k}={k}+{int(v)}" for k, v in filtered.items())

        with self._conn() as con:
            exists = con.execute(
                "SELECT 1 FROM download_stats WHERE session_id=?", (session_id,)
            ).fetchone()
            if not exists:
                con.execute(
                    "INSERT INTO download_stats (session_id, started_at) "
                    "VALUES (?, datetime('now'))",
                    (session_id,)
                )
            if sets:
                con.execute(
                    f"UPDATE download_stats SET {sets}, updated_at=datetime('now') "
                    f"WHERE session_id=?",
                    (session_id,)
                )

    def get_stats(self, session_id: str) -> dict:
        with self._conn() as con:
            r = con.execute(
                "SELECT * FROM download_stats WHERE session_id=?", (session_id,)
            ).fetchone()
        return dict(r) if r else {}

    # ── Ficheros descargados (Fase 3) ─────────────────────────────────────────

    def queue_downloads(self, rows: list[dict]):
        if not rows:
            return
        sql = """
        INSERT OR IGNORE INTO downloaded_files
            (distribution_id, dataset_id, url, format, byte_size_expected, status)
        VALUES
            (:distribution_id, :dataset_id, :url, :format, :byte_size_expected, 'pending')
        """
        with self._conn() as con:
            con.executemany(sql, rows)
        log.debug(f"queue_downloads: {len(rows)} URLs registradas")

    def get_pending_downloads(
        self,
        formatos: list[str] | None = None,
        limit: int = 500,
    ) -> list[dict]:
        params: list = []
        where = "status = 'pending'"
        if formatos:
            placeholders = ",".join("?" * len(formatos))
            where += f" AND UPPER(format) IN ({placeholders})"
            params.extend(f.upper() for f in formatos)
        sql = f"SELECT * FROM downloaded_files WHERE {where} LIMIT ?"
        params.append(limit)
        with self._conn() as con:
            rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def mark_download_start(self, row_id: int):
        with self._conn() as con:
            con.execute(
                "UPDATE downloaded_files SET status='downloading', "
                "started_at=datetime('now') WHERE id=?",
                (row_id,)
            )

    def mark_download_done(self, row_id: int, local_path: str,
                           byte_size: int, sha256: str):
        with self._conn() as con:
            con.execute(
                "UPDATE downloaded_files SET status='done', local_path=?, "
                "byte_size_actual=?, sha256=?, finished_at=datetime('now') "
                "WHERE id=?",
                (local_path, byte_size, sha256, row_id)
            )

    def mark_download_failed(self, row_id: int, error: str):
        with self._conn() as con:
            con.execute(
                "UPDATE downloaded_files SET status='failed', error=?, "
                "finished_at=datetime('now') WHERE id=?",
                (error, row_id)
            )

    def mark_download_skipped(self, row_id: int, reason: str = "ya existe"):
        with self._conn() as con:
            con.execute(
                "UPDATE downloaded_files SET status='skipped', error=? WHERE id=?",
                (reason, row_id)
            )

    def reset_stale_downloads(self):
        with self._conn() as con:
            n = con.execute(
                "UPDATE downloaded_files SET status='pending' WHERE status='downloading'"
            ).rowcount
        if n:
            log.info(f"Reset {n} descargas interrumpidas → pending")

    def reset_failed_downloads(self):
        with self._conn() as con:
            n = con.execute(
                "UPDATE downloaded_files SET status='pending', error=NULL "
                "WHERE status='failed'"
            ).rowcount
        log.info(f"Reset {n} descargas fallidas → pending")

    def count_downloads_by_status(self) -> dict:
        with self._conn() as con:
            rows = con.execute(
                "SELECT status, COUNT(*) AS n FROM downloaded_files GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def pending_downloads_count(self, formatos: list[str] | None = None) -> int:
        params: list = []
        where = "status='pending'"
        if formatos:
            placeholders = ",".join("?" * len(formatos))
            where += f" AND UPPER(format) IN ({placeholders})"
            params.extend(f.upper() for f in formatos)
        with self._conn() as con:
            return con.execute(
                f"SELECT COUNT(*) FROM downloaded_files WHERE {where}", params
            ).fetchone()[0]

    def get_distributions_with_url(
        self,
        formatos: list[str] | None = None,
        limit: int = 10_000,
    ) -> list[dict]:
        params: list = []
        where = "download_url IS NOT NULL AND download_url != ''"
        if formatos:
            placeholders = ",".join("?" * len(formatos))
            where += f" AND UPPER(format) IN ({placeholders})"
            params.extend(f.upper() for f in formatos)
        sql = f"""
        SELECT id AS distribution_id, dataset_id, download_url AS url,
               format, byte_size AS byte_size_expected
        FROM distributions WHERE {where} LIMIT ?
        """
        params.append(limit)
        with self._conn() as con:
            rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── BORME ─────────────────────────────────────────────────────────────────

    def borme_seed_dias(self, fechas: list[str]):
        if not fechas:
            return
        with self._conn() as con:
            con.executemany(
                "INSERT OR IGNORE INTO borme_dias (fecha, status) VALUES (?, 'pending')",
                [(f,) for f in fechas]
            )
        log.debug(f"borme_seed_dias: {len(fechas)} días registrados")

    def borme_get_pending(self, limit: int = 1000) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM borme_dias WHERE status='pending' ORDER BY fecha LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def borme_mark_start(self, fecha: str):
        with self._conn() as con:
            con.execute(
                "UPDATE borme_dias SET status='downloading', intentos=intentos+1 WHERE fecha=?",
                (fecha,)
            )

    def borme_mark_done(self, fecha: str, numero: str, n_actos: int,
                        n_anuncios: int, local_path: str):
        with self._conn() as con:
            con.execute(
                "UPDATE borme_dias SET status='done', numero=?, n_actos=?, "
                "n_anuncios=?, local_path=?, descargado_at=datetime('now') WHERE fecha=?",
                (numero, n_actos, n_anuncios, local_path, fecha)
            )

    def borme_mark_failed(self, fecha: str, error: str):
        with self._conn() as con:
            con.execute(
                "UPDATE borme_dias SET status='failed', error=? WHERE fecha=?",
                (error, fecha)
            )

    def borme_mark_sin_borme(self, fecha: str):
        with self._conn() as con:
            con.execute(
                "UPDATE borme_dias SET status='sin_borme' WHERE fecha=?",
                (fecha,)
            )

    def borme_reset_stale(self):
        with self._conn() as con:
            n = con.execute(
                "UPDATE borme_dias SET status='pending' WHERE status='downloading'"
            ).rowcount
        if n:
            log.info(f"Reset {n} días BORME interrumpidos → pending")

    def borme_reset_failed(self):
        with self._conn() as con:
            n = con.execute(
                "UPDATE borme_dias SET status='pending', error=NULL WHERE status='failed'"
            ).rowcount
        log.info(f"Reset {n} días BORME fallidos → pending")

    def borme_upsert_actos(self, actos: list[dict]):
        if not actos:
            return
        sql = """
        INSERT OR REPLACE INTO borme_actos
            (id, fecha, seccion, registro, titulo, url_pdf, url_xml, url_htm)
        VALUES
            (:id, :fecha, :seccion, :registro, :titulo, :url_pdf, :url_xml, :url_htm)
        """
        with self._conn() as con:
            con.executemany(sql, actos)

    # ── BORME XML ─────────────────────────────────────────────────────────────

    def borme_get_actos_pending_xml(self, limit: int = 1000) -> list[dict]:
        """Devuelve actos de sección A cuyo XML no se ha descargado aún."""
        with self._conn() as con:
            rows = con.execute(
                """
                SELECT id, fecha, url_xml, url_pdf
                FROM borme_actos
                WHERE seccion = '1'
                  AND (xml_status IS NULL OR xml_status IN ('pending', 'failed'))
                ORDER BY fecha
                LIMIT ?
                """,
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def borme_pending_xml_count(self) -> int:
        with self._conn() as con:
            return con.execute(
                """
                SELECT COUNT(*) FROM borme_actos
                WHERE seccion = '1'
                  AND (xml_status IS NULL OR xml_status IN ('pending', 'failed'))
                """
            ).fetchone()[0]

    def borme_mark_xml_start(self, acto_id: str):
        with self._conn() as con:
            con.execute(
                "UPDATE borme_actos SET xml_status='downloading' WHERE id=?",
                (acto_id,)
            )

    def borme_mark_xml_done(self, acto_id: str, local_path: str):
        with self._conn() as con:
            con.execute(
                """
                UPDATE borme_actos
                SET xml_status='done', xml_local_path=?
                WHERE id=?
                """,
                (local_path, acto_id)
            )

    def borme_mark_xml_failed(self, acto_id: str, error: str):
        with self._conn() as con:
            con.execute(
                "UPDATE borme_actos SET xml_status='failed' WHERE id=?",
                (acto_id,)
            )
        log.warning(f"XML fallido {acto_id}: {error}")

    def borme_mark_xml_sin_contenido(self, acto_id: str):
        """El BOE devolvió 404 para este identificador."""
        with self._conn() as con:
            con.execute(
                "UPDATE borme_actos SET xml_status='sin_xml' WHERE id=?",
                (acto_id,)
            )

    def borme_reset_stale_xml(self):
        """Resetea XMLs que quedaron en 'downloading' por crash."""
        with self._conn() as con:
            n = con.execute(
                "UPDATE borme_actos SET xml_status='pending' WHERE xml_status='downloading'"
            ).rowcount
        if n:
            log.info(f"Reset {n} XMLs interrumpidos → pending")

    def borme_upsert_anuncios(self, anuncios: list[dict]):
        """
        Inserta o reemplaza los anuncios parseados del XML.
        Cada dict: id, acto_id, fecha, empresa, datos_registrales, actos_json.
        """
        if not anuncios:
            return
        sql = """
        INSERT OR REPLACE INTO borme_anuncios
            (id, acto_id, fecha, empresa, datos_registrales, actos_json)
        VALUES
            (:id, :acto_id, :fecha, :empresa, :datos_registrales, :actos_json)
        """
        with self._conn() as con:
            con.executemany(sql, anuncios)

    def borme_stats(self) -> dict:
        with self._conn() as con:
            by_status = {
                r["status"]: r["n"]
                for r in con.execute(
                    "SELECT status, COUNT(*) AS n FROM borme_dias GROUP BY status"
                ).fetchall()
            }
            n_actos = con.execute("SELECT COUNT(*) FROM borme_actos").fetchone()[0]
            total_dias = con.execute("SELECT COUNT(*) FROM borme_dias").fetchone()[0]
        return {
            "total_dias":  total_dias,
            "por_estado":  by_status,
            "actos_total": n_actos,
        }

    def borme_pending_count(self) -> int:
        with self._conn() as con:
            return con.execute(
                "SELECT COUNT(*) FROM borme_dias WHERE status='pending'"
            ).fetchone()[0]

    # ── Resumen general ───────────────────────────────────────────────────────

    def summary(self) -> dict:
        with self._conn() as con:
            ds  = con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
            dis = con.execute("SELECT COUNT(*) FROM distributions").fetchone()[0]
            pub = con.execute("SELECT COUNT(*) FROM publishers").fetchone()[0]
            err = con.execute("SELECT COUNT(*) FROM scrape_errors").fetchone()[0]
            fmts = con.execute(
                "SELECT format, COUNT(*) AS n FROM distributions "
                "GROUP BY format ORDER BY n DESC LIMIT 10"
            ).fetchall()
            dl_stats = con.execute(
                "SELECT status, COUNT(*) AS n FROM downloaded_files GROUP BY status"
            ).fetchall()
        return {
            "datasets":      ds,
            "distributions": dis,
            "publishers":    pub,
            "errors":        err,
            "top_formats":   [dict(r) for r in fmts],
            "downloads":     {r["status"]: r["n"] for r in dl_stats},
        }

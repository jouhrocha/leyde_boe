# ═══════════════════════════════════════════════════════════════════════════════
# AÑADIR al final de SCHEMA_SQL (dentro de las triples comillas, antes del cierre)
# ═══════════════════════════════════════════════════════════════════════════════

CONTRATOS_SCHEMA = """
CREATE TABLE IF NOT EXISTS contratos_menores (
    id                  TEXT PRIMARY KEY,
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
    raw_csv             TEXT,
    ingesta_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cm_nif    ON contratos_menores(nif_adjudicatario);
CREATE INDEX IF NOT EXISTS idx_cm_adj    ON contratos_menores(adjudicatario);
CREATE INDEX IF NOT EXISTS idx_cm_organo ON contratos_menores(organo);
CREATE INDEX IF NOT EXISTS idx_cm_fecha  ON contratos_menores(fecha_adjudicacion);
CREATE INDEX IF NOT EXISTS idx_cm_año    ON contratos_menores(año);

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
    fuente      TEXT NOT NULL,
    filas_total INTEGER DEFAULT 0,
    filas_ok    INTEGER DEFAULT 0,
    filas_err   INTEGER DEFAULT 0,
    bytes       INTEGER DEFAULT 0,
    ingesta_at  TEXT DEFAULT (datetime('now'))
);
"""

# ═══════════════════════════════════════════════════════════════════════════════
# AÑADIR estos métodos dentro de la clase CatalogDB
# (pegar justo antes del último método, summary())
# ═══════════════════════════════════════════════════════════════════════════════

"""
    # ── Contratos públicos (PLACE) ────────────────────────────────────────────

    def contratos_init(self):
        \"\"\"Crea las tablas de contratos si no existen (idempotente).\"\"\"
        with self._conn() as con:
            con.executescript(CONTRATOS_SCHEMA)
        log.info("Tablas de contratos listas")

    def contratos_menores_upsert(self, batch: list[tuple]) -> int:
        \"\"\"
        Inserta filas de contratos_menores.
        batch: lista de tuplas en el orden del INSERT (ver core/contratos.py).
        Devuelve número de filas insertadas.
        \"\"\"
        if not batch:
            return 0
        with self._conn() as con:
            con.executemany(\"\"\"
                INSERT OR IGNORE INTO contratos_menores
                (id, año, num_expediente, objeto, tipo_contrato, cpv, organo,
                 importe_sin_iva, importe_con_iva, fecha_adjudicacion,
                 adjudicatario, nif_adjudicatario, pais, raw_csv)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            \"\"\", batch)
        return len(batch)

    def licitaciones_upsert(self, batch: list[tuple]) -> int:
        \"\"\"
        Inserta filas de licitaciones.
        Devuelve número de filas insertadas.
        \"\"\"
        if not batch:
            return 0
        with self._conn() as con:
            con.executemany(\"\"\"
                INSERT OR IGNORE INTO licitaciones
                (id, num_expediente, objeto, tipo_contrato, cpv, organo,
                 presupuesto_base, valor_estimado, importe_adj,
                 fecha_publicacion, fecha_adjudicacion,
                 adjudicatario, nif_adjudicatario, estado, procedimiento,
                 pais, raw_csv)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            \"\"\", batch)
        return len(batch)

    def contratos_log_ingesta(self, fuente: str, total: int,
                               ok: int, err: int, bytes_dl: int):
        \"\"\"Registra el resultado de cada descarga en el log de ingesta.\"\"\"
        with self._conn() as con:
            con.execute(\"\"\"
                INSERT INTO contratos_ingesta_log
                (fuente, filas_total, filas_ok, filas_err, bytes)
                VALUES (?,?,?,?,?)
            \"\"\", (fuente, total, ok, err, bytes_dl))

    def contratos_año_ya_ingestado(self, año: int) -> bool:
        \"\"\"True si ya hay filas de ese año en contratos_menores.\"\"\"
        with self._conn() as con:
            n = con.execute(
                \"SELECT COUNT(*) FROM contratos_menores WHERE año=? LIMIT 1\",
                (año,)
            ).fetchone()[0]
        return n > 0

    def contratos_stats(self) -> dict:
        \"\"\"Resumen rápido de las tablas de contratos.\"\"\"
        with self._conn() as con:
            cm  = con.execute(\"SELECT COUNT(*) FROM contratos_menores\").fetchone()[0]
            lic = con.execute(\"SELECT COUNT(*) FROM licitaciones\").fetchone()[0]
            años = con.execute(
                \"SELECT año, COUNT(*) n FROM contratos_menores GROUP BY año ORDER BY año\"
            ).fetchall()
        return {
            \"contratos_menores\": cm,
            \"licitaciones\":      lic,
            \"por_año\":           {r[0]: r[1] for r in años},
        }
"""

#!/usr/bin/env python3
"""
RECUPERAR DB v2 — Solo Python, sin sqlite3.exe
===============================================
Usa la técnica de copiar tabla por tabla con SELECT directo,
saltando las filas corruptas individualmente.

Uso:
    python recuperar_db_v2.py
    python recuperar_db_v2.py --db registro_mercantil.db
"""

import sqlite3
import shutil
import os
import sys
import argparse
from datetime import datetime


def recuperar(db_path: str):
    if not os.path.exists(db_path):
        print(f"❌ No existe: {db_path}")
        sys.exit(1)

    tam_mb = os.path.getsize(db_path) / 1_048_576
    print(f"\n📁 Base de datos: {db_path} ({tam_mb:.1f} MB)")

    # Backup
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{db_path}.backup_{ts}"
    if not os.path.exists(backup):
        shutil.copy2(db_path, backup)
        print(f"✅ Backup: {backup}")
    else:
        print(f"ℹ️  Backup ya existe: {backup}")

    db_clean = db_path + ".clean.db"
    if os.path.exists(db_clean):
        os.remove(db_clean)

    print(f"\n🔧 Abriendo BD corrupta en modo solo-lectura...")
    try:
        # uri=True permite abrir en modo read-only aunque esté corrupta
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        src.execute("PRAGMA journal_mode=OFF")
    except Exception as e:
        print(f"❌ No se puede abrir: {e}")
        sys.exit(1)

    dst = sqlite3.connect(db_clean)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=OFF")
    dst.execute("PRAGMA cache_size=-131072")

    # ── Esquema ──────────────────────────────────────────────────────────────
    print("📋 Copiando esquema...")
    dst.executescript("""
        CREATE TABLE IF NOT EXISTS raw_descarga (
            borme_id      TEXT PRIMARY KEY,
            fecha         TEXT NOT NULL,
            seccion       TEXT,
            subseccion    TEXT,
            provincia     TEXT,
            titulo        TEXT,
            url_pdf       TEXT,
            url_html      TEXT,
            html_ok       INTEGER DEFAULT 0,
            pdf_ok        INTEGER DEFAULT 0,
            html_content  TEXT,
            pdf_content   BLOB,
            n_total_dia   INTEGER DEFAULT 0,
            descargado_en TEXT,
            parseado      INTEGER DEFAULT 0,
            parseado_en   TEXT
        );
        CREATE TABLE IF NOT EXISTS entradas (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            borme_id          TEXT,
            fecha             TEXT,
            seccion           TEXT,
            subseccion        TEXT,
            provincia         TEXT,
            empresa           TEXT,
            nif               TEXT,
            num_registro      TEXT DEFAULT '',
            actos             TEXT,
            datos_registrales TEXT,
            texto             TEXT,
            url               TEXT,
            fuente            TEXT DEFAULT 'html',
            UNIQUE(borme_id, empresa)
        );
        CREATE TABLE IF NOT EXISTS fechas_procesadas (
            fecha         TEXT PRIMARY KEY,
            n_documentos  INTEGER DEFAULT 0,
            n_descargados INTEGER DEFAULT 0,
            n_insertados  INTEGER DEFAULT 0,
            procesada_en  TEXT
        );
    """)
    dst.commit()
    print("✅ Esquema creado")

    # ── Copiar tabla por tabla ────────────────────────────────────────────────

    def copiar_tabla(nombre, columnas, pk_col=None):
        cols_str  = ", ".join(columnas)
        marks_str = ", ".join("?" * len(columnas))
        insert_sql = f"INSERT OR IGNORE INTO {nombre} ({cols_str}) VALUES ({marks_str})"

        print(f"\n📦 Copiando {nombre}...")
        ok = 0; skip = 0; batch = []

        try:
            # Intentar con rowid para poder iterar incluso con índices rotos
            cur = src.execute(f"SELECT rowid, {cols_str} FROM {nombre} ORDER BY rowid")
        except Exception as e:
            print(f"  ⚠️  No se puede leer {nombre}: {e}")
            return 0

        while True:
            try:
                rows = cur.fetchmany(500)
                if not rows:
                    break
                for row in rows:
                    vals = row[1:]  # quitar rowid
                    try:
                        batch.append(vals)
                        ok += 1
                    except Exception:
                        skip += 1

                if batch:
                    try:
                        dst.executemany(insert_sql, batch)
                        dst.commit()
                    except Exception:
                        # Si falla el batch entero, insertar uno a uno
                        for v in batch:
                            try:
                                dst.execute(insert_sql, v)
                            except Exception:
                                skip += 1
                                ok   -= 1
                        dst.commit()
                    batch = []

                sys.stdout.write(f"\r  {ok:,} copiadas, {skip:,} saltadas...")
                sys.stdout.flush()

            except sqlite3.DatabaseError as e:
                # Página corrupta: saltar bloque y continuar
                skip += 500
                sys.stdout.write(f"\r  ⚠️ página corrupta, saltando... {ok:,} ok")
                sys.stdout.flush()
                continue
            except StopIteration:
                break

        if batch:
            try:
                dst.executemany(insert_sql, batch)
                dst.commit()
            except Exception:
                for v in batch:
                    try:
                        dst.execute(insert_sql, v)
                    except Exception:
                        skip += 1

        dst.commit()
        print(f"\n  ✅ {ok:,} filas recuperadas, {skip:,} saltadas")
        return ok

    # Orden importante: primero las tablas sin FKs
    copiar_tabla("fechas_procesadas",
        ["fecha","n_documentos","n_descargados","n_insertados","procesada_en"])

    copiar_tabla("raw_descarga",
        ["borme_id","fecha","seccion","subseccion","provincia","titulo",
         "url_pdf","url_html","html_ok","pdf_ok","html_content","pdf_content",
         "n_total_dia","descargado_en","parseado","parseado_en"])

    copiar_tabla("entradas",
        ["borme_id","fecha","seccion","subseccion","provincia","empresa","nif",
         "num_registro","actos","datos_registrales","texto","url","fuente"])

    src.close()

    # ── Reconstruir índices ───────────────────────────────────────────────────
    print("\n🔧 Reconstruyendo índices...")
    dst.executescript("""
        CREATE INDEX IF NOT EXISTS rdx_fecha    ON raw_descarga(fecha);
        CREATE INDEX IF NOT EXISTS rdx_parseado ON raw_descarga(parseado, fecha);
        CREATE INDEX IF NOT EXISTS idx_fecha     ON entradas(fecha);
        CREATE INDEX IF NOT EXISTS idx_empresa   ON entradas(empresa);
        CREATE INDEX IF NOT EXISTS idx_nif       ON entradas(nif);
        CREATE INDEX IF NOT EXISTS idx_provincia ON entradas(provincia);
        CREATE INDEX IF NOT EXISTS idx_borme_id  ON entradas(borme_id);
    """)
    dst.commit()

    # ── Stats finales ─────────────────────────────────────────────────────────
    r1 = dst.execute("SELECT COUNT(*) FROM entradas").fetchone()[0]
    r2 = dst.execute("SELECT COUNT(*) FROM raw_descarga").fetchone()[0]
    r3 = dst.execute("SELECT COUNT(*) FROM fechas_procesadas").fetchone()[0]
    r4 = dst.execute("SELECT MIN(fecha), MAX(fecha) FROM fechas_procesadas").fetchone()

    print(f"\n📊 Resultado:")
    print(f"   entradas:          {r1:,}")
    print(f"   raw_descarga:      {r2:,}")
    print(f"   fechas_procesadas: {r3:,}  ({r4[0]} → {r4[1]})")

    dst.execute("PRAGMA wal_checkpoint(FULL)")
    dst.close()

    tam_clean = os.path.getsize(db_clean) / 1_048_576
    print(f"   Tamaño BD limpia:  {tam_clean:.1f} MB")

    # ── Reemplazar ────────────────────────────────────────────────────────────
    print(f"\n🔄 Reemplazando BD corrupta...")
    corrupta = db_path + ".corrupta"
    os.replace(db_path, corrupta)
    os.replace(db_clean, db_path)

    print(f"✅ ¡Listo!")
    print(f"   BD recuperada: {db_path}")
    print(f"   BD corrupta:   {corrupta}  (puedes borrarla)")
    print(f"   Backup:        {backup}")
    print(f"\n▶  Ahora ejecuta: python scraper.py --runpod")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="registro_mercantil.db")
    args = parser.parse_args()
    recuperar(args.db)


if __name__ == "__main__":
    main()

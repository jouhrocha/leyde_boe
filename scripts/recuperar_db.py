#!/usr/bin/env python3
"""
RECUPERAR DB CORRUPTA
=====================
Ejecutar ANTES de volver a lanzar el scraper.

Uso:
    python recuperar_db.py
    python recuperar_db.py --db otro_nombre.db
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

    # 1. Backup antes de tocar nada
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup  = f"{db_path}.backup_{ts}"
    shutil.copy2(db_path, backup)
    print(f"✅ Backup creado: {backup}")

    # 2. Intentar PRAGMA integrity_check primero
    print("\n🔍 Comprobando integridad...")
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=DELETE")   # cerrar WAL primero
        conn.commit()
        result = conn.execute("PRAGMA integrity_check(100)").fetchall()
        conn.close()
        errores = [r[0] for r in result if r[0] != "ok"]
        if not errores:
            print("✅ La BD está OK — no necesita recuperación")
            print("   (El error puede ser del WAL. Ya se cerró correctamente.)")
            print(f"\n▶  Vuelve a ejecutar el scraper normalmente.")
            return
        print(f"⚠️  {len(errores)} errores encontrados:")
        for e in errores[:10]:
            print(f"   · {e}")
    except Exception as ex:
        print(f"⚠️  No se pudo abrir la BD directamente: {ex}")

    # 3. Recuperación: volcar con .dump y reimportar
    print("\n🔧 Recuperando con sqlite3 .dump → nueva BD...")
    db_recovered = db_path + ".recovered.db"

    try:
        # Abrir con modo recovery (ignora páginas corruptas)
        conn_src = sqlite3.connect(db_path)
        conn_src.execute("PRAGMA recover=ON")

        conn_dst = sqlite3.connect(db_recovered)
        # Copiar datos página a página con recover
        for line in conn_src.iterdump():
            try:
                conn_dst.execute(line)
            except sqlite3.Error:
                pass  # saltar filas corruptas individuales

        conn_dst.commit()

        # Verificar la nueva BD
        r = conn_dst.execute("SELECT COUNT(*) FROM entradas").fetchone()
        entradas = r[0] if r else 0
        r2 = conn_dst.execute("SELECT COUNT(*) FROM raw_descarga").fetchone()
        raw = r2[0] if r2 else 0
        r3 = conn_dst.execute("SELECT COUNT(*) FROM fechas_procesadas").fetchone()
        fechas = r3[0] if r3 else 0

        conn_src.close()
        conn_dst.close()

        print(f"\n✅ Recuperación completada:")
        print(f"   · entradas:          {entradas:,}")
        print(f"   · raw_descarga:      {raw:,}")
        print(f"   · fechas_procesadas: {fechas:,}")

        # Reemplazar la original
        os.replace(db_recovered, db_path)
        print(f"\n✅ {db_path} reemplazada con la versión recuperada")
        print(f"   Backup original en: {backup}")

        # Reconstruir índices y VACUUM
        print("\n🔧 Reconstruyendo índices y haciendo VACUUM...")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("REINDEX")
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
        conn.close()
        print("✅ REINDEX + VACUUM completados")

        tam_final = os.path.getsize(db_path) / 1_048_576
        print(f"\n📁 Tamaño final: {tam_final:.1f} MB")
        print(f"\n▶  Ahora puedes volver a ejecutar el scraper.")

    except Exception as ex:
        print(f"\n❌ Error durante recuperación: {ex}")
        print("\n💡 Intento alternativo: recuperación con sqlite3 CLI")
        print(f'   sqlite3 {db_path} ".recover" | sqlite3 {db_path}.clean.db')
        print(f"   (Requiere sqlite3 >= 3.29.0)")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="registro_mercantil.db")
    args = parser.parse_args()
    recuperar(args.db)

if __name__ == "__main__":
    main()

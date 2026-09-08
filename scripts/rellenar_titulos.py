#!/usr/bin/env python3
"""
Rellena title_es, description_es y keywords en la tabla datasets
extrayéndolos del campo raw_json.

    python rellenar_titulos.py
"""
import sqlite3, json
from pathlib import Path

DB_PATH = Path("data/catalog.db")

def get_lang(lst, lang="es"):
    """Extrae el valor de una lista [{_value:..., _lang:...}] para el idioma dado."""
    if not lst:
        return None
    if isinstance(lst, str):
        return lst
    # Primero busca el idioma pedido
    for item in lst:
        if isinstance(item, dict) and item.get("_lang") == lang:
            return item.get("_value")
    # Fallback: primer elemento que tenga _value
    for item in lst:
        if isinstance(item, dict) and "_value" in item:
            return item.get("_value")
    return None

def main():
    con = sqlite3.connect(DB_PATH, timeout=30)
    total = con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
    print(f"Procesando {total} datasets...")

    rows = con.execute("SELECT id, raw_json FROM datasets WHERE raw_json IS NOT NULL").fetchall()
    updated = 0

    for ds_id, raw in rows:
        try:
            d = json.loads(raw)
        except Exception:
            continue

        title_es      = get_lang(d.get("title"), "es")
        title_en      = get_lang(d.get("title"), "en")
        desc_es       = get_lang(d.get("description"), "es")
        desc_en       = get_lang(d.get("description"), "en")

        # keywords puede ser lista de strings o lista de dicts
        kw_raw = d.get("keyword", [])
        if isinstance(kw_raw, list):
            keywords = ", ".join(
                k if isinstance(k, str) else k.get("_value", "")
                for k in kw_raw
            )
        else:
            keywords = str(kw_raw) if kw_raw else None

        con.execute("""
            UPDATE datasets
            SET title_es=?, title_en=?, description_es=?, description_en=?, keywords=?
            WHERE id=?
        """, (title_es, title_en, desc_es, desc_en, keywords or None, ds_id))
        updated += 1

        if updated % 100 == 0:
            con.commit()
            print(f"  {updated}/{len(rows)}...", end="\r")

    con.commit()
    con.close()
    print(f"\nListo. {updated} datasets actualizados.")

if __name__ == "__main__":
    main()

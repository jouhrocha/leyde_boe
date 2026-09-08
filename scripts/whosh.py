#!/usr/bin/env python3
"""
Script de diagnóstico del índice Whoosh.
Ejecútalo desde la carpeta raíz de tu proyecto Flask con:
    python diagnostico_whoosh.py
"""

import os, json

# ── 1. Localizar el índice ──────────────────────────────────────────────────
POSIBLES = ["indice_whoosh", "whoosh_index", "index", "boe_index", "indices"]
carpeta = None
for p in POSIBLES:
    if os.path.isdir(p):
        carpeta = p
        break

if carpeta is None:
    print("❌  No se encontró la carpeta del índice.")
    print("    Carpetas disponibles aquí:", [d for d in os.listdir(".") if os.path.isdir(d)])
    exit(1)

print(f"✅  Índice encontrado en: '{carpeta}'\n")
print("📂  Archivos dentro:")
for f in os.listdir(carpeta):
    print(f"     {f}")
print()

# ── 2. Abrir el índice ─────────────────────────────────────────────────────
try:
    from whoosh.index import open_dir
    from whoosh import query as wq
    ix = open_dir(carpeta)
except Exception as e:
    print(f"❌  Error abriendo el índice: {e}")
    exit(1)

# ── 3. Schema ──────────────────────────────────────────────────────────────
print("=" * 60)
print("📋  SCHEMA (campos del índice)")
print("=" * 60)
schema = ix.schema
for nombre, campo in schema.items():
    print(f"   • {nombre:25s} → {type(campo).__name__}")
print()

# ── 4. Total de documentos ─────────────────────────────────────────────────
with ix.searcher() as s:
    total = s.doc_count()
    print(f"📊  Total de documentos indexados: {total:,}\n")

    # ── 5. Tres documentos de ejemplo ─────────────────────────────────────
    print("=" * 60)
    print("🔍  EJEMPLO — primeros 3 documentos")
    print("=" * 60)
    results = s.search(wq.Every(), limit=3)
    ejemplos = []
    for i, r in enumerate(results, 1):
        doc = dict(r)
        ejemplos.append(doc)
        print(f"\n--- Documento {i} ---")
        for k, v in doc.items():
            val = str(v)
            print(f"   {k:25s}: {val[:120]}")
    print()

    # ── 6. Búsqueda de prueba ──────────────────────────────────────────────
    print("=" * 60)
    print("🧪  PRUEBA DE BÚSQUEDA — término 'arrendamiento'")
    print("=" * 60)
    try:
        from whoosh.qparser import MultifieldParser, QueryParser
        campos_texto = [n for n, c in schema.items()
                        if type(c).__name__ in ("TEXT", "NGRAMWORDS", "NGRAM")]
        print(f"   Campos de texto detectados: {campos_texto}")
        if campos_texto:
            parser = MultifieldParser(campos_texto, schema=schema)
            q = parser.parse("arrendamiento")
            res = s.search(q, limit=5)
            print(f"   Resultados encontrados: {len(res)}")
            for r in res:
                campos_muestra = list(dict(r).items())[:3]
                print(f"   → {campos_muestra}")
        else:
            print("   ⚠️  No se detectaron campos de texto libre")
    except Exception as e:
        print(f"   ⚠️  Error en búsqueda de prueba: {e}")

# ── 7. Guardar resumen en JSON ─────────────────────────────────────────────
resumen = {
    "carpeta_indice": carpeta,
    "total_documentos": total,
    "campos": {n: type(c).__name__ for n, c in schema.items()},
    "ejemplo_documentos": [
        {k: str(v)[:200] for k, v in doc.items()} for doc in ejemplos
    ]
}
with open("diagnostico_resultado.json", "w", encoding="utf-8") as f:
    json.dump(resumen, f, ensure_ascii=False, indent=2)

print("\n" + "=" * 60)
print("✅  Diagnóstico completo.")
print("📄  Resultado guardado en: diagnostico_resultado.json")
print("    → Pega aquí el contenido de ese archivo o la salida de este script.")
print("=" * 60)

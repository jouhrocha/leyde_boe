"""
boe_search.py  –  Añade estas rutas a tu app Flask existente
o inclúyelo como Blueprint.

Requiere: whoosh (ya instalado en tu entorno Flask)
"""

from flask import Blueprint, request, jsonify, render_template_string, send_from_directory
from whoosh.index import open_dir
from whoosh.qparser import MultifieldParser, OrGroup
from whoosh.query import Every
import os, re

boe_bp = Blueprint("boe", __name__)

# ── Abre el índice UNA sola vez al arrancar ───────────────────────────────
INDEX_DIR = os.path.join(os.path.dirname(__file__), "indice_whoosh")
_ix = None

def get_index():
    global _ix
    if _ix is None:
        _ix = open_dir(INDEX_DIR)
    return _ix


# ── /api/buscar  ──────────────────────────────────────────────────────────
@boe_bp.route("/api/buscar")
def buscar():
    q_raw = request.args.get("q", "").strip()
    limit  = min(int(request.args.get("n", 8)), 20)

    if len(q_raw) < 2:
        return jsonify([])

    ix = get_index()
    with ix.searcher() as s:
        schema = ix.schema
        parser = MultifieldParser(
            ["ley", "articulo", "texto"],
            schema=schema,
            group=OrGroup
        )
        query = parser.parse(q_raw)
        results = s.search(query, limit=limit)

        out = []
        for r in results:
            texto = r.get("texto", "") or ""
            # Fragmento relevante: busca el término en el texto
            frag = _fragmento(texto, q_raw)
            out.append({
                "id":          r.get("id", ""),
                "codigo_boe":  r.get("codigo_boe", ""),
                "ley":         r.get("ley", ""),
                "articulo":    r.get("articulo", ""),
                "fragmento":   frag,
                "url":         r.get("url", ""),
                "score":       round(r.score, 3),
            })

    return jsonify(out)


def _fragmento(texto: str, query: str, largo: int = 160) -> str:
    """Devuelve un extracto del texto centrado en el término buscado."""
    if not texto:
        return ""
    palabras = query.lower().split()
    pos = -1
    for p in palabras:
        idx = texto.lower().find(p)
        if idx != -1:
            pos = idx
            break
    if pos == -1:
        return texto[:largo] + ("…" if len(texto) > largo else "")
    inicio = max(0, pos - 40)
    fin    = min(len(texto), inicio + largo)
    frag   = texto[inicio:fin]
    if inicio > 0:
        frag = "…" + frag
    if fin < len(texto):
        frag = frag + "…"
    return frag


# ── /maquetador  ─────────────────────────────────────────────────────────
@boe_bp.route("/maquetador")
def maquetador():
    """Sirve el maquetador jurídico (fichero maquetador.html)."""
    return send_from_directory(
        os.path.join(os.path.dirname(__file__), "templates"),
        "maquetador.html"
    )


# ══════════════════════════════════════════════════════════════════════════
#  CÓMO REGISTRAR ESTE BLUEPRINT EN TU app.py:
#
#  from boe_search import boe_bp
#  app.register_blueprint(boe_bp)
#
# ══════════════════════════════════════════════════════════════════════════

"""
api_boe.py  –  Mini API Flask independiente para búsqueda en Whoosh
Arrancar con:  python3 api_boe.py
O como servicio permanente (ver instrucciones al final)
"""

from flask import Flask, jsonify, request
from whoosh.index import open_dir
from whoosh.qparser import MultifieldParser, OrGroup
import os

app = Flask(__name__)

# ── Índice Whoosh ─────────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
INDEX_DIR = os.path.join(BASE, "indice_whoosh")

ix = open_dir(INDEX_DIR)
print(f"✅ Índice abierto: {ix.doc_count():,} documentos")

# ── CORS para que PHP/JS puedan llamar ───────────────────────────
@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r

# ── /api/buscar ───────────────────────────────────────────────────
@app.route("/api/buscar")
def buscar():
    q_raw = request.args.get("q", "").strip()
    limit  = min(int(request.args.get("n", 8)), 20)

    if len(q_raw) < 2:
        return jsonify([])

    with ix.searcher() as s:
        parser = MultifieldParser(
            ["ley", "articulo", "texto"],
            schema=ix.schema,
            group=OrGroup
        )
        query  = parser.parse(q_raw)
        results = s.search(query, limit=limit)

        out = []
        for r in results:
            texto = r.get("texto", "") or ""
            out.append({
                "id":         r.get("id", ""),
                "codigo_boe": r.get("codigo_boe", ""),
                "ley":        r.get("ley", ""),
                "articulo":   r.get("articulo", ""),
                "fragmento":  _fragmento(texto, q_raw),
                "url":        r.get("url", ""),
                "score":      round(r.score, 3),
            })

    return jsonify(out)

def _fragmento(texto, query, largo=160):
    if not texto:
        return ""
    for p in query.lower().split():
        idx = texto.lower().find(p)
        if idx != -1:
            ini = max(0, idx - 40)
            fin = min(len(texto), ini + largo)
            frag = texto[ini:fin]
            if ini > 0: frag = "…" + frag
            if fin < len(texto): frag += "…"
            return frag
    return texto[:largo] + ("…" if len(texto) > largo else "")

# ── /api/exportar-pdf  ───────────────────────────────────────────
import subprocess, tempfile, pathlib

PDF_TEMPLATE = """<!DOCTYPE html><html><head><meta charset="UTF-8"/>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,600;0,700;1,400&display=swap" rel="stylesheet"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Cormorant Garamond',Georgia,serif;font-size:12pt;
  line-height:1.85;color:#111827;padding:54px 64px;}}
.pt{{font-size:9.5pt;font-weight:700;text-align:center;letter-spacing:.06em;
  text-transform:uppercase;margin-bottom:4px;}}
.ptipo{{font-size:14pt;font-weight:700;text-align:center;letter-spacing:.1em;
  text-decoration:underline;text-underline-offset:4px;margin-bottom:5px;}}
.pnum{{font-size:10pt;text-align:center;color:#666;margin-bottom:32px;}}
.pintro{{text-align:justify;margin-bottom:28px;}}
.psec{{font-size:10.5pt;font-weight:700;text-align:center;
  margin:28px 0 12px;letter-spacing:.09em;text-transform:uppercase;
  page-break-after:avoid;}}
.ped{{text-align:justify;white-space:pre-wrap;page-break-inside:avoid;}}
.pfund{{border-left:3px solid #B8922A;padding:14px 18px;
  background:rgba(201,168,76,.04);white-space:pre-wrap;
  font-size:11.5pt;line-height:1.88;page-break-inside:avoid;}}
.ppie{{margin-top:40px;text-align:right;page-break-inside:avoid;}}
.pfirma{{border-top:1px solid #999;width:200px;margin:48px 0 7px auto;}}
.ef{{border:none;border-bottom:1px dashed rgba(150,120,50,.35);
  font-family:inherit;font-size:inherit;font-style:italic;background:transparent;}}
@page{{margin:0;size:A4;}}
</style></head><body>{body}</body></html>"""

@app.route("/api/exportar-pdf", methods=["POST"])
def exportar_pdf():
    from flask import Response
    data  = request.get_json(force=True)
    inner = data.get("html", "")
    titulo = data.get("titulo", "Documento Jurídico")

    html_completo = PDF_TEMPLATE.format(body=inner)

    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = pathlib.Path(tmpdir) / "doc.html"
        pdf_path  = pathlib.Path(tmpdir) / "doc.pdf"
        html_path.write_text(html_completo, encoding="utf-8")

        try:
            subprocess.run([
                "/usr/bin/chromium-browser",
                "--headless", "--disable-gpu", "--no-sandbox",
                "--disable-dev-shm-usage",
                f"--print-to-pdf={pdf_path}",
                "--print-to-pdf-no-header",
                str(html_path)
            ], check=True, timeout=30,
               capture_output=True)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

        pdf_bytes = pdf_path.read_bytes()

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="documento_juridico.pdf"'}
    )


# ── Arranque ──────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)

# ══════════════════════════════════════════════════════════════════
# INSTALAR COMO SERVICIO PERMANENTE:
#
# 1. Crea el fichero de servicio:
#    nano /etc/systemd/system/boe-api.service
#
# 2. Pega esto dentro:
#
#    [Unit]
#    Description=BOE API Whoosh
#    After=network.target
#
#    [Service]
#    User=www-data
#    WorkingDirectory=/var/www/leydeboe.com
#    ExecStart=/usr/bin/python3 /var/www/leydeboe.com/api_boe.py
#    Restart=always
#
#    [Install]
#    WantedBy=multi-user.target
#
# 3. Activa y arranca:
#    systemctl daemon-reload
#    systemctl enable boe-api
#    systemctl start boe-api
#    systemctl status boe-api
# ══════════════════════════════════════════════════════════════════

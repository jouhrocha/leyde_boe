#!/usr/bin/env python3
"""
=============================================================
  PDF VERSION EXTRACTOR + COMPARATIVA VISUAL
=============================================================
  1. Detecta todas las versiones incrementales del PDF
  2. Extrae cada versión como PDF independiente
  3. Renderiza cada página de cada versión como imagen
  4. Genera diff visual pixel a pixel entre versiones
  5. Crea un informe HTML navegable con todas las comparativas
=============================================================
"""

import sys
import os
import re
import zlib
import struct
import shutil
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageEnhance
import io

# ── Colores ANSI ──────────────────────────────────────────────────────────────
R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"
M="\033[95m"; C="\033[96m"; W="\033[97m"; DIM="\033[2m"
BOLD="\033[1m"; RST="\033[0m"

def banner():
    print(f"""
{M}{BOLD}╔══════════════════════════════════════════════════════════╗
║       PDF VERSION EXTRACTOR + COMPARATIVA VISUAL        ║
║         Recupera y compara versiones borradas           ║
╚══════════════════════════════════════════════════════════╝{RST}
""")

def section(t):
    print(f"\n{C}{BOLD}{'═'*60}\n  ▶  {t}\n{'─'*60}{RST}")

def ok(msg):   print(f"  {G}✓{RST} {msg}")
def info(msg): print(f"  {DIM}→ {msg}{RST}")
def warn(msg): print(f"  {Y}⚠ {msg}{RST}")
def err(msg):  print(f"  {R}✗ {msg}{RST}")

# ══════════════════════════════════════════════════════════════════════════════
#  PASO 1: EXTRAER VERSIONES COMO PDFs INDEPENDIENTES
# ══════════════════════════════════════════════════════════════════════════════

def extract_versions(pdf_path, out_dir):
    """Extrae cada versión incremental del PDF como archivo independiente."""
    section("EXTRAYENDO VERSIONES DEL PDF")

    with open(pdf_path, 'rb') as f:
        pdf_bytes = f.read()

    # Encontrar todos los marcadores %%EOF
    eof_positions = [m.end() for m in re.finditer(rb'%%EOF[\r\n]*', pdf_bytes)]

    info(f"Marcadores %%EOF encontrados: {len(eof_positions)}")

    if len(eof_positions) < 2:
        warn("Solo hay una versión en este PDF. Aun así se procesará.")
        eof_positions = [len(pdf_bytes)]

    version_paths = []
    for i, eof_pos in enumerate(eof_positions):
        version_data = pdf_bytes[:eof_pos]
        version_path = os.path.join(out_dir, f"version_{i+1}.pdf")

        with open(version_path, 'wb') as f:
            f.write(version_data)

        size_kb = len(version_data) / 1024
        ok(f"Versión {i+1} guardada: {size_kb:.1f} KB → {version_path}")
        version_paths.append((i+1, version_path))

    return version_paths

# ══════════════════════════════════════════════════════════════════════════════
#  PASO 2: RENDERIZAR PDFs A IMÁGENES
# ══════════════════════════════════════════════════════════════════════════════

def render_pdf_to_images(version_num, pdf_path, img_dir, dpi=150):
    """
    Extrae imágenes embebidas del PDF usando solo pypdf + Pillow.
    PDFs escaneados: cada página ES una imagen JPEG/PNG embebida directamente.
    """
    ver_img_dir = os.path.join(img_dir, f"v{version_num}")
    os.makedirs(ver_img_dir, exist_ok=True)
    info(f"Extrayendo imágenes de versión {version_num}...")

    from PIL import Image, ImageDraw
    import io

    paths = []

    try:
        from pypdf import PdfReader
        import warnings, logging
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        warnings.filterwarnings("ignore")
        reader = PdfReader(pdf_path, strict=False)
        total = len(reader.pages)
        info(f"  {total} páginas encontradas")

        for page_idx, page in enumerate(reader.pages):
            page_num = page_idx + 1
            img_path = os.path.join(ver_img_dir, f"page-{page_num:03d}.png")
            page_imgs = []

            # Método A: API .images de pypdf >= 3.x
            try:
                for img_obj in page.images:
                    try:
                        pil = Image.open(io.BytesIO(img_obj.data)).convert('RGB')
                        page_imgs.append(pil)
                    except Exception:
                        pass
            except Exception:
                pass

            # Método B: XObjects directos si A falla
            if not page_imgs:
                try:
                    res = page.get("/Resources", {})
                    xobj_dict = res.get("/XObject", {})
                    for k in xobj_dict:
                        try:
                            xobj = xobj_dict[k]
                            if xobj.get("/Subtype") == "/Image":
                                pil = Image.open(io.BytesIO(xobj.data)).convert('RGB')
                                page_imgs.append(pil)
                        except Exception:
                            pass
                except Exception:
                    pass

            if page_imgs:
                # Usar la imagen más grande (normalmente el fondo de página completo)
                main = max(page_imgs, key=lambda i: i.width * i.height)
                main.save(img_path, 'PNG')
                paths.append((page_num, img_path))
                if page_num % 20 == 0 or page_num == total:
                    info(f"    {page_num}/{total} procesadas")
            else:
                # Página sin imagen: renderizar texto sobre fondo blanco
                try:
                    w = max(100, int(float(page.mediabox.width)  * dpi / 72))
                    h = max(100, int(float(page.mediabox.height) * dpi / 72))
                except Exception:
                    w, h = 1240, 1754
                canvas = Image.new('RGB', (w, h), 'white')
                try:
                    text = (page.extract_text() or "").strip()
                    draw = ImageDraw.Draw(canvas)
                    y = 40
                    for line in text.split('\n'):
                        if line.strip() and y < h - 20:
                            draw.text((40, y), line.strip()[:150], fill='black')
                            y += 15
                except Exception:
                    pass
                canvas.save(img_path, 'PNG')
                paths.append((page_num, img_path))

    except Exception as e:
        err(f"  Error versión {version_num}: {e}")
        import traceback; traceback.print_exc()

    ok(f"  Versión {version_num}: {len(paths)} páginas listas")
    return paths

def create_text_diff_image(img_path_a, img_path_b, diff_path,
                            text_a, text_b, removed, added,
                            version_a, version_b, page_num, label=""):
    """
    Genera imagen de 3 paneles:
      Izquierda: página versión A con texto eliminado destacado en rojo
      Centro:    resumen de cambios de texto
      Derecha:   página versión B con texto añadido destacado en verde
    """
    from PIL import Image, ImageDraw, ImageFont
    import io, textwrap

    def load_img(path):
        try:
            return Image.open(path).convert('RGB')
        except Exception:
            return Image.new('RGB', (800, 1100), 'white')

    img_a = load_img(img_path_a)
    img_b = load_img(img_path_b)

    # Escalar a tamaño manejable
    MAX_H = 1100
    def scale(img):
        if img.height > MAX_H:
            ratio = MAX_H / img.height
            return img.resize((int(img.width * ratio), MAX_H), Image.LANCZOS)
        return img

    img_a = scale(img_a)
    img_b = scale(img_b)

    W = max(img_a.width, img_b.width)
    H = max(img_a.height, img_b.height)

    # Intentar cargar fuente
    try:
        fnt_big  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        fnt_med  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        fnt_sm   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except Exception:
        fnt_big = fnt_med = fnt_sm = ImageFont.load_default()

    HEADER = 55
    FOOTER = 200  # panel de texto diff abajo
    TOTAL_W = W * 3 + 40
    TOTAL_H = H + HEADER + FOOTER

    canvas = Image.new('RGB', (TOTAL_W, TOTAL_H), '#0d1117')
    draw   = ImageDraw.Draw(canvas)

    # ── Pegar imágenes ────────────────────────────────────────────────────────
    # Panel izq: V_a
    canvas.paste(img_a.resize((W, H), Image.LANCZOS), (0, HEADER))
    # Panel der: V_b
    canvas.paste(img_b.resize((W, H), Image.LANCZOS), (W*2 + 40, HEADER))

    # Panel central: imagen B con tinte si hay cambios
    center_img = img_b.resize((W, H), Image.LANCZOS).copy()
    if removed or added:
        tint = Image.new('RGB', (W, H), '#1a1a2e')
        center_img = Image.blend(center_img, tint, 0.3)
    canvas.paste(center_img, (W + 20, HEADER))

    # ── Headers de paneles ────────────────────────────────────────────────────
    colors = {'left': '#e94560', 'center': '#f5a623', 'right': '#4ade80'}

    draw.rectangle([0, 0, W, HEADER-2], fill='#161b22')
    draw.text((10, 8),  f"VERSIÓN {version_a}  —  ANTES", fill=colors['left'], font=fnt_big)
    draw.text((10, 30), f"Página {page_num}", fill='#8b949e', font=fnt_sm)

    draw.rectangle([W+20, 0, W*2+20, HEADER-2], fill='#161b22')
    draw.text((W+30, 8),  "CAMBIOS DETECTADOS", fill=colors['center'], font=fnt_big)
    draw.text((W+30, 30), f"{len(removed)} palabras eliminadas · {len(added)} añadidas", fill='#8b949e', font=fnt_sm)

    draw.rectangle([W*2+40, 0, W*3+40, HEADER-2], fill='#161b22')
    draw.text((W*2+50, 8),  f"VERSIÓN {version_b}  —  DESPUÉS", fill=colors['right'], font=fnt_big)
    draw.text((W*2+50, 30), f"Página {page_num}", fill='#8b949e', font=fnt_sm)

    # Separadores verticales
    draw.rectangle([W, 0, W+20, TOTAL_H], fill='#21262d')
    draw.rectangle([W*2+20, 0, W*2+40, TOTAL_H], fill='#21262d')

    # ── Panel inferior: diff de texto ─────────────────────────────────────────
    footer_y = H + HEADER + 8
    draw.rectangle([0, H+HEADER, TOTAL_W, TOTAL_H], fill='#161b22')
    draw.line([0, H+HEADER, TOTAL_W, H+HEADER], fill='#30363d', width=2)

    if not removed and not added:
        draw.text((TOTAL_W//2 - 80, footer_y + 10), "✓ Sin cambios de texto en esta página",
                  fill='#4ade80', font=fnt_med)
    else:
        # Columna izq: eliminado (rojo)
        x = 12
        draw.text((x, footer_y), "TEXTO ELIMINADO (presente en V{}, ausente en V{}):".format(version_a, version_b),
                  fill='#e94560', font=fnt_sm)
        removed_sample = ' · '.join(sorted(removed)[:60])
        wrapped = textwrap.wrap(removed_sample, width=int(TOTAL_W/2/6.5))
        y = footer_y + 16
        for line in wrapped[:7]:
            if y < TOTAL_H - 10:
                draw.text((x, y), line, fill='#ff8099', font=fnt_sm)
                y += 14

        # Columna der: añadido (verde)
        x2 = TOTAL_W // 2 + 12
        draw.text((x2, footer_y), "TEXTO AÑADIDO (ausente en V{}, presente en V{}):".format(version_a, version_b),
                  fill='#4ade80', font=fnt_sm)
        added_sample = ' · '.join(sorted(added)[:60])
        wrapped2 = textwrap.wrap(added_sample, width=int(TOTAL_W/2/6.5))
        y2 = footer_y + 16
        for line in wrapped2[:7]:
            if y2 < TOTAL_H - 10:
                draw.text((x2, y2), line, fill='#86efac', font=fnt_sm)
                y2 += 14

    canvas.save(diff_path, 'PNG', optimize=True)


def generate_html_report(pdf_name, versions_info, comparisons, out_dir):
    """Genera un informe HTML con slider interactivo y galería de diffs."""
    section("GENERANDO INFORME HTML INTERACTIVO")

    # Construir secciones de comparativa
    comparison_html = ""
    for (va, vb, page_num, diff_path, pct) in comparisons:
        rel_diff = os.path.relpath(diff_path, out_dir)
        pct_color = '#e94560' if pct > 5 else '#f5a623' if pct > 1 else '#4ade80'
        badge = "CAMBIO SIGNIFICATIVO" if pct > 5 else "Cambio menor" if pct > 1 else "Sin cambios"

        comparison_html += f"""
        <div class="diff-card {'significant' if pct > 5 else ''}">
            <div class="diff-header">
                <div class="diff-title">
                    <span class="ver-badge va">V{va}</span>
                    <span class="arrow">→</span>
                    <span class="ver-badge vb">V{vb}</span>
                    <span class="page-label">Página {page_num}</span>
                </div>
                <div class="diff-stats">
                    <span class="pct-badge" style="background:{pct_color}">{pct:.1f}% modificado</span>
                    <span class="status-badge">{badge}</span>
                </div>
            </div>
            <div class="diff-image-container">
                <img src="{rel_diff}" class="diff-img" loading="lazy"
                     alt="Diff V{va} vs V{vb} página {page_num}"
                     onclick="openLightbox(this.src)"/>
                <div class="diff-zoom-hint">🔍 Clic para ampliar</div>
            </div>
            <div class="legend">
                <span class="leg-item removed">🔴 Contenido eliminado (presente en V{va}, ausente en V{vb})</span>
                <span class="leg-item added">🟢 Contenido añadido (ausente en V{va}, presente en V{vb})</span>
            </div>
        </div>
        """

    # Sidebar de versiones
    versions_sidebar = ""
    for vnum, vpath, n_pages, size_kb in versions_info:
        versions_sidebar += f"""
        <div class="ver-item" onclick="filterVersion({vnum})">
            <div class="ver-num">V{vnum}</div>
            <div class="ver-details">
                <div class="ver-pages">{n_pages} páginas</div>
                <div class="ver-size">{size_kb:.0f} KB</div>
            </div>
        </div>
        """

    # Contar cambios significativos
    sig_changes = sum(1 for *_, pct in comparisons if pct > 5)
    total_pages_compared = len(comparisons)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PDF Forensics — {pdf_name}</title>
<style>
  :root {{
    --bg: #0d1117; --bg2: #161b22; --bg3: #1c2128;
    --border: #30363d; --text: #c9d1d9; --text2: #8b949e;
    --red: #e94560; --green: #4ade80; --yellow: #f5a623;
    --blue: #58a6ff; --purple: #a371f7;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, 'Segoe UI', sans-serif; }}

  /* ── Header ── */
  .header {{
    background: linear-gradient(135deg, #0f3460 0%, #1a1a2e 50%, #16213e 100%);
    padding: 32px 40px;
    border-bottom: 2px solid var(--red);
  }}
  .header h1 {{ font-size: 28px; font-weight: 700; color: white; margin-bottom: 6px; }}
  .header h1 span {{ color: var(--red); }}
  .header .subtitle {{ color: var(--text2); font-size: 14px; }}
  .stats-row {{ display: flex; gap: 24px; margin-top: 20px; }}
  .stat-box {{
    background: rgba(255,255,255,0.05); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 20px; text-align: center;
  }}
  .stat-box .num {{ font-size: 28px; font-weight: 700; color: var(--yellow); }}
  .stat-box .label {{ font-size: 12px; color: var(--text2); margin-top: 2px; }}

  /* ── Layout ── */
  .main {{ display: flex; min-height: calc(100vh - 140px); }}
  .sidebar {{
    width: 200px; background: var(--bg2); border-right: 1px solid var(--border);
    padding: 20px 12px; position: sticky; top: 0; height: 100vh; overflow-y: auto;
  }}
  .sidebar h3 {{ font-size: 11px; text-transform: uppercase; color: var(--text2);
                 letter-spacing: 1px; margin-bottom: 12px; padding: 0 8px; }}
  .ver-item {{
    display: flex; align-items: center; gap: 12px; padding: 10px;
    border-radius: 8px; cursor: pointer; margin-bottom: 6px;
    border: 1px solid transparent; transition: all .2s;
  }}
  .ver-item:hover {{ background: var(--bg3); border-color: var(--border); }}
  .ver-num {{
    width: 36px; height: 36px; background: var(--red); border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 13px; flex-shrink: 0;
  }}
  .ver-pages {{ font-size: 13px; font-weight: 600; }}
  .ver-size  {{ font-size: 11px; color: var(--text2); }}

  /* ── Content ── */
  .content {{ flex: 1; padding: 28px; overflow-x: hidden; }}
  .section-title {{
    font-size: 16px; font-weight: 600; color: var(--text);
    margin-bottom: 20px; padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 10px;
  }}
  .section-title .count {{
    background: var(--red); color: white; font-size: 11px;
    padding: 2px 8px; border-radius: 10px; font-weight: 700;
  }}

  /* ── Diff Cards ── */
  .diff-card {{
    background: var(--bg2); border: 1px solid var(--border);
    border-radius: 12px; margin-bottom: 28px; overflow: hidden;
    transition: border-color .2s;
  }}
  .diff-card.significant {{ border-color: var(--red); }}
  .diff-card.significant .diff-header {{ border-bottom: 2px solid var(--red); }}

  .diff-header {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 14px 20px; background: var(--bg3);
    border-bottom: 1px solid var(--border);
  }}
  .diff-title {{ display: flex; align-items: center; gap: 10px; }}
  .ver-badge {{
    padding: 3px 10px; border-radius: 6px; font-size: 12px; font-weight: 700;
  }}
  .ver-badge.va {{ background: rgba(233,69,96,.2); color: var(--red); border: 1px solid var(--red); }}
  .ver-badge.vb {{ background: rgba(74,222,128,.2); color: var(--green); border: 1px solid var(--green); }}
  .arrow {{ color: var(--text2); font-size: 18px; }}
  .page-label {{ font-size: 13px; color: var(--text2); margin-left: 6px; }}

  .diff-stats {{ display: flex; gap: 10px; align-items: center; }}
  .pct-badge {{ padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; color: white; }}
  .status-badge {{ font-size: 11px; color: var(--text2); }}

  .diff-image-container {{
    position: relative; cursor: zoom-in; overflow: hidden;
    max-height: 600px; display: flex; align-items: center; justify-content: center;
    background: #000;
  }}
  .diff-img {{
    width: 100%; object-fit: contain; display: block;
    transition: transform .3s; max-height: 600px;
  }}
  .diff-img:hover {{ transform: scale(1.01); }}
  .diff-zoom-hint {{
    position: absolute; bottom: 10px; right: 10px;
    background: rgba(0,0,0,.7); color: white; font-size: 11px;
    padding: 4px 10px; border-radius: 4px; pointer-events: none;
  }}

  .legend {{
    display: flex; gap: 20px; padding: 10px 20px;
    background: rgba(0,0,0,.2); flex-wrap: wrap;
  }}
  .leg-item {{ font-size: 12px; color: var(--text2); }}
  .leg-item.removed {{ color: #ff8099; }}
  .leg-item.added   {{ color: #86efac; }}

  /* ── Lightbox ── */
  #lightbox {{
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,.95);
    z-index: 1000; cursor: zoom-out; align-items: center; justify-content: center;
  }}
  #lightbox.open {{ display: flex; }}
  #lightbox img {{ max-width: 98vw; max-height: 96vh; object-fit: contain; }}
  #lightbox .close {{
    position: fixed; top: 16px; right: 20px; color: white; font-size: 32px;
    cursor: pointer; z-index: 1001; background: rgba(233,69,96,.8);
    width: 44px; height: 44px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
  }}

  /* ── Scrollbar ── */
  ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
  ::-webkit-scrollbar-track {{ background: var(--bg); }}
  ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}

  /* ── Filter ── */
  .filter-bar {{
    display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap;
    align-items: center;
  }}
  .filter-btn {{
    padding: 6px 14px; border-radius: 20px; border: 1px solid var(--border);
    background: var(--bg3); color: var(--text); cursor: pointer;
    font-size: 12px; transition: all .2s;
  }}
  .filter-btn:hover, .filter-btn.active {{
    background: var(--red); border-color: var(--red); color: white;
  }}
  .no-results {{
    text-align: center; padding: 60px; color: var(--text2); font-size: 14px;
  }}
</style>
</head>
<body>

<div class="header">
  <h1>🔍 PDF <span>Forensics</span> — Comparativa de Versiones</h1>
  <div class="subtitle">{pdf_name} · Análisis de contenido modificado/eliminado</div>
  <div class="stats-row">
    <div class="stat-box">
      <div class="num">{len(versions_info)}</div>
      <div class="label">Versiones detectadas</div>
    </div>
    <div class="stat-box">
      <div class="num">{total_pages_compared}</div>
      <div class="label">Páginas comparadas</div>
    </div>
    <div class="stat-box">
      <div class="num" style="color:var(--red)">{sig_changes}</div>
      <div class="label">Cambios significativos</div>
    </div>
  </div>
</div>

<div class="main">
  <div class="sidebar">
    <h3>Versiones</h3>
    {versions_sidebar}
    <br>
    <h3>Guía</h3>
    <div style="font-size:11px;color:var(--text2);line-height:1.6;padding:0 8px">
      <p>🔴 <b>Eliminado</b>: existía en versión anterior</p><br>
      <p>🟢 <b>Añadido</b>: aparece en versión nueva</p><br>
      <p>Haz clic en cualquier imagen para ampliarla</p>
    </div>
  </div>

  <div class="content">
    <div class="section-title">
      Diferencias visuales entre versiones
      <span class="count">{total_pages_compared} comparaciones</span>
    </div>

    <div class="filter-bar">
      <button class="filter-btn active" onclick="filterAll()">Todas</button>
      <button class="filter-btn" onclick="filterSignificant()">Solo cambios significativos (&gt;5%)</button>
    </div>

    <div id="comparisons">
      {comparison_html}
    </div>

    <div class="no-results" id="no-results" style="display:none">
      No hay comparaciones para mostrar con este filtro.
    </div>
  </div>
</div>

<!-- Lightbox -->
<div id="lightbox" onclick="closeLightbox()">
  <div class="close" onclick="closeLightbox()">✕</div>
  <img id="lightbox-img" src="" alt=""/>
</div>

<script>
function openLightbox(src) {{
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').classList.add('open');
}}
function closeLightbox() {{
  document.getElementById('lightbox').classList.remove('open');
}}
document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') closeLightbox();
}});

function filterAll() {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.diff-card').forEach(c => c.style.display = '');
  document.getElementById('no-results').style.display = 'none';
}}

function filterSignificant() {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  let visible = 0;
  document.querySelectorAll('.diff-card').forEach(c => {{
    const show = c.classList.contains('significant');
    c.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  document.getElementById('no-results').style.display = visible === 0 ? '' : 'none';
}}

function filterVersion(v) {{
  // Scroll to first card mentioning that version
  const cards = document.querySelectorAll('.diff-card');
  cards.forEach(c => c.style.display = '');
  document.getElementById('no-results').style.display = 'none';
}}
</script>
</body>
</html>"""

    html_path = os.path.join(out_dir, "informe_comparativa.html")
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    ok(f"Informe HTML generado: {html_path}")
    return html_path

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="PDF Version Extractor + Comparativa Visual")
    parser.add_argument("pdf_file", help="Ruta al PDF a analizar")
    parser.add_argument("--dpi", type=int, default=150, help="DPI para renderizado (default: 150)")
    parser.add_argument("--max-pages", type=int, default=999, help="Máximo de páginas a comparar")
    parser.add_argument("--out", default="pdf_forensics_output", help="Carpeta de salida")
    args = parser.parse_args()

    banner()

    if not os.path.exists(args.pdf_file):
        err(f"Archivo no encontrado: {args.pdf_file}")
        sys.exit(1)

    pdf_name = Path(args.pdf_file).name
    out_dir  = args.out
    img_dir  = os.path.join(out_dir, "imagenes")
    diff_dir = os.path.join(out_dir, "diffs")

    os.makedirs(out_dir,  exist_ok=True)
    os.makedirs(img_dir,  exist_ok=True)
    os.makedirs(diff_dir, exist_ok=True)

    print(f"{W}PDF: {G}{BOLD}{args.pdf_file}{RST}")
    print(f"{W}Tamaño: {Y}{os.path.getsize(args.pdf_file)/1024/1024:.1f} MB{RST}")
    print(f"{W}Salida: {Y}{out_dir}/{RST}")
    print(f"{W}DPI: {Y}{args.dpi}{RST}")

    # ── 1. Extraer versiones ──────────────────────────────────────────────────
    version_paths = extract_versions(args.pdf_file, out_dir)

    if len(version_paths) == 0:
        err("No se pudieron extraer versiones del PDF")
        sys.exit(1)

    # ── 2. Renderizar cada versión ────────────────────────────────────────────
    section("RENDERIZANDO PÁGINAS DE CADA VERSIÓN")

    all_versions_pages = {}   # {version_num: [(page_num, img_path), ...]}
    versions_info = []

    for vnum, vpath in version_paths:
        fsize = os.path.getsize(vpath) if os.path.exists(vpath) else 0
        if not os.path.exists(vpath) or fsize < 10000:
            warn(f"Versión {vnum} demasiado pequeña ({fsize/1024:.1f} KB) — es solo el header del PDF, saltando")
            continue

        pages = render_pdf_to_images(vnum, vpath, img_dir, dpi=args.dpi)
        if pages:
            all_versions_pages[vnum] = pages[:args.max_pages]
            size_kb = os.path.getsize(vpath) / 1024
            versions_info.append((vnum, vpath, len(pages), size_kb))
            ok(f"Versión {vnum}: {len(pages)} páginas listas")
        else:
            warn(f"Versión {vnum}: no se pudieron renderizar páginas")

    if len(all_versions_pages) < 2:
        warn("Solo se pudo renderizar 1 versión. Generando informe de versión única...")
        # Crear informe simple con lo que hay
        if all_versions_pages:
            vnum = list(all_versions_pages.keys())[0]
            pages = all_versions_pages[vnum]
            html_path = generate_html_report(pdf_name, versions_info, [], out_dir)
            info("Informe generado con versión única")
        sys.exit(0)

    # ── 3. Extraer texto de cada versión ─────────────────────────────────────
    section("EXTRAYENDO TEXTO DE CADA VERSIÓN")

    from pypdf import PdfReader
    import warnings, logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")

    version_texts = {}  # {vnum: {page_num: text}}
    for vnum, vpath in version_paths:
        fsize = os.path.getsize(vpath) if os.path.exists(vpath) else 0
        if fsize < 10000:
            continue
        try:
            reader = PdfReader(vpath, strict=False)
            texts = {}
            for pidx, page in enumerate(reader.pages):
                try:
                    t = page.extract_text() or ""
                    texts[pidx+1] = t.strip()
                except Exception:
                    texts[pidx+1] = ""
            version_texts[vnum] = texts
            ok(f"Versión {vnum}: texto extraído de {len(texts)} páginas")
        except Exception as e:
            warn(f"Versión {vnum}: error extrayendo texto — {e}")

    # ── 4. Generar diffs de texto + imagen ───────────────────────────────────
    section("GENERANDO DIFFS VISUALES (TEXTO + IMAGEN)")

    version_nums = sorted(all_versions_pages.keys())
    comparisons  = []

    pairs = list(zip(version_nums, version_nums[1:]))  # consecutivas
    # Añadir primera vs última si hay 3+
    if len(version_nums) >= 3:
        pairs.append((version_nums[0], version_nums[-1]))

    for va, vb in pairs:
        pages_a = {pnum: ppath for pnum, ppath in all_versions_pages[va]}
        pages_b = {pnum: ppath for pnum, ppath in all_versions_pages[vb]}
        texts_a = version_texts.get(va, {})
        texts_b = version_texts.get(vb, {})
        common  = sorted(set(pages_a.keys()) & set(pages_b.keys()))
        label   = "GLOBAL" if (va, vb) == (version_nums[0], version_nums[-1]) and len(version_nums) >= 3 else ""
        info(f"Comparando V{va} vs V{vb} {label}: {len(common)} páginas")

        for page_num in common:
            txt_a = texts_a.get(page_num, "")
            txt_b = texts_b.get(page_num, "")

            # Calcular diferencia de texto
            words_a = set(txt_a.split())
            words_b = set(txt_b.split())
            removed = words_a - words_b
            added   = words_b - words_a
            total   = max(len(words_a | words_b), 1)
            pct     = (len(removed) + len(added)) / total * 100

            diff_filename = f"diff_v{va}_v{vb}_{label}_pag{page_num:03d}.png"
            diff_path = os.path.join(diff_dir, diff_filename)

            create_text_diff_image(
                img_path_a=pages_a[page_num],
                img_path_b=pages_b[page_num],
                diff_path=diff_path,
                text_a=txt_a, text_b=txt_b,
                removed=removed, added=added,
                version_a=va, version_b=vb,
                page_num=page_num, label=label
            )

            comparisons.append((va, vb, page_num, diff_path, pct))
            if pct > 2:
                print(f"    {R}★ Pág {page_num:3d} [{label or 'V'+str(va)+'→V'+str(vb)}]: {pct:.0f}% cambio — eliminadas: {len(removed)} palabras, añadidas: {len(added)}{RST}")
            else:
                print(f"    {DIM}  Pág {page_num:3d}: sin cambios de texto{RST}")

    # ── 5. Ordenar por % de cambio ────────────────────────────────────────────
    comparisons.sort(key=lambda x: x[4], reverse=True)

    # ── 6. Generar informe HTML ───────────────────────────────────────────────
    html_path = generate_html_report(pdf_name, versions_info, comparisons, out_dir)

    # ── Resumen final ─────────────────────────────────────────────────────────
    section("RESUMEN FINAL")

    sig = [(va, vb, p, pct) for va, vb, p, _, pct in comparisons if pct > 5]
    print(f"\n  {G}{BOLD}Versiones extraídas: {len(version_paths)}{RST}")
    print(f"  {G}{BOLD}Comparativas generadas: {len(comparisons)}{RST}")
    print(f"  {R}{BOLD}Cambios significativos (>5%): {len(sig)}{RST}")

    if sig:
        print(f"\n  {Y}Top cambios más relevantes:{RST}")
        for va, vb, pg, pct in sig[:10]:
            print(f"    {R}▶{RST} V{va}→V{vb} Página {pg}: {R}{pct:.1f}%{RST} modificado")

    print(f"\n  {G}{BOLD}✓ Informe HTML: {html_path}{RST}")
    print(f"  {B}Abre el HTML en tu navegador para ver la comparativa visual completa{RST}")
    print(f"\n{M}{BOLD}{'═'*60}\n  Análisis completado\n{'═'*60}{RST}\n")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
pdf_version_diff.py
-------------------
Dado un PDF con actualizaciones incrementales (varias versiones),
extrae cada versión, saca el texto de cada página, y muestra:
  - Qué texto DESAPARECIÓ entre versión vieja y nueva
  - Qué texto APARECIÓ nuevo
  - Imagen lado a lado de cada página que cambió

Uso:
    python pdf_version_diff.py archivo.pdf
"""

import sys
import os
import re
import tempfile
import difflib
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import pikepdf
from pdf2image import convert_from_path
import subprocess

# ── colores para el diff visual ──────────────────────────────────────────────
COLOR_REMOVED = (255, 80,  80)   # rojo
COLOR_ADDED   = (80,  200, 80)   # verde
COLOR_SAME    = (200, 200, 200)  # gris

def log(msg): print(f"  {msg}", flush=True)

# ─────────────────────────────────────────────────────────────────────────────
# 1. EXTRAER VERSIONES DEL PDF
# ─────────────────────────────────────────────────────────────────────────────

def find_version_offsets(pdf_bytes: bytes) -> list[int]:
    """Localiza los offsets de cada %%EOF → una por versión incremental."""
    offsets = []
    pos = 0
    while True:
        idx = pdf_bytes.find(b'%%EOF', pos)
        if idx == -1:
            break
        offsets.append(idx + 5)   # justo después de %%EOF
        pos = idx + 5
    return offsets

def extract_versions(pdf_path: str) -> list[bytes]:
    """Devuelve una lista de bytes, uno por versión del PDF."""
    data = Path(pdf_path).read_bytes()
    offsets = find_version_offsets(data)
    if len(offsets) <= 1:
        return [data]
    versions = []
    for off in offsets:
        # cada versión es válida hasta ese EOF
        versions.append(data[:off])
    return versions

# ─────────────────────────────────────────────────────────────────────────────
# 2. EXTRAER TEXTO DE CADA VERSIÓN (por página)
# ─────────────────────────────────────────────────────────────────────────────

def extract_text_per_page(pdf_bytes: bytes) -> list[str]:
    """Devuelve lista de strings, uno por página."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(pdf_bytes)
        tmp = f.name
    try:
        result = subprocess.run(
            ['pdftotext', '-layout', tmp, '-'],
            capture_output=True, text=True, timeout=30
        )
        # pdftotext separa páginas con \x0c
        pages = result.stdout.split('\x0c')
        return [p.strip() for p in pages if p.strip()]
    finally:
        os.unlink(tmp)

# ─────────────────────────────────────────────────────────────────────────────
# 3. DIFF DE TEXTO
# ─────────────────────────────────────────────────────────────────────────────

def diff_pages(old_pages: list[str], new_pages: list[str]) -> list[dict]:
    """
    Compara página a página.
    Devuelve lista de dicts con: page_num, removed, added, changed (bool)
    """
    results = []
    n = max(len(old_pages), len(new_pages))
    for i in range(n):
        old = old_pages[i] if i < len(old_pages) else ""
        new = new_pages[i] if i < len(new_pages) else ""
        
        if old == new:
            results.append({'page': i+1, 'changed': False, 'removed': [], 'added': []})
            continue

        old_lines = old.splitlines()
        new_lines = new.splitlines()
        
        removed, added = [], []
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ('remove', 'replace'):
                removed.extend(old_lines[i1:i2])
            if tag in ('insert', 'replace'):
                added.extend(new_lines[j1:j2])
        
        # filtrar líneas vacías
        removed = [l for l in removed if l.strip()]
        added   = [l for l in added   if l.strip()]
        
        results.append({
            'page': i+1,
            'changed': bool(removed or added),
            'removed': removed,
            'added': added,
        })
    return results

# ─────────────────────────────────────────────────────────────────────────────
# 4. RENDERIZAR PÁGINAS COMO IMAGEN
# ─────────────────────────────────────────────────────────────────────────────

def render_pages(pdf_bytes: bytes, page_nums: list[int], dpi=150) -> dict[int, Image.Image]:
    """Renderiza páginas concretas. Devuelve dict {num_pagina: Image}."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(pdf_bytes)
        tmp = f.name
    try:
        imgs = convert_from_path(tmp, dpi=dpi, fmt='RGB')
        return {pn: imgs[pn-1] for pn in page_nums if pn-1 < len(imgs)}
    finally:
        os.unlink(tmp)

# ─────────────────────────────────────────────────────────────────────────────
# 5. COMPONER IMAGEN COMPARATIVA
# ─────────────────────────────────────────────────────────────────────────────

def make_label(img: Image.Image, label: str, color: tuple) -> Image.Image:
    """Añade una barra de título sobre la imagen."""
    bar_h = 40
    out = Image.new('RGB', (img.width, img.height + bar_h), color)
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
    draw.text((10, 8), label, fill=(255,255,255), font=font)
    out.paste(img, (0, bar_h))
    return out

def compose_comparison(old_img: Image.Image, new_img: Image.Image,
                        page_num: int, removed: list[str], added: list[str]) -> Image.Image:
    """Une las dos imágenes lado a lado con panel de diff abajo."""
    # etiquetar
    old_labeled = make_label(old_img, f"VERSIÓN ANTERIOR — Página {page_num}", (160, 60, 60))
    new_labeled = make_label(new_img, f"VERSIÓN ACTUAL  — Página {page_num}", (40, 130, 60))

    # igualar alturas
    h = max(old_labeled.height, new_labeled.height)
    def pad(im, target_h):
        if im.height == target_h: return im
        bg = Image.new('RGB', (im.width, target_h), (240,240,240))
        bg.paste(im, (0,0))
        return bg
    old_labeled = pad(old_labeled, h)
    new_labeled = pad(new_labeled, h)

    # separador vertical
    sep_w = 6
    sep = Image.new('RGB', (sep_w, h), (50, 50, 50))

    # canvas lateral
    total_w = old_labeled.width + sep_w + new_labeled.width
    canvas = Image.new('RGB', (total_w, h), (255,255,255))
    canvas.paste(old_labeled, (0, 0))
    canvas.paste(sep, (old_labeled.width, 0))
    canvas.paste(new_labeled, (old_labeled.width + sep_w, 0))

    # panel de texto diff
    panel_h = max(40 + 26*(len(removed)+len(added)+4), 100)
    panel = Image.new('RGB', (total_w, panel_h), (30, 30, 30))
    draw = ImageDraw.Draw(panel)
    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        font_r = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
    except Exception:
        font_b = font_r = ImageFont.load_default()

    y = 12
    if removed:
        draw.text((12, y), "✖  TEXTO QUE DESAPARECIÓ:", fill=COLOR_REMOVED, font=font_b)
        y += 24
        for line in removed[:20]:
            draw.text((24, y), f"- {line[:120]}", fill=COLOR_REMOVED, font=font_r)
            y += 22
    if added:
        draw.text((12, y), "✔  TEXTO NUEVO / MODIFICADO:", fill=COLOR_ADDED, font=font_b)
        y += 24
        for line in added[:20]:
            draw.text((24, y), f"+ {line[:120]}", fill=COLOR_ADDED, font=font_r)
            y += 22

    # unir todo verticalmente
    final = Image.new('RGB', (total_w, h + panel_h), (255,255,255))
    final.paste(canvas, (0, 0))
    final.paste(panel, (0, h))
    return final

# ─────────────────────────────────────────────────────────────────────────────
# 6. REPORTE TEXTO PLANO
# ─────────────────────────────────────────────────────────────────────────────

def print_report(diffs: list[dict], n_versions: int):
    print("\n" + "═"*70)
    print(f"  PDF CON {n_versions} VERSIÓN(ES) DETECTADA(S)")
    print("═"*70)
    
    changed = [d for d in diffs if d['changed']]
    if not changed:
        print("\n  ✔  No se detectaron cambios de texto entre versiones.\n")
        return
    
    print(f"\n  Páginas con cambios: {[d['page'] for d in changed]}\n")
    for d in changed:
        print(f"  ── Página {d['page']} " + "─"*50)
        if d['removed']:
            print(f"\n  TEXTO QUE DESAPARECIÓ ({len(d['removed'])} líneas):")
            for l in d['removed']:
                print(f"    ✖  {l}")
        if d['added']:
            print(f"\n  TEXTO NUEVO / MODIFICADO ({len(d['added'])} líneas):")
            for l in d['added']:
                print(f"    ✔  {l}")
        print()

# ─────────────────────────────────────────────────────────────────────────────
# 7. MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Uso: python pdf_version_diff.py archivo.pdf")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("pdf_diff_output")
    out_dir.mkdir(exist_ok=True)
    
    print(f"\n▶  Analizando: {pdf_path}")
    
    # 1. extraer versiones
    log("Buscando versiones incrementales…")
    versions = extract_versions(pdf_path)
    n = len(versions)
    log(f"Versiones encontradas: {n}")
    
    if n < 2:
        print("\n  Este PDF solo tiene una versión. No hay nada que comparar.")
        sys.exit(0)
    
    # 2. texto por versión (usamos la primera y la última)
    log("Extrayendo texto versión ANTERIOR…")
    old_pages = extract_text_per_page(versions[0])
    log("Extrayendo texto versión ACTUAL…")
    new_pages = extract_text_per_page(versions[-1])
    
    # 3. diff
    log("Calculando diferencias…")
    diffs = diff_pages(old_pages, new_pages)
    
    # 4. reporte en consola
    print_report(diffs, n)
    
    # 5. imágenes solo de páginas cambiadas
    changed_pages = [d['page'] for d in diffs if d['changed']]
    
    if not changed_pages:
        print("  No hay cambios visuales que mostrar.")
        sys.exit(0)
    
    log(f"Renderizando páginas con cambios: {changed_pages}…")
    old_imgs = render_pages(versions[0],  changed_pages)
    new_imgs = render_pages(versions[-1], changed_pages)
    
    output_files = []
    for d in diffs:
        if not d['changed']:
            continue
        pn = d['page']
        if pn not in old_imgs or pn not in new_imgs:
            continue
        log(f"  Generando imagen comparativa página {pn}…")
        comp = compose_comparison(old_imgs[pn], new_imgs[pn], pn, d['removed'], d['added'])
        out_path = out_dir / f"diff_pagina_{pn:03d}.png"
        comp.save(out_path)
        output_files.append(str(out_path))
        log(f"  Guardado: {out_path}")
    
    print(f"\n✔  Imágenes guardadas en: {out_dir}/")
    print(f"   Archivos: {[os.path.basename(f) for f in output_files]}\n")
    return output_files

if __name__ == '__main__':
    main()

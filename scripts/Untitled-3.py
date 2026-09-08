#!/usr/bin/env python3
"""
DOSSIER JUDICIAL ULTIMATE v3.0 - CORREGIDO
"""

import os
import sys
import pickle
import threading
import concurrent.futures
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Callable
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
from PIL import Image, ImageTk
import io

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
ctk.set_appearance_mode("dark")
APP_VERSION = "3.0.1"

COLORS = {
    "bg_dark": "#0d1117", "bg_panel": "#161b22", "bg_card": "#21262d",
    "accent": "#1f6feb", "success": "#238636", "danger": "#da3633",
    "text": "#e6edf3", "text_dim": "#8b949e", "border": "#30363d",
    "gold": "#d4a017"
}

THUMB_SIZE = (180, 240)


# ─────────────────────────────────────────────
#  MODELO DE DATOS
# ─────────────────────────────────────────────
class Pagina:
    __slots__ = ['webp_path', 'num_original', 'prueba_nombre', 'enabled', '_thumb']
    
    def __init__(self, webp_path: str, num_original: int, prueba_nombre: str):
        self.webp_path = Path(webp_path)
        self.num_original = num_original
        self.prueba_nombre = prueba_nombre
        self.enabled = True
        self._thumb = None
        
    def get_thumbnail(self) -> Optional[ImageTk.PhotoImage]:
        if self._thumb is not None:
            return self._thumb
        try:
            img = Image.open(self.webp_path)
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            self._thumb = ImageTk.PhotoImage(img)
            return self._thumb
        except:
            return None


class Prueba:
    __slots__ = ['pdf_path', 'nombre', 'fecha', 'tamano', 'paginas', 'enabled', '_convertido']
    
    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)
        self.nombre = self.pdf_path.stem
        self.fecha = self._extraer_fecha()
        self.tamano = self.pdf_path.stat().st_size
        self.paginas: List[Pagina] = []
        self.enabled = True
        self._convertido = False

    def _extraer_fecha(self) -> datetime:
        try:
            reader = PdfReader(str(self.pdf_path))
            meta = reader.metadata
            if meta and meta.get("/CreationDate"):
                ds = meta["/CreationDate"].replace("D:", "").split("+")[0][:14]
                return datetime.strptime(ds, "%Y%m%d%H%M%S")
        except:
            pass
        return datetime.fromtimestamp(self.pdf_path.stat().st_mtime)

    @property
    def fecha_str(self) -> str:
        return self.fecha.strftime("%d/%m/%Y")

    @property 
    def num_paginas(self) -> int:
        return len([p for p in self.paginas if p.enabled])

    @property
    def convertido(self) -> bool:
        return self._convertido and len(self.paginas) > 0


# ─────────────────────────────────────────────
#  PROCESADOR
# ─────────────────────────────────────────────
class ProcesadorRapido:
    def __init__(self, log_cb: Callable, progress_cb: Callable):
        self.log = log_cb
        self.progress = progress_cb
        self.cancelar = False
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        
    def convertir_todos(self, pruebas: List[Prueba], dir_salida: Path) -> bool:
        dir_salida.mkdir(parents=True, exist_ok=True)
        futures = []
        for prueba in pruebas:
            future = self._executor.submit(self._convertir_uno, prueba, dir_salida)
            futures.append((prueba, future))
            
        completados = 0
        for prueba, future in futures:
            if self.cancelar:
                return False
            try:
                ok = future.result(timeout=300)
                completados += 1
                self.progress(completados / len(futures), f"Convertido {prueba.nombre}")
            except Exception as e:
                self.log(f"Error en {prueba.nombre}: {e}", "error")
        return completados > 0
        
    def _convertir_uno(self, prueba: Prueba, dir_base: Path) -> bool:
        carpeta = dir_base / prueba.nombre
        if carpeta.exists() and any(carpeta.glob("*.webp")):
            webps = sorted(carpeta.glob("*.webp"))
            prueba.paginas = [Pagina(str(w), i, prueba.nombre) for i, w in enumerate(webps)]
            prueba._convertido = True
            self.log(f"⏩ {prueba.nombre} ya convertido", "success")
            return True
            
        carpeta.mkdir(parents=True, exist_ok=True)
        try:
            import fitz
            doc = fitz.open(str(prueba.pdf_path))
            prueba.paginas = []
            for i, page in enumerate(doc):
                pix = page.get_pixmap(matrix=fitz.Matrix(150/72, 150/72))
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                ruta = carpeta / f"{i:04d}.webp"
                img.save(ruta, "WEBP", quality=85, method=6)
                prueba.paginas.append(Pagina(str(ruta), i, prueba.nombre))
            doc.close()
            prueba._convertido = True
            self.log(f"✅ {prueba.nombre}: {len(prueba.paginas)} páginas", "success")
            return True
        except Exception as e:
            self.log(f"❌ {prueba.nombre}: {e}", "error")
            return False

    def crear_dossier_limpio(self, pruebas: List[Prueba], salida: Path) -> bool:
        self.log("Generando dossier...", "info")
        try:
            writer = PdfWriter()
            indice_pdf = self._generar_indice(pruebas)
            writer.add_page(indice_pdf.pages[0])
            
            pagina_inicio = {}
            contador = 1
            for pr in pruebas:
                if pr.enabled and [p for p in pr.paginas if p.enabled]:
                    pagina_inicio[pr.nombre] = contador
                    contador += len([p for p in pr.paginas if p.enabled])
            
            total_paginas = sum(len([p for p in pr.paginas if p.enabled]) 
                              for pr in pruebas if pr.enabled)
            pagina_actual = 1
            
            for prueba in pruebas:
                if not prueba.enabled:
                    continue
                reader = PdfReader(str(prueba.pdf_path))
                for pag_obj in prueba.paginas:
                    if not pag_obj.enabled:
                        continue
                    if self.cancelar:
                        return False
                    pdf_page = reader.pages[pag_obj.num_original]
                    
                    # Añadir link invisible al índice
                    packet = io.BytesIO()
                    can = canvas.Canvas(packet, pagesize=A4)
                    can.linkAbsolute("Índice", "indice", (0, 0, 595, 842))
                    can.save()
                    packet.seek(0)
                    link_pdf = PdfReader(packet)
                    pdf_page.merge_page(link_pdf.pages[0])
                    writer.add_page(pdf_page)
                    
                    pagina_actual += 1
                    self.progress(pagina_actual / (total_paginas + 1), 
                                f"Página {pagina_actual}/{total_paginas}")
            
            with open(salida, "wb") as f:
                writer.write(f)
            self.log(f"✅ Dossier creado: {salida.name}", "success")
            return True
        except Exception as e:
            self.log(f"❌ Error: {e}", "error")
            import traceback; traceback.print_exc()
            return False
            
    def _generar_indice(self, pruebas: List[Prueba]):
        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=A4)
        W, H = A4
        
        c.setFillColorRGB(0.05, 0.07, 0.09)
        c.rect(0, 0, W, H, fill=1, stroke=0)
        
        c.setFillColorRGB(0.83, 0.63, 0.09)
        c.setFont("Helvetica-Bold", 24)
        c.drawCentredString(W/2, H - 60, "⚖  DOSSIER JUDICIAL  ⚖")
        
        c.setFillColorRGB(0.9, 0.9, 0.9)
        c.setFont("Helvetica", 12)
        c.drawCentredString(W/2, H - 85, "ÍNDICE DE PRUEBAS")
        
        c.setStrokeColorRGB(0.83, 0.63, 0.09)
        c.line(50, H - 100, W - 50, H - 100)
        
        y = H - 130
        x_num, x_nom, x_fec, x_pag, x_ini = 50, 80, 360, 460, 530
        
        headers = ["#", "NOMBRE", "FECHA", "PÁGS", "INICIO"]
        c.setFillColorRGB(0.83, 0.63, 0.09)
        c.setFont("Helvetica-Bold", 10)
        for x, h in zip([x_num, x_nom, x_fec, x_pag, x_ini], headers):
            c.drawString(x, y, h)
        y -= 25
        
        inicios = {}
        pag_actual = 1
        for pr in pruebas:
            if pr.enabled and [p for p in pr.paginas if p.enabled]:
                inicios[pr.nombre] = pag_actual
                pag_actual += len([p for p in pr.paginas if p.enabled])
        
        idx = 1
        for prueba in pruebas:
            if not prueba.enabled:
                continue
            pags = len([p for p in prueba.paginas if p.enabled])
            if pags == 0:
                continue
            if y < 80:
                break
            if idx % 2 == 0:
                c.setFillColorRGB(0.1, 0.12, 0.15)
                c.rect(50, y-5, W-100, 20, fill=1, stroke=0)
            
            nombre = prueba.nombre[:35] + "..." if len(prueba.nombre) > 38 else prueba.nombre
            c.setFillColorRGB(0.55, 0.73, 0.98)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(x_num, y, f"{idx:02d}")
            
            c.setFillColorRGB(0.9, 0.9, 0.9)
            c.setFont("Helvetica", 10)
            c.drawString(x_nom, y, nombre)
            
            c.setFillColorRGB(0.7, 0.7, 0.7)
            c.drawString(x_fec, y, prueba.fecha_str)
            c.drawString(x_pag, y, str(pags))
            
            ini = inicios.get(prueba.nombre, "-")
            c.setFillColorRGB(0.3, 0.8, 0.3)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(x_ini, y, str(ini))
            
            c.linkAbsolute("", f"prueba_{prueba.nombre}", 
                         (x_num-5, y-8, x_ini+50, y+12))
            
            y -= 20
            idx += 1
            
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.setFont("Helvetica", 9)
        c.drawCentredString(W/2, 30, f"Generado: {datetime.now().strftime('%d/%m/%Y')}")
        
        c.save()
        packet.seek(0)
        return PdfReader(packet)


# ─────────────────────────────────────────────
#  EDITOR CON DRAG & DROP CORREGIDO
# ─────────────────────────────────────────────
class EditorDragDrop(tk.Canvas):
    def __init__(self, parent, app_ref, **kwargs):
        super().__init__(parent, bg=COLORS["bg_dark"], highlightthickness=0, **kwargs)
        self.app = app_ref
        
        # Scroll
        self.scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.yview)
        self.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.pack(side="left", fill="both", expand=True)
        
        # Bindings scroll
        self.bind("<MouseWheel>", self._on_scroll)
        self.bind("<Button-4>", self._on_scroll)
        self.bind("<Button-5>", self._on_scroll)
        
        # Drag & drop
        self.drag_item = None
        self.drag_start_idx = None
        self.items = []
        
        # Configurar scroll region
        self.configure(scrollregion=(0, 0, 900, 2000))
        
    def _on_scroll(self, event):
        if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
            self.yview_scroll(-5, "units")
        else:
            self.yview_scroll(5, "units")
            
    def actualizar(self, pruebas: List[Prueba]):
        self.delete("all")
        self.items = []
        
        paginas_visibles = []
        for prueba in pruebas:
            if prueba.enabled:
                for pag in prueba.paginas:
                    paginas_visibles.append((prueba, pag))
        
        if not paginas_visibles:
            self.create_text(400, 300, text="No hay páginas. Convierte primero.", 
                           fill=COLORS["text_dim"], font=("Helvetica", 14))
            return
            
        cols = 4
        x_start, y_start = 20, 20
        x_pad, y_pad = 200, 280
        
        for i, (prueba, pag) in enumerate(paginas_visibles):
            col = i % cols
            row = i // cols
            x = x_start + col * x_pad
            y = y_start + row * y_pad
            self._dibujar_thumbnail(prueba, pag, x, y, i)
            
        max_y = y_start + ((len(paginas_visibles) - 1) // cols + 1) * y_pad
        self.configure(scrollregion=(0, 0, 900, max_y + 50))
        
    def _dibujar_thumbnail(self, prueba, pagina, x, y, idx):
        # Marco
        frame_id = self.create_rectangle(x, y, x+180, y+240, 
                                        fill=COLORS["bg_card"], 
                                        outline=COLORS["border"], width=1,
                                        tags=f"frame_{idx}")
        
        # Imagen
        thumb = pagina.get_thumbnail()
        if thumb:
            self.create_image(x+90, y+100, image=thumb, tags=f"img_{idx}")
            self.image = thumb
            
        # Checkbox
        chk_x, chk_y = x+10, y+10
        chk_color = COLORS["accent"] if pagina.enabled else COLORS["bg_dark"]
        self.create_rectangle(chk_x, chk_y, chk_x+15, chk_y+15, 
                             fill=chk_color, outline=COLORS["text"], tags=f"chk_{idx}")
        
        # Textos
        self.create_text(x+90, y+200, text=f"{prueba.nombre[:20]}", 
                      fill=COLORS["text"], font=("Helvetica", 9, "bold"),
                      width=160, tags=f"text_{idx}")
        self.create_text(x+90, y+220, text=f"Pág. {pagina.num_original+1}", 
                      fill=COLORS["text_dim"], font=("Helvetica", 8), tags=f"sub_{idx}")
        
        # Botón eliminar
        self.create_text(x+165, y+15, text="✕", fill=COLORS["danger"], 
                        font=("Helvetica", 12, "bold"), tags=f"del_{idx}")
        
        self.items.append({
            'idx': idx, 'prueba': prueba, 'pagina': pagina,
            'x': x, 'y': y, 'frame': frame_id,
        })
        
        # Bindings
        for tag in [f"frame_{idx}", f"img_{idx}"]:
            self.tag_bind(tag, "<ButtonPress-1>", lambda e, i=idx: self._start_drag(e, i))
            self.tag_bind(tag, "<B1-Motion>", lambda e, i=idx: self._dragging(e, i))
            self.tag_bind(tag, "<ButtonRelease-1>", lambda e, i=idx: self._on_drop(e, i))
            
        self.tag_bind(f"chk_{idx}", "<Button-1>", lambda e, p=pagina: self._toggle(e, p))
        self.tag_bind(f"del_{idx}", "<Button-1>", lambda e, p=pagina, pr=prueba: self._delete(e, p, pr))
        
    def _start_drag(self, event, idx):
        self.drag_item = idx
        item = self.items[idx]
        self.itemconfig(item['frame'], outline=COLORS["accent"], width=3)
        
    def _dragging(self, event, idx):
        pass  # Efecto visual opcional
        
    def _on_drop(self, event, idx):
        if self.drag_item is None:
            return
            
        # Calcular destino
        y = self.canvasy(event.y)
        cols, y_start, y_pad = 4, 20, 280
        row = int((y - y_start) / y_pad)
        dest_idx = max(0, min(row * cols, len(self.items) - 1))
        
        if dest_idx != self.drag_item:
            self._reordenar(self.drag_item, dest_idx)
            
        item = self.items[self.drag_item]
        self.itemconfig(item['frame'], outline=COLORS["border"], width=1)
        self.drag_item = None
        
    def _reordenar(self, origen, destino):
        item_origen = self.items[origen]
        pagina_mover = item_origen['pagina']
        prueba_origen = item_origen['prueba']
        
        # Eliminar de origen
        prueba_origen.paginas.remove(pagina_mover)
        
        # Insertar en destino
        item_dest = self.items[destino]
        prueba_dest = item_dest['prueba']
        if prueba_dest == prueba_origen and len(prueba_dest.paginas) == 0:
            prueba_dest.paginas.append(pagina_mover)
        else:
            idx_insert = min(destino % 4, len(prueba_dest.paginas))
            prueba_dest.paginas.insert(idx_insert, pagina_mover)
        
        self.actualizar(self.app.pruebas)
        self.app._actualizar_stats()
        
    def _toggle(self, event, pagina):
        pagina.enabled = not pagina.enabled
        self.actualizar(self.app.pruebas)
        self.app._actualizar_stats()
        
    def _delete(self, event, pagina, prueba):
        if messagebox.askyesno("Confirmar", "¿Eliminar esta página?"):
            prueba.paginas.remove(pagina)
            self.actualizar(self.app.pruebas)
            self.app._actualizar_stats()


# ─────────────────────────────────────────────
#  APP PRINCIPAL
# ─────────────────────────────────────────────
class DossierApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"⚖ DOSSIER JUDICIAL PRO v{APP_VERSION}")
        self.geometry("1400x900")
        self.configure(fg_color=COLORS["bg_dark"])
        
        self.pruebas: List[Prueba] = []
        self.dir_trabajo: Optional[Path] = None
        self.procesando = False
        
        self.procesador = ProcesadorRapido(self._log, self._progress)
        self._build_ui()
        
    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # Sidebar
        sb = ctk.CTkFrame(self, width=280, fg_color=COLORS["bg_panel"])
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        
        ctk.CTkLabel(sb, text="⚖", font=("Arial", 48), text_color=COLORS["gold"]).pack(pady=(20, 0))
        ctk.CTkLabel(sb, text="DOSSIER\nJUDICIAL", font=ctk.CTkFont("Helvetica", 18, "bold")).pack()
        
        ctk.CTkButton(sb, text="📂 Nuevo", command=self._nuevo).pack(padx=20, pady=5, fill="x")
        ctk.CTkButton(sb, text="➕ Añadir PDFs", command=self._agregar, 
                     fg_color=COLORS["accent"]).pack(padx=20, pady=5, fill="x")
        ctk.CTkButton(sb, text="🔄 Convertir Todo", command=self._convertir,
                     fg_color=COLORS["success"]).pack(padx=20, pady=5, fill="x")
        
        ctk.CTkLabel(sb, text="Ordenar por:", text_color=COLORS["text_dim"]).pack(pady=(20, 0))
        self.orden = ctk.CTkComboBox(sb, values=["Fecha", "Nombre", "Tamaño"], width=200)
        self.orden.set("Fecha")
        self.orden.pack(padx=20, pady=5)
        ctk.CTkButton(sb, text="Ordenar", command=self._ordenar).pack(padx=20, pady=2, fill="x")
        
        ctk.CTkButton(sb, text="📄 Crear Dossier", command=self._crear,
                     fg_color="#7d6608", hover_color="#a07d0a",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(padx=20, pady=20, fill="x")
        
        self.lbl_stats = ctk.CTkLabel(sb, text="0 pruebas | 0 páginas", 
                                     font=ctk.CTkFont(size=12))
        self.lbl_stats.pack(pady=10)
        
        self.progress = ctk.CTkProgressBar(sb)
        self.progress.set(0)
        self.progress.pack(padx=20, pady=5, fill="x")
        self.lbl_progress = ctk.CTkLabel(sb, text="", font=ctk.CTkFont(size=10))
        self.lbl_progress.pack()
        
        # Main
        self.tabs = ctk.CTkTabview(self, fg_color=COLORS["bg_panel"])
        self.tabs.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        
        self.tabs.add("📋 Lista")
        self.tabs.add("🖼 Editor")
        self.tabs.add("📜 Log")
        
        # Lista
        self.lista = ListaPruebas(self.tabs.tab("📋 Lista"), self)
        self.lista.pack(fill="both", expand=True)
        
        # Editor
        editor_frame = ttk.Frame(self.tabs.tab("🖼 Editor"))
        editor_frame.pack(fill="both", expand=True)
        self.editor = EditorDragDrop(editor_frame, self)
        
        # Log
        self.log_text = ctk.CTkTextbox(self.tabs.tab("📜 Log"), 
                                      font=ctk.CTkFont("Courier", 10))
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        self.log_text.configure(state="disabled")
        
    def _nuevo(self):
        self.pruebas = []
        self._actualizar_ui()
        self._log("Nuevo proyecto creado")
        
    def _agregar(self):
        rutas = filedialog.askopenfilenames(filetypes=[("PDF", "*.pdf")])
        if not rutas:
            return
        if not self.dir_trabajo:
            self.dir_trabajo = Path(rutas[0]).parent
        for r in rutas:
            if not any(p.pdf_path == Path(r) for p in self.pruebas):
                self.pruebas.append(Prueba(r))
        self._ordenar()
        self._log(f"Añadidos {len(rutas)} PDFs")
        
    def _convertir(self):
        if not self.pruebas:
            return
        dir_pruebas = self.dir_trabajo / "pruebas"
        def work():
            self.procesando = True
            ok = self.procesador.convertir_todos(self.pruebas, dir_pruebas)
            self.procesando = False
            self.after(0, lambda: [
                self._actualizar_ui(),
                self.tabs.set("🖼 Editor"),
                self._log("Conversión completada" if ok else "Error")
            ])
        threading.Thread(target=work, daemon=True).start()
        
    def _crear(self):
        if not any(p.convertido for p in self.pruebas):
            messagebox.showwarning("Atención", "Convierte los PDFs primero")
            return
        salida = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
            initialfile="Dossier_Judicial.pdf"
        )
        if not salida:
            return
        def work():
            self.procesando = True
            ok = self.procesador.crear_dossier_limpio(self.pruebas, Path(salida))
            self.procesando = False
            self.after(0, lambda: [
                self._log("Dossier creado" if ok else "Error"),
                messagebox.showinfo("Éxito", "Dossier generado") if ok else None
            ])
        threading.Thread(target=work, daemon=True).start()
        
    def _ordenar(self):
        modo = self.orden.get()
        if modo == "Fecha":
            self.pruebas.sort(key=lambda x: x.fecha)
        elif modo == "Nombre":
            self.pruebas.sort(key=lambda x: x.nombre.lower())
        elif modo == "Tamaño":
            self.pruebas.sort(key=lambda x: x.tamano)
        self._actualizar_ui()
        
    def _actualizar_ui(self):
        self.lista.actualizar()
        self.editor.actualizar(self.pruebas)
        self._actualizar_stats()
        
    def _actualizar_stats(self):
        n = len(self.pruebas)
        pags = sum(p.num_paginas for p in self.pruebas)
        self.lbl_stats.configure(text=f"{n} pruebas | {pags} páginas")
        
    def _log(self, msg, level="info"):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")
        
    def _progress(self, val, txt=""):
        self.progress.set(val)
        self.lbl_progress.configure(text=txt)
        self.update_idletasks()


class ListaPruebas(ttk.Frame):
    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, **kwargs)
        self.app = app
        
        columns = ("nombre", "fecha", "pags", "tamano", "estado")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        
        for col, title, w in zip(columns, ["NOMBRE", "FECHA", "PÁGS", "TAMAÑO", "ESTADO"],
                                [300, 120, 60, 100, 80]):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w)
            
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        
        self.tree.bind("<MouseWheel>", lambda e: self.tree.yview_scroll(int(-1*(e.delta/120)), "units"))
        self.tree.bind("<Button-4>", lambda e: self.tree.yview_scroll(-3, "units"))
        self.tree.bind("<Button-5>", lambda e: self.tree.yview_scroll(3, "units"))
        
    def actualizar(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for p in self.app.pruebas:
            estado = "✅" if p.convertido else "⏳"
            tam = f"{p.tamano/1024/1024:.1f}MB"
            self.tree.insert("", "end", values=(p.nombre, p.fecha_str, p.num_paginas, tam, estado))


if __name__ == "__main__":
    app = DossierApp()
    app.mainloop()
import os
import sys
import shutil
import hashlib
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import defaultdict
from pathlib import Path

# ── Colores ──────────────────────────────────────────────────────────────────
BG       = "#0f0f0f"
BG2      = "#1a1a1a"
BG3      = "#242424"
ACCENT   = "#e8ff00"
ACCENT2  = "#ff6b35"
FG       = "#f0f0f0"
FG2      = "#888888"
BORDER   = "#333333"
SUCCESS  = "#00ff88"
DANGER   = "#ff3b3b"

def style_btn(btn, color=ACCENT, fg=BG):
    btn.configure(
        bg=color, fg=fg, relief="flat", bd=0,
        activebackground=color, activeforeground=fg,
        font=("Courier New", 10, "bold"), cursor="hand2",
        padx=16, pady=8
    )

# ── Utilidades ────────────────────────────────────────────────────────────────
def fast_hash(path, blocksize=65536):
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(blocksize):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None

def find_duplicates(dirs, progress_cb=None):
    """Agrupa por (nombre, tamaño), luego verifica con hash MD5."""
    groups = defaultdict(list)
    all_files = []

    for d in dirs:
        for root, _, files in os.walk(d):
            for fname in files:
                fp = os.path.join(root, fname)
                try:
                    sz = os.path.getsize(fp)
                    all_files.append((fname, sz, fp))
                except Exception:
                    pass

    total = len(all_files)
    for i, (fname, sz, fp) in enumerate(all_files):
        groups[(fname, sz)].append(fp)
        if progress_cb:
            progress_cb(int((i + 1) / total * 60) if total else 60, f"Escaneando... {i+1}/{total}")

    duplicates = []
    candidates = [(k, v) for k, v in groups.items() if len(v) > 1]
    total_c = len(candidates)

    for i, ((fname, sz), paths) in enumerate(candidates):
        hash_map = defaultdict(list)
        for p in paths:
            h = fast_hash(p)
            if h:
                hash_map[h].append(p)
        for h, ps in hash_map.items():
            if len(ps) > 1:
                duplicates.append({"name": fname, "size": sz, "paths": ps})
        if progress_cb:
            progress_cb(60 + int((i + 1) / total_c * 40) if total_c else 100,
                        f"Verificando hashes... {i+1}/{total_c}")

    if progress_cb:
        progress_cb(100, "Listo")
    return duplicates

def human_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

# ── Ventana principal ─────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DEDUP — Limpiador de duplicados")
        self.configure(bg=BG)
        self.geometry("920x680")
        self.minsize(800, 560)
        self.resizable(True, True)

        self.dirs = []
        self.duplicates = []
        self.decisions = {}   # idx -> index of path to KEEP
        self.output_dir = tk.StringVar(value=str(Path.home() / "duplicados_recopilados"))

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=BG, pady=16)
        hdr.pack(fill="x", padx=24)
        tk.Label(hdr, text="DEDUP", bg=BG, fg=ACCENT,
                 font=("Courier New", 28, "bold")).pack(side="left")
        tk.Label(hdr, text="  limpiador de archivos duplicados", bg=BG, fg=FG2,
                 font=("Courier New", 11)).pack(side="left", padx=8)

        # Directorios
        frame_dirs = tk.LabelFrame(self, text="  DIRECTORIOS A ANALIZAR  ",
                                   bg=BG, fg=ACCENT, font=("Courier New", 9, "bold"),
                                   bd=1, relief="solid", labelanchor="nw")
        frame_dirs.pack(fill="x", padx=24, pady=(0, 12))

        top = tk.Frame(frame_dirs, bg=BG, pady=8, padx=12)
        top.pack(fill="x")

        self.dir_listbox = tk.Listbox(top, bg=BG3, fg=FG, selectbackground=ACCENT,
                                      selectforeground=BG, font=("Courier New", 10),
                                      height=4, bd=0, highlightthickness=1,
                                      highlightcolor=BORDER, relief="flat")
        self.dir_listbox.pack(side="left", fill="x", expand=True)

        btn_col = tk.Frame(top, bg=BG, padx=8)
        btn_col.pack(side="left")

        b_add = tk.Button(btn_col, text="+ Añadir", command=self._add_dir)
        style_btn(b_add, ACCENT, BG)
        b_add.pack(fill="x", pady=2)

        b_del = tk.Button(btn_col, text="− Quitar", command=self._remove_dir)
        style_btn(b_del, BG3, FG2)
        b_del.pack(fill="x", pady=2)

        # Carpeta de salida
        frame_out = tk.LabelFrame(self, text="  CARPETA PARA COPIAS ELIMINADAS  ",
                                  bg=BG, fg=FG2, font=("Courier New", 9, "bold"),
                                  bd=1, relief="solid", labelanchor="nw")
        frame_out.pack(fill="x", padx=24, pady=(0, 12))

        row_out = tk.Frame(frame_out, bg=BG, padx=12, pady=8)
        row_out.pack(fill="x")
        tk.Entry(row_out, textvariable=self.output_dir, bg=BG3, fg=FG,
                 insertbackground=FG, font=("Courier New", 10), bd=0,
                 highlightthickness=1, highlightcolor=BORDER, relief="flat"
                 ).pack(side="left", fill="x", expand=True, ipady=6, padx=(0,8))
        b_out = tk.Button(row_out, text="Explorar", command=self._choose_out)
        style_btn(b_out, BG3, FG2)
        b_out.pack(side="left")

        # Botón escanear
        self.btn_scan = tk.Button(self, text="⟳  ESCANEAR DUPLICADOS",
                                  command=self._start_scan)
        style_btn(self.btn_scan, ACCENT, BG)
        self.btn_scan.configure(font=("Courier New", 12, "bold"), pady=12)
        self.btn_scan.pack(fill="x", padx=24, pady=(0, 4))

        # Barra progreso
        self.progress_var = tk.DoubleVar()
        self.progress_lbl = tk.Label(self, text="", bg=BG, fg=FG2,
                                     font=("Courier New", 9))
        self.progress_lbl.pack()
        self.pbar = ttk.Progressbar(self, variable=self.progress_var,
                                    maximum=100, mode="determinate")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TProgressbar", troughcolor=BG3, background=ACCENT,
                        bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT)
        self.pbar.pack(fill="x", padx=24, pady=(2, 12))

        # Panel central duplicados
        self.mid = tk.Frame(self, bg=BG)
        self.mid.pack(fill="both", expand=True, padx=24)

        self.canvas = tk.Canvas(self.mid, bg=BG, bd=0, highlightthickness=0)
        vsb = tk.Scrollbar(self.mid, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.scroll_frame = tk.Frame(self.canvas, bg=BG)
        self.canvas_win = self.canvas.create_window((0, 0), window=self.scroll_frame,
                                                     anchor="nw")
        self.scroll_frame.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(
            self.canvas_win, width=e.width))
        self.canvas.bind_all("<MouseWheel>",
            lambda e: self.canvas.yview_scroll(-1*(e.delta//120), "units"))

        # Footer botón aplicar
        footer = tk.Frame(self, bg=BG, pady=12)
        footer.pack(fill="x", padx=24)
        self.lbl_summary = tk.Label(footer, text="", bg=BG, fg=FG2,
                                    font=("Courier New", 9))
        self.lbl_summary.pack(side="left")
        self.btn_apply = tk.Button(footer, text="✓  APLICAR SELECCIÓN",
                                   command=self._apply, state="disabled")
        style_btn(self.btn_apply, SUCCESS, BG)
        self.btn_apply.configure(font=("Courier New", 11, "bold"), pady=10)
        self.btn_apply.pack(side="right")

    # ── Acciones directorio ───────────────────────────────────────────────────
    def _add_dir(self):
        d = filedialog.askdirectory(title="Selecciona directorio")
        if d and d not in self.dirs:
            self.dirs.append(d)
            self.dir_listbox.insert("end", d)

    def _remove_dir(self):
        sel = self.dir_listbox.curselection()
        if sel:
            idx = sel[0]
            self.dir_listbox.delete(idx)
            del self.dirs[idx]

    def _choose_out(self):
        d = filedialog.askdirectory(title="Carpeta para copias eliminadas")
        if d:
            self.output_dir.set(d)

    # ── Escaneo ───────────────────────────────────────────────────────────────
    def _start_scan(self):
        if not self.dirs:
            messagebox.showwarning("Sin directorios", "Añade al menos un directorio.")
            return
        self.btn_scan.configure(state="disabled")
        self.btn_apply.configure(state="disabled")
        self._clear_results()
        self.progress_var.set(0)
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        def cb(pct, msg):
            self.progress_var.set(pct)
            self.progress_lbl.configure(text=msg)

        dupes = find_duplicates(self.dirs, cb)
        self.duplicates = dupes
        self.decisions = {i: 0 for i in range(len(dupes))}
        self.after(0, self._render_results)

    # ── Render resultados ─────────────────────────────────────────────────────
    def _clear_results(self):
        for w in self.scroll_frame.winfo_children():
            w.destroy()

    def _render_results(self):
        self._clear_results()
        n = len(self.duplicates)

        if n == 0:
            lbl = tk.Label(self.scroll_frame,
                           text="\n✓  No se encontraron duplicados exactos\n",
                           bg=BG, fg=SUCCESS, font=("Courier New", 13, "bold"))
            lbl.pack(pady=40)
            self.btn_scan.configure(state="normal")
            self.lbl_summary.configure(text="Sin duplicados.")
            return

        self.lbl_summary.configure(
            text=f"{n} grupo{'s' if n!=1 else ''} de duplicados encontrados")

        for idx, grp in enumerate(self.duplicates):
            self._build_group_card(idx, grp)

        self.btn_scan.configure(state="normal")
        self.btn_apply.configure(state="normal")

    def _build_group_card(self, idx, grp):
        card = tk.Frame(self.scroll_frame, bg=BG2, bd=0,
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=4, padx=2)

        # Cabecera
        hdr = tk.Frame(card, bg=BG3, padx=12, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"#{idx+1}  {grp['name']}", bg=BG3, fg=ACCENT,
                 font=("Courier New", 10, "bold")).pack(side="left")
        tk.Label(hdr, text=f"  {human_size(grp['size'])}  ·  {len(grp['paths'])} copias",
                 bg=BG3, fg=FG2, font=("Courier New", 9)).pack(side="left")

        # Radio buttons para cada copia
        var = tk.IntVar(value=self.decisions.get(idx, 0))

        def on_change(i=idx, v=var):
            self.decisions[i] = v.get()

        for pidx, path in enumerate(grp["paths"]):
            row = tk.Frame(card, bg=BG2, padx=12, pady=3)
            row.pack(fill="x")

            rb = tk.Radiobutton(row, variable=var, value=pidx,
                                command=lambda i=idx, v=var: on_change(i, v),
                                bg=BG2, fg=SUCCESS, activebackground=BG2,
                                selectcolor=BG, relief="flat",
                                font=("Courier New", 10))
            rb.pack(side="left")

            # Directorio padre en gris, nombre en blanco
            parent = str(Path(path).parent) + os.sep
            fname  = Path(path).name
            lbl_p  = tk.Label(row, text=parent, bg=BG2, fg=FG2,
                              font=("Courier New", 9))
            lbl_p.pack(side="left")
            lbl_f  = tk.Label(row, text=fname, bg=BG2, fg=FG,
                              font=("Courier New", 9, "bold"))
            lbl_f.pack(side="left")

            # Indicador MANTENER / MOVER
            tag_var = tk.StringVar()
            tag_lbl = tk.Label(row, textvariable=tag_var, bg=BG2,
                               font=("Courier New", 8, "bold"), padx=4)
            tag_lbl.pack(side="right")

            # Actualizar etiquetas al cambiar selección
            def refresh_tags(card_ref=card, grp_ref=grp, idx_ref=idx):
                kept = self.decisions.get(idx_ref, 0)
                for j, child_row in enumerate(
                        [w for w in card_ref.winfo_children() if isinstance(w, tk.Frame)][1:]):
                    labels = [w for w in child_row.winfo_children()
                              if isinstance(w, tk.Label) and hasattr(w, '_tag')]
                    # usar el último Label de cada fila como tag
                    tags = [w for w in child_row.winfo_children()
                            if isinstance(w, tk.Label)]
                    if tags:
                        t = tags[-1]
                        if j == kept:
                            t.configure(text="● MANTENER", fg=SUCCESS)
                        else:
                            t.configure(text="→ MOVER", fg=ACCENT2)

            rb.configure(command=lambda i=idx, v=var, r=refresh_tags: (on_change(i,v), r()))

            # Tag inicial
            if pidx == var.get():
                tag_lbl.configure(text="● MANTENER", fg=SUCCESS)
            else:
                tag_lbl.configure(text="→ MOVER", fg=ACCENT2)

    # ── Aplicar ───────────────────────────────────────────────────────────────
    def _apply(self):
        if not self.duplicates:
            return

        out = self.output_dir.get()
        if not out:
            messagebox.showerror("Error", "Especifica la carpeta de destino.")
            return

        total_moved = 0
        errors = []

        try:
            os.makedirs(out, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo crear la carpeta de destino:\n{e}")
            return

        for idx, grp in enumerate(self.duplicates):
            keep_idx = self.decisions.get(idx, 0)
            for pidx, path in enumerate(grp["paths"]):
                if pidx == keep_idx:
                    continue  # Esta copia se queda
                # Mover a carpeta de salida con nombre único
                dst_name = f"{idx+1:03d}_{pidx}_{Path(path).name}"
                dst = os.path.join(out, dst_name)
                try:
                    shutil.move(path, dst)
                    total_moved += 1
                except Exception as e:
                    errors.append(f"{path}\n  → {e}")

        msg = f"✓  {total_moved} archivo(s) movido(s) a:\n{out}"
        if errors:
            msg += f"\n\n⚠  {len(errors)} error(es):\n" + "\n".join(errors[:5])
        messagebox.showinfo("Proceso completado", msg)

        # Refrescar
        self._start_scan()


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()

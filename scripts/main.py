#!/usr/bin/env python3
"""
CLI principal del scraper datos.gob.es
Uso:
    python main.py scrape --modo full
    python main.py scrape --modo sector --sectores medio-ambiente,salud
    python main.py scrape --modo resume
    python main.py download-files                                # descarga todos los formatos
    python main.py download-files --formatos CSV,JSON           # solo CSV y JSON
    python main.py download-files --modo resume                 # reanudar descargas
    python main.py download-files --directorio /mis/datos       # directorio personalizado
    python main.py stats
    python main.py export --formato csv --salida mi_catalogo.csv
    python main.py queue --reset-failed
    python main.py sparql --query "SELECT ?s WHERE {?s a dcat:Dataset} LIMIT 5"
"""

import sys
import time
import logging
import argparse
import threading
from pathlib import Path
from datetime import datetime

# ── Logging antes de cualquier import interno ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/scraper.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scraper.main")

# ── Imports del proyecto ───────────────────────────────────────────────────────
try:
    from rich.console   import Console
    from rich.table     import Table
    from rich.live      import Live
    from rich.panel     import Panel
    from rich.progress  import (Progress, SpinnerColumn, BarColumn,
                                TextColumn, TimeElapsedColumn)
    from rich           import box
    RICH = True
except ImportError:
    RICH = False

from config.settings import SECTORES, SPARQL_ENDPOINT, FILES_DIR, BORME_FILES_DIR, BORME_START_DATE
from core.client import get, sparql_query
from core.engine import ScraperEngine
from core.borme import genera_fechas
from db.catalog_db import CatalogDB
from jobqueue.download_queue import DownloadQueue

console = Console() if RICH else None


# ─── Utilidades de display ────────────────────────────────────────────────────

def print_banner():
    if RICH:
        console.print(Panel.fit(
            "[bold cyan]datos.gob.es Scraper[/bold cyan]\n"
            "[dim]Descarga masiva del catálogo nacional de datos abiertos[/dim]",
            border_style="cyan"
        ))
    else:
        print("=" * 50)
        print(" datos.gob.es Scraper — Descarga Masiva")
        print("=" * 50)


def print_stats_table(db: CatalogDB, queue: DownloadQueue, engine_stats: dict = None):
    summary = db.summary()
    q_summary = queue.summary()

    if RICH:
        t = Table(title="Estado del Catálogo", box=box.ROUNDED, border_style="blue")
        t.add_column("Métrica", style="bold")
        t.add_column("Valor",   style="green", justify="right")

        t.add_row("📦 Datasets indexados",       f"{summary['datasets']:,}")
        t.add_row("📄 Distribuciones",            f"{summary['distributions']:,}")
        t.add_row("🏛️  Publicadores",             f"{summary['publishers']:,}")
        t.add_row("❌ Errores registrados",       f"{summary['errors']:,}")

        # Estadísticas de Fase 3
        dl = summary.get("downloads", {})
        if dl:
            t.add_section()
            t.add_row("✅ Ficheros descargados",  f"{dl.get('done', 0):,}")
            t.add_row("⏳ Ficheros pendientes",   f"{dl.get('pending', 0):,}")
            t.add_row("❌ Descargas fallidas",    f"{dl.get('failed', 0):,}")
            t.add_row("⏭️  Ficheros saltados",    f"{dl.get('skipped', 0):,}")

        if engine_stats:
            t.add_section()
            t.add_row("✅ Páginas descargadas",   f"{engine_stats.get('pages_done',0):,}")
            t.add_row("⏳ Trabajos pendientes",   f"{engine_stats.get('pending',0):,}")

        console.print(t)

        if summary["top_formats"]:
            tf = Table(title="Formatos más frecuentes", box=box.SIMPLE)
            tf.add_column("Formato")
            tf.add_column("Distribuciones", justify="right")
            for f in summary["top_formats"][:8]:
                tf.add_row(f.get("format") or "—", str(f.get("n", 0)))
            console.print(tf)

        if q_summary:
            tq = Table(title="Estado de la Cola", box=box.SIMPLE)
            tq.add_column("Tipo")
            tq.add_column("Estado")
            tq.add_column("Cantidad", justify="right")
            for row in q_summary:
                tq.add_row(row["job_type"], row["status"], str(row["count"]))
            console.print(tq)
    else:
        print(f"\nDatasets: {summary['datasets']:,}")
        print(f"Distribuciones: {summary['distributions']:,}")
        print(f"Publicadores: {summary['publishers']:,}")


# ─── Comandos ─────────────────────────────────────────────────────────────────

def cmd_download_borme(args):
    """
    Descarga el BORME completo desde la API del BOE.

    Flujo:
    1. Genera todos los días laborables 2009-01-02 → hoy (o rango indicado)
    2. Los registra en borme_dias (idempotente)
    3. Encola trabajos borme_dia en la cola persistente
    4. Workers descargan cada día: JSON sumario → disco + actos → BD
    5. Días sin BORME (festivos) quedan marcados como 'sin_borme' automáticamente
    """
    print_banner()

    db    = CatalogDB()
    queue = DownloadQueue()

    files_dir = Path(args.directorio or BORME_FILES_DIR)
    files_dir.mkdir(parents=True, exist_ok=True)

    # ── Reset de interrupciones ───────────────────────────────────────────────
    db.borme_reset_stale()

    # ── Seeding de fechas ─────────────────────────────────────────────────────
    if args.modo != "resume":
        if RICH:
            console.print("[cyan]Calculando fechas pendientes...[/cyan]")

        fechas = genera_fechas(
            desde=args.desde or None,
            hasta=args.hasta or None,
        )
        db.borme_seed_dias(fechas)

        # Reset de fallidos si se pide
        if args.reset_failed:
            db.borme_reset_failed()

    n_pending = db.borme_pending_count()
    if n_pending == 0:
        if RICH:
            bstats = db.borme_stats()
            console.print("[green]✔ BORME ya completamente descargado.[/green]")
            _print_borme_stats(bstats)
        return

    if RICH:
        console.print(
            f"[green]{n_pending:,} días BORME pendientes de descarga[/green] "
            f"[dim]→ {files_dir}[/dim]\n"
        )

    # ── Encolar trabajos ──────────────────────────────────────────────────────
    batch = db.borme_get_pending(limit=min(n_pending, 5000))
    jobs  = [
        ("borme_dia", {
            "fecha":     row["fecha"],
            "files_dir": str(files_dir),
        }, 3)
        for row in batch
    ]
    queue.push_many(jobs)

    if RICH:
        console.print(
            f"[green]▶  Descargando {len(jobs):,} días BORME "
            f"con {args.workers} workers...[/green]\n"
            "[dim]  Los días sin BORME (festivos/fin de semana) "
            "se marcan automáticamente como 'sin_borme'[/dim]\n"
        )

    # ── Monitor ───────────────────────────────────────────────────────────────
    live_stats: dict = {}
    stop_monitor = threading.Event()

    def on_progress(stats: dict):
        live_stats.update(stats)

    def _monitor():
        while not stop_monitor.is_set():
            time.sleep(10)
            if live_stats and RICH:
                console.print(
                    f"[dim][{datetime.now().strftime('%H:%M:%S')}] "
                    f"✔ {live_stats.get('borme_dias_ok',0):,} días | "
                    f"📋 {live_stats.get('borme_actos_ok',0):,} actos | "
                    f"❌ {live_stats.get('borme_dias_err',0):,} errores | "
                    f"⏳ {queue.pending_count():,} pendientes[/dim]"
                )

    engine = ScraperEngine(db=db, queue=queue, on_progress=on_progress)
    mon = threading.Thread(target=_monitor, daemon=True)
    mon.start()

    try:
        stats = engine.run(workers=args.workers, job_type="borme_dia")
    except KeyboardInterrupt:
        engine.stop()
        log.warning("BORME detenido. Reanuda con: download-borme --modo resume")
        if RICH:
            console.print(
                "\n[yellow]⚠  Pausado — reanuda con: "
                "download-borme --modo resume[/yellow]"
            )
    finally:
        stop_monitor.set()

    # ── Resumen final ─────────────────────────────────────────────────────────
    if RICH:
        console.print("\n[bold green]✔ Descarga BORME completada[/bold green]")
        _print_borme_stats(db.borme_stats())
        console.print(f"[dim]JSONs guardados en: {files_dir.resolve()}[/dim]")


def _print_borme_stats(bstats: dict):
    """Imprime la tabla de estadísticas BORME."""
    if not RICH:
        print(bstats)
        return
    t = Table(title="Estado del BORME", box=box.ROUNDED, border_style="cyan")
    t.add_column("Estado",   style="bold")
    t.add_column("Días",     justify="right", style="green")
    emojis = {
        "done":      "✅",
        "pending":   "⏳",
        "sin_borme": "📅",
        "failed":    "❌",
        "downloading": "⬇️",
    }
    for status, n in bstats.get("por_estado", {}).items():
        t.add_row(f"{emojis.get(status,'•')} {status}", f"{n:,}")
    t.add_section()
    t.add_row("📋 Actos/anuncios indexados", f"{bstats.get('actos_total',0):,}")
    console.print(t)


def cmd_download_files(args):
    """
    Fase 3: descarga los ficheros físicos de las distribuciones.

    Flujo:
    1. Lee de `distributions` todas las URLs descargables (filtrando por formato si se pide)
    2. Las registra en `downloaded_files` (idempotente, ignora duplicados)
    3. Lanza workers que descargan en paralelo con reanudación por bytes
    4. Persiste el progreso en BD para reanudar con --modo resume
    """
    print_banner()

    db    = CatalogDB()
    queue = DownloadQueue()

    # Recuperar filtros
    formatos = None
    if args.formatos:
        formatos = [f.strip().upper() for f in args.formatos.split(",")]
        if RICH:
            console.print(f"[dim]Filtrando formatos: {', '.join(formatos)}[/dim]")

    files_dir = Path(args.directorio or FILES_DIR)
    files_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Resetear descargas interrumpidas (status=downloading → pending) ──
    db.reset_stale_downloads()

    # ── 2. Poblar downloaded_files desde distributions ───────────────────────
    if args.modo != "resume":
        if RICH:
            console.print("[cyan]Registrando distribuciones pendientes de descarga...[/cyan]")
        distribs = db.get_distributions_with_url(
            formatos=formatos,
            limit=500_000,
        )
        if not distribs:
            console.print("[yellow]No hay distribuciones con URL de descarga en la BD.[/yellow]")
            console.print("[dim]Ejecuta 'scrape' primero para indexar el catálogo.[/dim]")
            return
        db.queue_downloads(distribs)
        total_pending = db.pending_downloads_count(formatos=formatos)
        if RICH:
            console.print(
                f"[green]{total_pending:,} ficheros pendientes de descarga[/green] "
                f"[dim]en {files_dir}[/dim]"
            )
    else:
        total_pending = db.pending_downloads_count(formatos=formatos)
        if RICH:
            console.print(f"[cyan]Modo resume: {total_pending:,} ficheros pendientes[/cyan]")

    if total_pending == 0:
        if RICH:
            console.print("[green]✔ Todos los ficheros ya están descargados.[/green]")
        return

    # ── 3. Encolar trabajos en la cola de jobs ───────────────────────────────
    batch_size  = min(total_pending, 2000)
    pending_rows = db.get_pending_downloads(formatos=formatos, limit=batch_size)

    jobs = []
    for row in pending_rows:
        jobs.append(("file_download", {
            "row_id":          row["id"],
            "distribution_id": row["distribution_id"],
            "dataset_id":      row["dataset_id"],
            "url":             row["url"],
            "format":          row["format"],
            "byte_size_expected": row.get("byte_size_expected"),
            "files_dir":       str(files_dir),
        }, 3))  # prioridad 3

    queue.push_many(jobs)

    if RICH:
        console.print(
            f"[green]▶  Descargando {len(jobs):,} ficheros "
            f"con {args.workers} workers...[/green]\n"
        )

    # ── 4. Monitor de progreso ───────────────────────────────────────────────
    live_stats: dict = {}
    stop_monitor = threading.Event()

    def _fmt_bytes(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    def _monitor():
        while not stop_monitor.is_set():
            time.sleep(8)
            if live_stats and RICH:
                console.print(
                    f"[dim][{datetime.now().strftime('%H:%M:%S')}] "
                    f"✔ {live_stats.get('files_done',0):,} ficheros | "
                    f"❌ {live_stats.get('files_err',0):,} errores | "
                    f"📦 {_fmt_bytes(live_stats.get('bytes_total',0))} descargados | "
                    f"⏳ {queue.pending_count():,} pendientes[/dim]"
                )

    def on_progress(stats: dict):
        live_stats.update(stats)

    engine = ScraperEngine(db=db, queue=queue, on_progress=on_progress)

    mon = threading.Thread(target=_monitor, daemon=True)
    mon.start()

    try:
        stats = engine.run(workers=args.workers, job_type="file_download")
    except KeyboardInterrupt:
        engine.stop()
        log.warning("Descarga detenida por el usuario. Reanuda con --modo resume")
        if RICH:
            console.print("\n[yellow]⚠  Pausado — reanuda con: download-files --modo resume[/yellow]")
    finally:
        stop_monitor.set()

    # ── 5. Informe final ──────────────────────────────────────────────────────
    dl_counts = db.count_downloads_by_status()
    if RICH:
        console.print("\n[bold green]✔ Descarga de ficheros completada[/bold green]")
        t = Table(title="Resumen de ficheros descargados", box=box.ROUNDED, border_style="green")
        t.add_column("Estado", style="bold")
        t.add_column("Ficheros", justify="right", style="green")
        for status, n in dl_counts.items():
            emoji = {"done": "✅", "failed": "❌", "skipped": "⏭️", "pending": "⏳"}.get(status, "•")
            t.add_row(f"{emoji} {status}", f"{n:,}")
        t.add_row("[dim]Total bytes[/dim]",
                  f"[dim]{_fmt_bytes(stats.get('bytes_total', 0))}[/dim]")
        console.print(t)
        console.print(f"[dim]Ficheros guardados en: {files_dir.resolve()}[/dim]")
    else:
        print(f"\nDescarga completada: {dl_counts}")


def cmd_scrape(args):
    """Inicia o reanuda la descarga del catálogo."""
    print_banner()

    db    = CatalogDB()
    queue = DownloadQueue()

    live_stats = {}

    def on_progress(stats: dict):
        live_stats.update(stats)

    engine = ScraperEngine(db=db, queue=queue, on_progress=on_progress)

    # Determinar sectores
    sectores = None
    if args.sectores:
        sectores = [s.strip() for s in args.sectores.split(",")]

    # Seedear cola
    engine.seedear_cola(
        modo=args.modo,
        sectores=sectores,
        forzar=getattr(args, "forzar", False),
    )

    n_pending = queue.pending_count()
    if n_pending == 0:
        log.info("Cola vacía — no hay nada que descargar")
        if RICH:
            console.print("[yellow]Cola vacía. Usa --modo full o --modo sector para iniciar.[/yellow]")
        return

    log.info(f"Iniciando descarga: {n_pending} trabajos en cola")

    if RICH:
        console.print(f"\n[green]▶  Descargando con {args.workers} workers...[/green]")
        console.print(f"[dim]  Trabajos en cola: {n_pending:,}[/dim]\n")

    # Monitor en hilo separado
    stop_monitor = threading.Event()

    def _monitor():
        while not stop_monitor.is_set():
            time.sleep(5)
            if live_stats and RICH:
                console.print(
                    f"[dim][{datetime.now().strftime('%H:%M:%S')}] "
                    f"datasets={live_stats.get('db_total',0):,} | "
                    f"páginas={live_stats.get('pages_done',0):,} | "
                    f"distribs={live_stats.get('distribs_ok',0):,} | "
                    f"pendientes={live_stats.get('pending',0):,}[/dim]"
                )

    mon = threading.Thread(target=_monitor, daemon=True)
    mon.start()

    try:
        stats = engine.run(workers=args.workers)
    except KeyboardInterrupt:
        engine.stop()
        log.warning("Descarga detenida por el usuario. El progreso está guardado.")
        if RICH:
            console.print("\n[yellow]⚠  Descarga pausada — reanuda con --modo resume[/yellow]")
    finally:
        stop_monitor.set()

    if RICH:
        console.print("\n[bold green]✔ Descarga completada[/bold green]")
    print_stats_table(db, queue, live_stats)


def cmd_stats(args):
    """Muestra estadísticas del catálogo descargado."""
    print_banner()
    db    = CatalogDB()
    queue = DownloadQueue()
    print_stats_table(db, queue)


def cmd_queue(args):
    """Gestión de la cola de descargas."""
    queue = DownloadQueue()
    if args.reset_failed:
        queue.reset_failed()
        if RICH:
            console.print("[green]Trabajos fallidos reencolados.[/green]")
    if args.clear_done:
        queue.clear_done()
        if RICH:
            console.print("[green]Trabajos completados eliminados.[/green]")
    if RICH:
        t = Table(title="Cola de descargas", box=box.ROUNDED)
        t.add_column("Tipo")
        t.add_column("Estado")
        t.add_column("Cantidad", justify="right")
        for row in queue.summary():
            t.add_row(row["job_type"], row["status"], str(row["count"]))
        console.print(t)


def cmd_export(args):
    """Exporta el catálogo descargado a CSV o JSON."""
    import sqlite3, csv, json
    from config.settings import DB_PATH

    db_path = Path(DB_PATH)
    if not db_path.exists():
        print("No hay base de datos. Ejecuta 'scrape' primero.")
        return

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    fmt    = args.formato.lower()
    salida = Path(args.salida)

    if fmt == "csv":
        rows = con.execute("SELECT * FROM datasets").fetchall()
        if not rows:
            print("Sin datos para exportar.")
            return
        with open(salida, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows([dict(r) for r in rows])
        print(f"Exportados {len(rows):,} datasets → {salida}")

    elif fmt == "json":
        rows = con.execute("SELECT * FROM datasets").fetchall()
        data = [dict(r) for r in rows]
        with open(salida, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Exportados {len(data):,} datasets → {salida}")

    elif fmt == "jsonl":
        rows = con.execute("SELECT * FROM datasets").fetchall()
        with open(salida, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(dict(r), ensure_ascii=False) + "\n")
        print(f"Exportados {len(rows):,} datasets → {salida} (JSONL)")

    con.close()


def cmd_sparql(args):
    """Ejecuta una consulta SPARQL contra el endpoint semántico de datos.gob.es."""
    query = args.query
    result = sparql_query(SPARQL_ENDPOINT, query, fmt="json")
    if result and "results" in result:
        bindings = result["results"]["bindings"]
        if RICH:
            cols = result["head"]["vars"]
            t = Table(title=f"SPARQL — {len(bindings)} resultados", box=box.MINIMAL)
            for c in cols:
                t.add_column(c)
            for b in bindings[:50]:
                t.add_row(*[b.get(c, {}).get("value", "") for c in cols])
            console.print(t)
            if len(bindings) > 50:
                console.print(f"[dim]... y {len(bindings)-50} más[/dim]")
        else:
            import json as j
            print(j.dumps(bindings, indent=2, ensure_ascii=False))
    else:
        print("Sin resultados o error en la consulta.")


# ─── Argparse ─────────────────────────────────────────────────────────────────

def cmd_download_borme_xml(args):
    """
    Descarga el XML de cada acto BORME (sección A — Empresarios) ya indexado
    y guarda los anuncios/empresas en la tabla borme_anuncios.
    Prerequisito: haber ejecutado 'download-borme' primero.
    """
    print_banner()

    db    = CatalogDB()
    queue = DownloadQueue()

    files_dir = Path(args.directorio or BORME_FILES_DIR)
    files_dir.mkdir(parents=True, exist_ok=True)

    db.borme_reset_stale_xml()

    if args.reset_failed:
        with db._conn() as con:
            n = con.execute(
                "UPDATE borme_actos SET xml_status='pending' WHERE xml_status='failed'"
            ).rowcount
        if RICH:
            console.print(f"[yellow]Reset {n} XMLs fallidos → pending[/yellow]")

    n_pending = db.borme_pending_xml_count()
    if n_pending == 0:
        if RICH:
            console.print("[green]✔ Todos los XMLs ya descargados.[/green]")
        return

    if RICH:
        console.print(
            f"[green]{n_pending:,} actos BORME pendientes de XML[/green] "
            f"[dim]→ {files_dir}/xml/[/dim]\n"
        )

    # Solo encolar si la cola está vacía para evitar duplicados
    n_en_cola = queue.pending_count("borme_acto_xml")
    cola_vacia = n_en_cola == 0

    if not cola_vacia:
        if RICH:
            console.print(f"[dim]Cola ya tiene {n_en_cola:,} jobs pendientes, reanudando...[/dim]")
    else:
        LOTE = 5000
        encolados = 0
        while encolados < n_pending:
            batch = db.borme_get_actos_pending_xml(limit=min(LOTE, n_pending - encolados))
            if not batch:
                break
            jobs = [
                ("borme_acto_xml", {
                    "acto_id":   row["id"],
                    "fecha":     row["fecha"],
                    "files_dir": str(files_dir),
                }, 3)
                for row in batch
            ]
            queue.push_many(jobs)
            encolados += len(jobs)
            if RICH:
                console.print(f"[dim]Encolados {encolados:,}/{n_pending:,}...[/dim]")

    if RICH:
        console.print(
            f"[green]Descargando XMLs con {args.workers} workers...[/green]\n"
        )

    live_stats: dict = {}
    stop_monitor = threading.Event()

    def on_progress(s: dict):
        live_stats.update(s)

    def _monitor():
        while not stop_monitor.is_set():
            time.sleep(10)
            if live_stats and RICH:
                console.print(
                    f"[dim][{datetime.now().strftime('%H:%M:%S')}] "
                    f"XMLs ok: {live_stats.get('borme_xml_ok', 0):,} | "
                    f"Anuncios: {live_stats.get('borme_anuncios_ok', 0):,} | "
                    f"Pendientes: {queue.pending_count():,}[/dim]"
                )

    engine = ScraperEngine(db=db, queue=queue, on_progress=on_progress)
    mon = threading.Thread(target=_monitor, daemon=True)
    mon.start()

    stats = {}
    try:
        stats = engine.run(workers=args.workers, job_type="borme_acto_xml")
    except KeyboardInterrupt:
        engine.stop()
        if RICH:
            console.print("\n[yellow]Pausado - vuelve a lanzar el mismo comando para reanudar[/yellow]")
    finally:
        stop_monitor.set()

    if RICH:
        console.print(
            f"\n[bold green]XMLs descargados[/bold green] - "
            f"{stats.get('borme_xml_ok', 0):,} actos | "
            f"{stats.get('borme_anuncios_ok', 0):,} anuncios en BD"
        )
        console.print(f"[dim]XMLs en: {(files_dir / 'xml').resolve()}[/dim]")



def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Scraper del catálogo nacional de datos abiertos datos.gob.es"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # scrape
    s = sub.add_parser("scrape", help="Inicia o reanuda la descarga del catálogo")
    s.add_argument("--modo", choices=["full", "sector", "resume"], default="full",
                   help="full=catálogo completo | sector=sectores específicos | resume=reanudar")
    s.add_argument("--sectores", default=None,
                   help="Lista de sectores separados por coma (solo con --modo sector)")
    s.add_argument("--workers", type=int, default=4,
                   help="Número de workers paralelos (default: 4, max recomendado: 6)")
    s.add_argument("--forzar", action="store_true",
                   help="Reinicia la cola aunque haya trabajos pendientes")
    s.set_defaults(func=cmd_scrape)

    # download-files
    d = sub.add_parser("download-files", help="Fase 3: descarga los ficheros físicos de las distribuciones")
    d.add_argument("--modo", choices=["full", "resume"], default="full",
                   help="full=encolar todo | resume=reanudar descargas interrumpidas")
    d.add_argument("--formatos", default=None,
                   help="Formatos a descargar separados por coma, ej: CSV,JSON,XML "
                        "(por defecto: todos)")
    d.add_argument("--directorio", default=None,
                   help=f"Directorio raíz donde guardar los ficheros (default: {FILES_DIR})")
    d.add_argument("--workers", type=int, default=4,
                   help="Workers paralelos de descarga (default: 4)")
    d.set_defaults(func=cmd_download_files)

    # stats
    st = sub.add_parser("stats", help="Muestra estadísticas del catálogo descargado")
    st.set_defaults(func=cmd_stats)

    # queue
    q = sub.add_parser("queue", help="Gestión de la cola de descargas")
    q.add_argument("--reset-failed", action="store_true", help="Reencola trabajos fallidos")
    q.add_argument("--clear-done",   action="store_true", help="Elimina trabajos completados")
    q.set_defaults(func=cmd_queue)

    # export
    e = sub.add_parser("export", help="Exporta el catálogo a CSV/JSON/JSONL")
    e.add_argument("--formato", choices=["csv", "json", "jsonl"], default="csv")
    e.add_argument("--salida",  default="catalogo_export.csv")
    e.set_defaults(func=cmd_export)

    # sparql
    sp = sub.add_parser("sparql", help="Consulta SPARQL directa")
    sp.add_argument("--query", required=True, help="Consulta SPARQL")
    sp.set_defaults(func=cmd_sparql)

    # download-borme
    b = sub.add_parser("download-borme", help="Descarga el BORME completo desde la API del BOE")
    b.add_argument("--modo", choices=["full", "resume"], default="full",
                   help="full=desde cero (respeta ya descargados), resume=continúa cola existente")
    b.add_argument("--desde", default=None, metavar="YYYY-MM-DD",
                   help="Fecha de inicio (default: 2009-01-02)")
    b.add_argument("--hasta", default=None, metavar="YYYY-MM-DD",
                   help="Fecha de fin (default: hoy)")
    b.add_argument("--directorio", default=None,
                   help=f"Carpeta destino de los JSONs (default: {BORME_FILES_DIR})")
    b.add_argument("--workers", type=int, default=4,
                   help="Descargas paralelas (default: 4)")
    b.add_argument("--reset-failed", action="store_true",
                   help="Reintenta los días marcados como fallidos")
    b.set_defaults(func=cmd_download_borme)

    # download-borme-xml
    bx = sub.add_parser(
        "download-borme-xml",
        help="Descarga el XML de cada acto BORME indexado y guarda los anuncios en BD"
    )
    bx.add_argument(
        "--directorio", default=None,
        help=f"Carpeta destino (default: {BORME_FILES_DIR})"
    )
    bx.add_argument(
        "--workers", type=int, default=4,
        help="Workers paralelos (default: 4)"
    )
    bx.add_argument(
        "--reset-failed", action="store_true",
        help="Reintenta los XMLs marcados como fallidos"
    )
    bx.set_defaults(func=cmd_download_borme_xml)

    return p


def main():
    Path("logs").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    parser = build_parser()
    args   = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
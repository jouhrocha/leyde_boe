"""
Motor de scraping con:
  - Cola de trabajos persistente (reanudable)
  - Workers concurrentes con ThreadPool
  - Descarga acumulativa por páginas
  - Agrupación automática de datasets + distribuciones
  - Estadísticas en tiempo real
"""

import json
import time
import uuid
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from config.settings import (
    API_BASE, CATALOG_ENDPOINT, DISTRIB_ENDPOINT, THEME_ENDPOINT,
    PAGE_SIZE, MAX_WORKERS, SECTORES, FILES_DIR, BORME_FILES_DIR,
)
from core.client      import get
from core.parser      import parse_api_response, parse_distributions_response, _slug
from core.downloader  import download_file, build_local_path
from core.borme       import fetch_sumario, parse_sumario, guardar_sumario, SinBorme
from db.catalog_db    import CatalogDB
from jobqueue.download_queue import DownloadQueue

log = logging.getLogger("scraper.engine")


class ScraperEngine:
    """
    Motor de scraping de datos.gob.es con cola persistente y reanudación.

    Flujo:
    1. seedear_cola() — genera los trabajos de página inicial
    2. run()          — workers consumen la cola hasta agotarla
    3. Si se interrumpe, la próxima llamada a run() reanuda desde donde quedó
    """

    def __init__(
        self,
        db:    CatalogDB    = None,
        queue: DownloadQueue = None,
        on_progress: Callable = None,
    ):
        self.db    = db    or CatalogDB()
        self.queue = queue or DownloadQueue()
        self.session_id  = self.db.get_progress("session_id") or uuid.uuid4().hex[:8]
        self.on_progress = on_progress  # callback(stats_dict) para la UI
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Contadores en memoria para el progreso en tiempo real
        self._stats = {
            "datasets_ok":   0,
            "datasets_err":  0,
            "distribs_ok":   0,
            "pages_done":    0,
            "files_done":    0,
            "files_err":     0,
            "bytes_total":   0,
        }

        # Guardar session_id para reanudación
        self.db.set_progress("session_id", self.session_id)

    # ── Seeding ───────────────────────────────────────────────────────────────

    def seedear_cola(
        self,
        modo: str = "full",
        sectores: list[str] | None = None,
        forzar: bool = False,
    ):
        """
        Genera los trabajos de la primera pasada.
        modo: 'full'     → todo el catálogo paginado
              'sector'   → solo los sectores listados
              'resume'   → no seedear, reanudar desde la cola existente
        """
        if modo == "resume":
            pending = self.queue.pending_count()
            log.info(f"Modo reanudación: {pending} trabajos pendientes en cola")
            return

        # Si ya hay trabajos y no se fuerza, reanudar silenciosamente
        if not forzar and self.queue.pending_count() > 0:
            log.info("Cola no vacía → reanudando descarga existente")
            return

        log.info(f"Seeding cola en modo '{modo}'...")
        jobs = []

        if modo == "full":
            # Determinar total de páginas consultando la primera
            resp = get(CATALOG_ENDPOINT, {"_pageSize": PAGE_SIZE, "_page": 0})
            if not resp:
                log.error("No se pudo consultar el catálogo inicial")
                return
            _, meta = parse_api_response(resp)
            total    = meta.get("total_items") or 0
            per_page = meta.get("items_per_page") or PAGE_SIZE
            n_pages  = max(1, -(-total // per_page))  # ceil division

            log.info(f"Catálogo: ~{total} datasets en {n_pages} páginas")
            self.db.set_progress("total_items", total)
            self.db.set_progress("total_pages", n_pages)

            for p in range(n_pages):
                jobs.append(("page", {
                    "url":      CATALOG_ENDPOINT,
                    "page":     p,
                    "pageSize": PAGE_SIZE,
                    "sector":   None,
                }, 5))

        elif modo == "sector":
            targets = sectores or SECTORES
            for sector in targets:
                url = THEME_ENDPOINT.format(id=sector)
                # Primera página para saber el total
                resp = get(url, {"_pageSize": PAGE_SIZE, "_page": 0})
                if not resp:
                    continue
                _, meta = parse_api_response(resp)
                total    = meta.get("total_items") or 0
                per_page = meta.get("items_per_page") or PAGE_SIZE
                n_pages  = max(1, -(-total // per_page))
                log.info(f"  Sector '{sector}': ~{total} datasets, {n_pages} páginas")
                for p in range(n_pages):
                    jobs.append(("page", {
                        "url":      url,
                        "page":     p,
                        "pageSize": PAGE_SIZE,
                        "sector":   sector,
                    }, 4))

        if jobs:
            self.queue.push_many(jobs)
            log.info(f"Encolados {len(jobs)} trabajos de paginación")

    # ── Procesadores ──────────────────────────────────────────────────────────

    def _procesar_pagina(self, job: dict) -> int:
        """Descarga una página del catálogo, inserta datasets y encola distribuciones."""
        payload = job["payload"]
        url     = payload["url"]
        page    = payload["page"]
        size    = payload.get("pageSize", PAGE_SIZE)

        resp = get(url, {"_pageSize": size, "_page": page})
        if resp is None:
            raise RuntimeError(f"Sin respuesta en {url}?_page={page}")

        datasets, _ = parse_api_response(resp)
        if not datasets:
            return 0

        self.db.upsert_datasets(datasets)

        # Encolar trabajos de distribución para cada dataset
        distrib_jobs = []
        for ds in datasets:
            dataset_id = ds["id"]
            if not dataset_id:
                continue
            distrib_jobs.append(("distribution", {
                "dataset_id": dataset_id,
                "uri":        ds["uri"],
            }, 6))

        if distrib_jobs:
            self.queue.push_many(distrib_jobs)

        # Publisher
        for ds in datasets:
            if ds.get("publisher_uri"):
                self.db.upsert_publisher(ds["publisher_uri"])

        with self._lock:
            self._stats["datasets_ok"] += len(datasets)
            self._stats["pages_done"]  += 1

        return len(datasets)

    def _procesar_distribucion(self, job: dict) -> int:
        """Descarga y almacena las distribuciones de un dataset."""
        payload    = job["payload"]
        dataset_id = payload["dataset_id"]
        uri        = payload.get("uri", "")

        # Extraer el ID corto del slug de la URI para construir la URL de la API
        slug = dataset_id  # ya es el slug procesado
        url  = f"{API_BASE}/catalog/distribution/dataset/{slug}"

        resp = get(url)
        if resp is None:
            return 0

        distribuciones = parse_distributions_response(resp, dataset_id)
        if distribuciones:
            self.db.upsert_distributions(distribuciones)

        with self._lock:
            self._stats["distribs_ok"] += len(distribuciones)

        return len(distribuciones)

    def _procesar_borme_dia(self, job: dict) -> int:
        """
        Descarga el sumario BORME de un día concreto, lo guarda en disco
        y persiste los actos/anuncios en la BD.
        """
        payload   = job["payload"]
        fecha     = payload["fecha"]          # YYYY-MM-DD
        files_dir = payload.get("files_dir")  # puede ser None → usa el default

        self.db.borme_mark_start(fecha)

        try:
            raw = fetch_sumario(fecha)
        except SinBorme:
            self.db.borme_mark_sin_borme(fecha)
            log.debug(f"[borme] {fecha}: sin BORME (festivo o fin de semana)")
            return 0

        parsed    = parse_sumario(fecha, raw)
        ruta      = guardar_sumario(fecha, raw, base_dir=files_dir)
        todos_actos = parsed["actos"] + parsed["anuncios"]

        self.db.borme_mark_done(
            fecha,
            numero    = parsed["numero"],
            n_actos   = len(parsed["actos"]),
            n_anuncios= len(parsed["anuncios"]),
            local_path= ruta,
        )

        if todos_actos:
            self.db.borme_upsert_actos(todos_actos)

        with self._lock:
            self._stats["borme_dias_ok"]   = self._stats.get("borme_dias_ok", 0) + 1
            self._stats["borme_actos_ok"]  = (
                self._stats.get("borme_actos_ok", 0) + len(todos_actos)
            )

        log.debug(
            f"[borme] {fecha}: nº{parsed['numero']} | "
            f"{len(parsed['actos'])} actos | {len(parsed['anuncios'])} anuncios"
        )
        return len(todos_actos)

    def _procesar_borme_acto_xml(self, job: dict) -> int:
        """
        Descarga el XML de un acto BORME concreto, lo parsea
        y persiste los anuncios/empresas en la BD (tabla borme_anuncios).

        payload: {
            "acto_id":   "BORME-A-2024-1-01",
            "fecha":     "2024-01-02",
            "files_dir": "/ruta/borme"
        }
        """
        from core.borme import fetch_acto_xml, parse_acto_xml, guardar_acto_xml, SinBorme

        payload   = job["payload"]
        acto_id   = payload["acto_id"]
        fecha     = payload["fecha"]
        files_dir = payload.get("files_dir", BORME_FILES_DIR)

        self.db.borme_mark_xml_start(acto_id)

        try:
            xml_text = fetch_acto_xml(acto_id)
        except SinBorme:
            self.db.borme_mark_xml_sin_contenido(acto_id)
            log.debug(f"[borme_xml] {acto_id}: sin XML en el BOE (404)")
            return 0

        local_path = guardar_acto_xml(acto_id, xml_text, base_dir=files_dir)
        parsed     = parse_acto_xml(xml_text, acto_id)
        anuncios   = parsed["anuncios"]

        filas = []
        for a in anuncios:
            filas.append({
                "id":                a["id"],
                "acto_id":           acto_id,
                "fecha":             fecha,
                "empresa":           a.get("empresa"),
                "datos_registrales": a.get("datos_registrales"),
                "actos_json":        json.dumps(a.get("actos", []), ensure_ascii=False),
            })

        if filas:
            self.db.borme_upsert_anuncios(filas)

        self.db.borme_mark_xml_done(acto_id, local_path)

        with self._lock:
            self._stats["borme_xml_ok"]      = self._stats.get("borme_xml_ok", 0) + 1
            self._stats["borme_anuncios_ok"] = (
                self._stats.get("borme_anuncios_ok", 0) + len(filas)
            )

        log.debug(f"[borme_xml] {acto_id}: {len(filas)} anuncios guardados")
        return len(filas)

    def _procesar_file_download(self, job: dict) -> int:
        """
        Descarga el fichero físico de una distribución.
        El payload contiene: row_id, distribution_id, dataset_id, url, format,
        byte_size_expected, files_dir.
        """
        payload        = job["payload"]
        row_id         = payload["row_id"]
        distribution_id = payload["distribution_id"]
        dataset_id     = payload["dataset_id"]
        url            = payload["url"]
        fmt            = payload.get("format")
        expected       = payload.get("byte_size_expected")
        files_dir      = Path(payload.get("files_dir", FILES_DIR))

        self.db.mark_download_start(row_id)

        dest = build_local_path(files_dir, dataset_id, distribution_id, url, fmt)

        # Si ya existe completo (de una sesión anterior), saltar
        if dest.exists() and expected:
            try:
                expected_int = int(expected)
                if dest.stat().st_size == expected_int:
                    self.db.mark_download_skipped(row_id, "fichero ya completo en disco")
                    return 0
            except (ValueError, TypeError):
                pass

        total_bytes, sha256 = download_file(url, dest, expected_bytes=(
            int(expected) if expected else None
        ))

        self.db.mark_download_done(row_id, str(dest), total_bytes, sha256)

        with self._lock:
            self._stats["files_done"]  += 1
            self._stats["bytes_total"] += total_bytes

        return total_bytes

    def _procesar_job(self, job: dict):
        """Dispatcher central: delega al procesador correcto según job_type."""
        jtype = job["job_type"]
        jid   = job["id"]
        try:
            if jtype == "page":
                n = self._procesar_pagina(job)
                log.debug(f"[page] {jid}: {n} datasets")
            elif jtype == "distribution":
                n = self._procesar_distribucion(job)
                log.debug(f"[dist] {jid}: {n} distribuciones")
            elif jtype == "file_download":
                n = self._procesar_file_download(job)
                log.debug(f"[file] {jid}: {n:,} bytes")
            elif jtype == "borme_dia":
                n = self._procesar_borme_dia(job)
                log.debug(f"[borme] {jid}: {n} actos")
            elif jtype == "borme_acto_xml":
                n = self._procesar_borme_acto_xml(job)
                log.debug(f"[borme_xml] {jid}: {n} anuncios")
            else:
                log.warning(f"Tipo de trabajo desconocido: {jtype}")
                n = 0

            self.queue.ack(jid)

            # Persistir estadísticas en BD cada 50 acks
            with self._lock:
                total = self._stats["pages_done"] + self._stats["distribs_ok"]
            if total % 50 == 0:
                self.db.update_stats(self.session_id, **{
                    k: 0 for k in self._stats  # solo actualizar si hay delta
                })

            # Callback de progreso para la UI
            if self.on_progress:
                try:
                    with self._lock:
                        stats_snap = dict(self._stats)
                    stats_snap["pending"]  = self.queue.pending_count()
                    stats_snap["db_total"] = self.db.count_datasets()
                    self.on_progress(stats_snap)
                except Exception:
                    pass

        except Exception as e:
            log.error(f"Error procesando job {jid} ({jtype}): {e}")
            self.queue.nack(jid, str(e))
            self.db.log_error(f"{jtype}:{jid}", str(e))
            with self._lock:
                if jtype == "file_download":
                    row_id = job["payload"].get("row_id")
                    if row_id:
                        self.db.mark_download_failed(row_id, str(e))
                    self._stats["files_err"] += 1
                elif jtype == "borme_dia":
                    self.db.borme_mark_failed(job["payload"].get("fecha", ""), str(e))
                    self._stats["borme_dias_err"] = self._stats.get("borme_dias_err", 0) + 1
                else:
                    self._stats["datasets_err"] += 1

    # ── Runner principal ──────────────────────────────────────────────────────

    def run(
        self,
        workers: int = MAX_WORKERS,
        job_type: str | None = None,
        max_jobs: int | None = None,
    ) -> dict:
        """
        Ejecuta la cola con N workers en paralelo.
        Retorna las estadísticas finales.
        """
        log.info(f"Iniciando engine: {workers} workers | tipo='{job_type or 'todos'}'")
        self._stop_event.clear()
        total_procesados = 0

        def _worker_loop():
            while not self._stop_event.is_set():
                job = self.queue.pop(job_type)
                if job is None:
                    break
                self._procesar_job(job)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_worker_loop) for _ in range(workers)]
            try:
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as e:
                        log.error(f"Worker terminado con error: {e}")
            except KeyboardInterrupt:
                log.warning("Interrupción recibida — guardando estado...")
                self._stop_event.set()

        # Persistir stats finales
        self.db.update_stats(self.session_id, **self._stats)
        self.db.set_progress("last_run_stats", self._stats)

        log.info(
            f"Engine finalizado — "
            f"{self._stats['datasets_ok']} datasets, "
            f"{self._stats['distribs_ok']} distribuciones, "
            f"{self._stats['pages_done']} páginas"
        )
        return self._stats

    def stop(self):
        """Detiene el engine de forma ordenada (guarda el estado)."""
        log.info("Deteniendo engine...")
        self._stop_event.set()

    def stats(self) -> dict:
        with self._lock:
            s = dict(self._stats)
        s["db_datasets"]      = self.db.count_datasets()
        s["db_distributions"] = self.db.count_distributions()
        s["pending_jobs"]     = self.queue.pending_count()
        s["session_id"]       = self.session_id
        return s
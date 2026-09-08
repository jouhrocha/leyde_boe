#!/usr/bin/env python3
"""
vt_scan.py — Analizador VirusTotal por línea de comandos
Uso:
  python vt_scan.py --key TU_API_KEY --files archivo1 archivo2 ...
  python vt_scan.py --key TU_API_KEY --ips 70.156.87.141 8.8.8.8
  python vt_scan.py --key TU_API_KEY --urls https://cdn.insuit.net/x
  python vt_scan.py --key TU_API_KEY --files a.exe --ips 1.2.3.4 --urls http://x.com

Requiere: pip install requests
"""

import sys
import time
import hashlib
import argparse
import json
import os
import requests

R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"
B = "\033[94m"; C = "\033[96m"; W = "\033[0m"; BOLD = "\033[1m"

BASE = "https://www.virustotal.com/api/v3"
WAIT = 16  # segundos entre peticiones (4/min)

def titulo(t):
    print(f"\n{BOLD}{C}{'─'*55}{W}")
    print(f"{BOLD}{Y}  {t}{W}")
    print(f"{BOLD}{C}{'─'*55}{W}")

def mal(m):  print(f"  {R}✗ {m}{W}")
def ok(m):   print(f"  {G}✓ {m}{W}")
def warn(m): print(f"  {Y}⚠ {m}{W}")
def info(m): print(f"  {B}→ {m}{W}")

lookup_count = 0

def vt_get(endpoint, key):
    global lookup_count
    lookup_count += 1
    print(f"  {C}[{lookup_count} lookup]{W} GET {endpoint[:80]}")
    r = requests.get(f"{BASE}{endpoint}", headers={"x-apikey": key}, timeout=60)
    return r

def vt_post(endpoint, key, **kwargs):
    global lookup_count
    lookup_count += 1
    print(f"  {C}[{lookup_count} lookup]{W} POST {endpoint}")
    r = requests.post(f"{BASE}{endpoint}", headers={"x-apikey": key}, timeout=120, **kwargs)
    return r

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def poll_analysis(analysis_id, key, max_tries=4):
    info("Esperando que VT analice (30s)...")
    time.sleep(30)
    for t in range(max_tries):
        r = vt_get(f"/analyses/{analysis_id}", key)
        if r.status_code == 200:
            data = r.json()
            if data["data"]["attributes"]["status"] == "completed":
                return data["data"]["attributes"]
        if t < max_tries - 1:
            info(f"Aún procesando, esperando 20s más...")
            time.sleep(20)
    return None

def print_verdict(name, stats, engines=None, extra=None):
    mal_count = stats.get("malicious", 0)
    sus_count = stats.get("suspicious", 0)
    total = sum(stats.values())

    if mal_count > 0:
        verdict_str = f"{R}{BOLD}MALICIOSO{W}"
    elif sus_count > 0:
        verdict_str = f"{Y}{BOLD}SOSPECHOSO{W}"
    else:
        verdict_str = f"{G}{BOLD}LIMPIO{W}"

    print(f"\n  {BOLD}{name}{W}")
    print(f"  Veredicto : {verdict_str}")
    print(f"  Detecciones: {R if mal_count>0 else W}{mal_count}{W} maliciosos, {Y if sus_count>0 else W}{sus_count}{W} sospechosos de {total} motores")

    if extra:
        for k, v in extra.items():
            info(f"{k}: {v}")

    if engines:
        detected = {k: v for k, v in engines.items()
                    if v.get("category") in ("malicious", "suspicious")}
        if detected:
            print(f"\n  {R}Motores que detectan:{W}")
            for engine, data in detected.items():
                result = data.get("result") or data.get("category")
                print(f"    {R}• {engine:<25}{W} {result}")

def scan_file(path, key):
    titulo(f"ARCHIVO: {os.path.basename(path)}")
    if not os.path.exists(path):
        mal(f"Archivo no encontrado: {path}")
        return

    size = os.path.getsize(path)
    info(f"Tamaño: {size:,} bytes")

    # 1. Hash local
    info("Calculando SHA256...")
    h = sha256_file(path)
    info(f"SHA256: {h}")

    # 2. Buscar en VT primero
    info("Consultando si VT ya conoce el hash...")
    r = vt_get(f"/files/{h}", key)

    if r.status_code == 200:
        attrs = r.json()["data"]["attributes"]
        ok("Hash encontrado en VT — sin necesidad de subir")
        stats = attrs.get("last_analysis_stats", {})
        engines = attrs.get("last_analysis_results", {})
        extra = {}
        if attrs.get("type_description"): extra["Tipo"] = attrs["type_description"]
        if attrs.get("meaningful_name"):   extra["Nombre"] = attrs["meaningful_name"]
        print_verdict(os.path.basename(path), stats, engines, extra)
        print(f"\n  {B}→ https://www.virustotal.com/gui/file/{h}{W}")

    elif r.status_code == 404:
        warn("No encontrado en VT — subiendo archivo...")
        if size > 32 * 1024 * 1024:
            mal("Archivo supera 32MB, no se puede subir con API pública")
            return
        with open(path, "rb") as f:
            r2 = vt_post("/files", key, files={"file": (os.path.basename(path), f)})
        if r2.status_code != 200:
            mal(f"Error al subir: HTTP {r2.status_code}")
            return
        analysis_id = r2.json()["data"]["id"]
        attrs = poll_analysis(analysis_id, key)
        if not attrs:
            mal("VT tardó demasiado, prueba más tarde")
            return
        stats = attrs.get("stats", {})
        engines = attrs.get("results", {})
        print_verdict(os.path.basename(path), stats, engines)
        print(f"\n  {B}→ https://www.virustotal.com/gui/file/{h}{W}")
    else:
        mal(f"Error HTTP {r.status_code}: {r.text[:200]}")

def scan_ip(ip, key):
    titulo(f"IP: {ip}")
    r = vt_get(f"/ip_addresses/{ip}", key)
    if r.status_code != 200:
        mal(f"Error HTTP {r.status_code}")
        return
    attrs = r.json()["data"]["attributes"]
    stats = attrs.get("last_analysis_stats", {})
    engines = attrs.get("last_analysis_results", {})
    extra = {}
    if attrs.get("country"):   extra["País"] = attrs["country"]
    if attrs.get("as_owner"):  extra["ASN Owner"] = attrs["as_owner"]
    if attrs.get("asn"):       extra["ASN"] = str(attrs["asn"])
    if attrs.get("network"):   extra["Red"] = attrs["network"]
    print_verdict(ip, stats, engines, extra)
    print(f"\n  {B}→ https://www.virustotal.com/gui/ip-address/{ip}{W}")

def scan_url(url, key):
    titulo(f"URL: {url[:80]}")
    # Enviar URL
    r = vt_post("/urls", key, data={"url": url})
    if r.status_code != 200:
        mal(f"Error al enviar URL: HTTP {r.status_code}")
        return
    analysis_id = r.json()["data"]["id"]
    attrs = poll_analysis(analysis_id, key)
    if not attrs:
        mal("VT tardó demasiado")
        return
    stats = attrs.get("stats", {})
    engines = attrs.get("results", {})
    print_verdict(url[:60], stats, engines)

def main():
    parser = argparse.ArgumentParser(description="Analizador VirusTotal")
    parser.add_argument("--key", required=True, help="API key de VirusTotal")
    parser.add_argument("--files", nargs="*", default=[], help="Archivos a analizar")
    parser.add_argument("--ips",   nargs="*", default=[], help="IPs a analizar")
    parser.add_argument("--urls",  nargs="*", default=[], help="URLs a analizar")
    parser.add_argument("--ips-file",  help="Fichero con IPs (una por línea)")
    parser.add_argument("--urls-file", help="Fichero con URLs (una por línea)")
    args = parser.parse_args()

    # Cargar desde ficheros si se pasan
    if args.ips_file:
        with open(args.ips_file) as f:
            args.ips += [l.strip() for l in f if l.strip()]
    if args.urls_file:
        with open(args.urls_file) as f:
            args.urls += [l.strip() for l in f if l.strip()]

    total = len(args.files) + len(args.ips) + len(args.urls)
    if total == 0:
        parser.print_help()
        sys.exit(1)

    print(f"\n{BOLD}{G}VT SCANNER — {total} elementos a analizar{W}")
    print(f"  Cuota estimada: ~{total * 2} lookups (archivos conocidos = 1 lookup c/u)")

    items_done = 0

    for path in args.files:
        scan_file(path, args.key)
        items_done += 1
        if items_done < total:
            info(f"Esperando {WAIT}s (límite API)...")
            time.sleep(WAIT)

    for ip in args.ips:
        scan_ip(ip, args.key)
        items_done += 1
        if items_done < total:
            info(f"Esperando {WAIT}s (límite API)...")
            time.sleep(WAIT)

    for url in args.urls:
        scan_url(url, args.key)
        items_done += 1
        if items_done < total:
            info(f"Esperando {WAIT}s (límite API)...")
            time.sleep(WAIT)

    print(f"\n{BOLD}{G}Completado. Lookups usados: ~{lookup_count}{W}\n")

if __name__ == "__main__":
    main()

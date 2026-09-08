#!/usr/bin/env python3
"""
deploy_vllm_webui.py
────────────────────
Instala Docker (si no está), lanza vLLM con el modelo ALIA-40B y
OpenWebUI con persistencia. Todo en GPU, sin CPU.

Uso:
    sudo python3 deploy_vllm_webui.py
"""

import os
import sys
import time
import shutil
import secrets
import subprocess
import urllib.request
import urllib.error

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN — edita aquí si necesitas cambiar puertos o nombres
# ══════════════════════════════════════════════════════════════════════════════
MODEL          = "BSC-LT/ALIA-40b-instruct-2601"
VLLM_PORT      = 8000 
WEBUI_PORT     = 3000
VLLM_NAME      = "vllm-server"
WEBUI_NAME     = "open-webui"
WEBUI_VOLUME   = "open-webui-data"
HF_CACHE       = os.path.expanduser("~/.cache/huggingface")

# Imagen vLLM — misma base para CUDA 12 y 13; el runtime NVIDIA elige el kernel
VLLM_IMAGE_CUDA12 = "vllm/vllm-openai:v0.9.0"   # CUDA 12.x
VLLM_IMAGE_CUDA13 = "vllm/vllm-openai:v0.9.0"   # CUDA 13.x (misma imagen, distinto driver)

WEBUI_IMAGE    = "ghcr.io/open-webui/open-webui:main"

# Clave secreta fija generada una sola vez en este script
WEBUI_SECRET_KEY = secrets.token_hex(32)

# ══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ══════════════════════════════════════════════════════════════════════════════
def banner(title: str):
    print(f"\n{'═' * 62}")
    print(f"  {title}")
    print('═' * 62)

def run(cmd, *, check=True, capture=False, shell=False):
    display = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"  $ {display}")
    if capture:
        return subprocess.run(
            cmd, shell=shell or isinstance(cmd, str),
            capture_output=True, text=True
        )
    subprocess.run(
        cmd, shell=shell or isinstance(cmd, str), check=check
    )

# ══════════════════════════════════════════════════════════════════════════════
# PASO 0 — Verificar root
# ══════════════════════════════════════════════════════════════════════════════
if os.geteuid() != 0:
    print("✗  Ejecuta el script con sudo o como root.")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# PASO 1 — Arrancar Docker dentro del contenedor (DinD)
# ══════════════════════════════════════════════════════════════════════════════
banner("1 · Arrancando Docker (modo privileged)")

def start_docker_daemon():
    print("  Iniciando dockerd dentro del contenedor...")

    subprocess.Popen(
        [
            "dockerd",
            "--host=unix:///var/run/docker.sock",
            "--storage-driver=overlay2"
        ],
        stdout=open("/tmp/dockerd.log", "w"),
        stderr=subprocess.STDOUT,
    )

    # Esperar a que Docker responda
    for i in range(30):
        time.sleep(2)
        r = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        if r.returncode == 0:
            print("  ✓  Docker daemon listo.")
            return

    print("  ✗  Docker no arrancó. Mira /tmp/dockerd.log")
    sys.exit(1)

# Arrancar Docker SIEMPRE (porque estás dentro de contenedor)
start_docker_daemon()

# ══════════════════════════════════════════════════════════════════════════════
# PASO 2 — Detectar GPUs y versión CUDA
# ══════════════════════════════════════════════════════════════════════════════
banner("2 · Detectando GPUs y versión CUDA")

if not shutil.which("nvidia-smi"):
    print("✗  nvidia-smi no encontrado. Se requieren GPUs NVIDIA con driver instalado.")
    sys.exit(1)

# Lista de GPUs
gpu_info = run(
    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
    capture=True
)
if gpu_info.returncode != 0 or not gpu_info.stdout.strip():
    print("✗  No se detectaron GPUs. Abortando.")
    sys.exit(1)

gpus = [g.strip() for g in gpu_info.stdout.strip().splitlines() if g.strip()]
num_gpus = len(gpus)
print(f"  GPUs encontradas: {num_gpus}")
for i, g in enumerate(gpus):
    print(f"    [{i}] {g}")

# Versión CUDA desde la cabecera de nvidia-smi
smi_full = run(["nvidia-smi"], capture=True)
cuda_version = None
for line in smi_full.stdout.splitlines():
    if "CUDA Version:" in line:
        try:
            cuda_version = line.split("CUDA Version:")[1].strip().split()[0]
        except IndexError:
            pass
        break

if cuda_version:
    cuda_major = int(cuda_version.split(".")[0])
    print(f"  CUDA detectado: {cuda_version}  (major={cuda_major})")
else:
    cuda_major = 12
    print("  No se pudo leer la versión CUDA. Asumiendo CUDA 12.")

# Selección de imagen
vllm_image = VLLM_IMAGE_CUDA12 if cuda_major <= 12 else VLLM_IMAGE_CUDA13
print(f"  Imagen vLLM seleccionada: {vllm_image}")
print(f"  Tensor-parallel-size: {num_gpus}  (todas las GPUs)")

# ══════════════════════════════════════════════════════════════════════════════
# PASO 3 — Caché HuggingFace
# ══════════════════════════════════════════════════════════════════════════════
banner("3 · Caché HuggingFace")

os.makedirs(HF_CACHE, exist_ok=True)
print(f"  Directorio caché: {HF_CACHE}  (modelo público, sin token)")

# ══════════════════════════════════════════════════════════════════════════════
# PASO 4 — Volumen Docker para OpenWebUI
# ══════════════════════════════════════════════════════════════════════════════
banner("4 · Volumen de persistencia OpenWebUI")

run(["docker", "volume", "create", WEBUI_VOLUME], check=False)
print(f"  Volumen '{WEBUI_VOLUME}' listo.")
print("  (Chats, usuarios, configuración y archivos sobreviven reinicios.)")

# ══════════════════════════════════════════════════════════════════════════════
# PASO 5 — Eliminar contenedores anteriores
# ══════════════════════════════════════════════════════════════════════════════
banner("5 · Limpiando contenedores anteriores")

for name in [VLLM_NAME, WEBUI_NAME]:
    run(["docker", "rm", "-f", name], check=False)
    print(f"  Eliminado (si existía): {name}")

# ══════════════════════════════════════════════════════════════════════════════
# PASO 6 — Descargar imágenes
# ══════════════════════════════════════════════════════════════════════════════
banner("6 · Descargando imágenes Docker")

run(["docker", "pull", vllm_image])
run(["docker", "pull", WEBUI_IMAGE])

# ══════════════════════════════════════════════════════════════════════════════
# PASO 7 — Lanzar vLLM
# ══════════════════════════════════════════════════════════════════════════════
banner("7 · Lanzando vLLM")

# Comandos fieles a la documentación provista, más flags de producción:
#   --dtype bfloat16            → igual que el modelo original usa torch.bfloat16
#   --tensor-parallel-size N    → reparte el modelo entre todas las GPUs
#   --trust-remote-code         → necesario para modelos con código personalizado
vllm_cmd = [
    "docker", "run", "-d",
    "--gpus", "all",
    "-v", f"{HF_CACHE}:/root/.cache/huggingface",
    "-p", f"{VLLM_PORT}:8000",
    "--ipc=host",
    "--name", VLLM_NAME,
    "--restart", "unless-stopped",
    vllm_image,
    "--model", MODEL,
    "--dtype", "bfloat16",
    "--tensor-parallel-size", str(num_gpus),
    "--trust-remote-code",
]

run(vllm_cmd)
print(f"✓  vLLM arrancando → http://localhost:{VLLM_PORT}")

# ══════════════════════════════════════════════════════════════════════════════
# PASO 8 — Esperar a que vLLM esté listo
# ══════════════════════════════════════════════════════════════════════════════
banner("8 · Esperando a que vLLM cargue el modelo")
print("  ALIA-40B es un modelo grande. Puede tardar varios minutos...")
print("  (Comprueba el progreso con: docker logs -f vllm-server)\n")

MAX_WAIT_SECONDS = 900   # 15 minutos máximo
POLL_INTERVAL    = 15
elapsed          = 0

while elapsed < MAX_WAIT_SECONDS:
    try:
        urllib.request.urlopen(
            f"http://localhost:{VLLM_PORT}/health", timeout=5
        )
        print(f"\n  ✓  vLLM listo tras {elapsed}s")
        break
    except (urllib.error.URLError, OSError):
        print(f"  ... {elapsed}s / {MAX_WAIT_SECONDS}s", end="\r")
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
else:
    print(
        "\n  ⚠  vLLM no respondió en el tiempo límite.\n"
        "     OpenWebUI se lanzará igualmente; conéctate más tarde."
    )

# ══════════════════════════════════════════════════════════════════════════════
# PASO 9 — Lanzar OpenWebUI
# ══════════════════════════════════════════════════════════════════════════════
banner("9 · Lanzando OpenWebUI")

# host.docker.internal → resuelve a la IP del host desde dentro del contenedor
# OPENAI_API_BASE_URL  → apunta al endpoint OpenAI-compatible de vLLM
webui_cmd = [
    "docker", "run", "-d",
    "--gpus", "all",
    "-p", f"{WEBUI_PORT}:8080",
    "-v", f"{WEBUI_VOLUME}:/app/backend/data",
    "-e", f"WEBUI_SECRET_KEY={WEBUI_SECRET_KEY}",
    "-e", f"OPENAI_API_BASE_URL=http://host.docker.internal:{VLLM_PORT}/v1",
    "-e", "OPENAI_API_KEY=ignored",
    "--add-host=host.docker.internal:host-gateway",
    "--name", WEBUI_NAME,
    "--restart", "always",
    WEBUI_IMAGE,
]

run(webui_cmd)
print(f"✓  OpenWebUI arrancando → http://localhost:{WEBUI_PORT}")

# ══════════════════════════════════════════════════════════════════════════════
# RESUMEN FINAL
# ══════════════════════════════════════════════════════════════════════════════
banner("✓  Despliegue completado")

print(f"""
  SERVICIO        URL
  ─────────────── ──────────────────────────────────
  vLLM API        http://localhost:{VLLM_PORT}
  OpenWebUI       http://localhost:{WEBUI_PORT}

  MODELO          {MODEL}
  GPUs            {num_gpus}x  (tensor-parallel, todas las GPUs)
  CUDA            {cuda_version or 'auto'}
  Imagen vLLM     {vllm_image}

  PERSISTENCIA    volumen Docker '{WEBUI_VOLUME}'
                  (chats, usuarios, config, archivos)

  WEBUI_SECRET_KEY  {WEBUI_SECRET_KEY}
  ⚠  Guarda esta clave. Sin ella cada recreación del
     contenedor cierra todas las sesiones.

  LOGS
    docker logs -f {VLLM_NAME}
    docker logs -f {WEBUI_NAME}

  ACTUALIZAR OpenWebUI (datos preservados):
    docker rm -f {WEBUI_NAME}
    docker pull {WEBUI_IMAGE}
    docker run -d --gpus all -p {WEBUI_PORT}:8080 \\
      -v {WEBUI_VOLUME}:/app/backend/data \\
      -e WEBUI_SECRET_KEY={WEBUI_SECRET_KEY} \\
      -e OPENAI_API_BASE_URL=http://host.docker.internal:{VLLM_PORT}/v1 \\
      -e OPENAI_API_KEY=ignored \\
      --add-host=host.docker.internal:host-gateway \\
      --name {WEBUI_NAME} --restart always {WEBUI_IMAGE}
""")
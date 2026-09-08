import os
import re
import ast
from pathlib import Path

# Configuración de rutas
BASE_DIR = Path(".")
ARCHIVOS_FISICOS = [f.name for f in BASE_DIR.iterdir() if f.is_file()]

# Intentar detectar cuál es el punto de entrada real de Flask
puntos_entrada = ['server.py', 'servidor.py', 'main.py']
punto_inicial = next((p for p in puntos_entrada if p in ARCHIVOS_FISICOS), None)

if not punto_inicial:
    print("❌ No se encontró un punto de entrada claro (server.py, servidor.py o main.py).")
    print("Por favor, edita este script y pon el nombre correcto en 'punto_inicial'.")
    exit(1)

print(f"🚀 Punto de entrada detectado para Flask: {punto_inicial}\n")

# Conjuntos para almacenar lo que descubramos
modulos_python_usados = set([punto_inicial])
htmls_usados = set()
archivos_estaticos_usados = set()

# --- 1. RASTREAR IMPORTS EN PYTHON (Recursivo) ---
def analizar_imports_recursivo(archivo_py):
    ruta = BASE_DIR / archivo_py
    if not ruta.exists():
        return
    
    try:
        with open(ruta, 'r', encoding='utf-8', errors='ignore') as f:
            tree = ast.parse(f.read(), filename=archivo_py)
            
        for node in ast.walk(tree):
            # Caso: import api_boe
            if isinstance(node, ast.Import):
                for n in node.names:
                    nombre_mod = f"{n.name}.py"
                    if nombre_mod in ARCHIVOS_FISICOS and nombre_mod not in modulos_python_usados:
                        modulos_python_usados.add(nombre_mod)
                        analizar_imports_recursivo(nombre_mod)
            # Caso: from borme_parser import X
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    nombre_mod = f"{node.module}.py"
                    if nombre_mod in ARCHIVOS_FISICOS and nombre_mod not in modulos_python_usados:
                        modulos_python_usados.add(nombre_mod)
                        analizar_imports_recursivo(nombre_mod)
    except Exception as e:
        print(f"⚠️ Error analizando imports en {archivo_py}: {e}")

# --- 2. RASTREAR LLAMADAS A PLANTILLAS Y STATICS EN FLASK ---
def analizar_referencias_en_flask(archivo_py):
    ruta = BASE_DIR / archivo_py
    if not ruta.exists():
        return
    try:
        with open(ruta, 'r', encoding='utf-8', errors='ignore') as f:
            contenido = f.read()
            
            # Buscar render_template('archivo.html')
            templates = re.findall(r"render_template\s*\(\s*['\"]([^'\"]+\.html)['\"]", contenido)
            for t in templates:
                if t in ARCHIVOS_FISICOS:
                    htmls_usados.add(t)
                    
            # Buscar menciones directas a otros archivos (como los .svg o .zip pesados)
            for archivo in ARCHIVOS_FISICOS:
                if archivo in contenido and archivo != archivo_py:
                    if archivo.endswith('.html'):
                        htmls_usados.add(archivo)
                    elif archivo.endswith(('.svg', '.zip', '.py')):
                        archivos_estaticos_usados.add(archivo)
    except Exception as e:
        print(f"⚠️ Error analizando texto en {archivo_py}: {e}")

# --- 3. RASTREAR ENLACES ENTRE ARCHIVOS HTML ---
def analizar_htmls_recursivo():
    # Revisamos los HTMLs descubiertos hasta ahora para ver si llaman a otros HTMLs
    cambios = True
    while cambios:
        tamanio_inicial = len(htmls_usados)
        for html in list(htmls_usados):
            ruta = BASE_DIR / html
            if not ruta.exists():
                continue
            try:
                with open(ruta, 'r', encoding='utf-8', errors='ignore') as f:
                    contenido = f.read()
                    # Buscar href="archivo.html" o src="archivo.svg"
                    enlaces = re.findall(r"href=['\"]([^'\"]+)['\"]|src=['\"]([^'\"]+)['\"]", contenido)
                    for e in enlaces:
                        url = e[0] if e[0] else e[1]
                        # Limpiar posibles rutas relativas sencillas
                        nombre_limpio = os.path.basename(url)
                        if nombre_limpio in ARCHIVOS_FISICOS:
                            if nombre_limpio.endswith('.html'):
                                htmls_usados.add(nombre_limpio)
                            else:
                                archivos_estaticos_usados.add(nombre_limpio)
            except:
                pass
        cambios = len(htmls_usados) > tamanio_inicial

# Ejecutar el motor de análisis
analizar_imports_recursivo(punto_inicial)
# Si tienes varios archivos de servidor posibles, auditamos los imports de todos los sospechosos principales por si acaso
for p in puntos_entrada:
    if p in ARCHIVOS_FISICOS:
        analizar_imports_recursivo(p)
        analizar_referencias_en_flask(p)

analizar_htmls_recursivo()

# Cruzar datos con el almacenamiento real
todos_los_usados = modulos_python_usados.union(htmls_usados).union(archivos_estaticos_usados)
todos_los_usados.add('requirements.txt') # Exclusión lógica
todos_los_usados.add('filtrar_recientes.py')
todos_los_usados.add('auditar_proyecto.py')

archivos_no_usados = [f for f in ARCHIVOS_FISICOS if f not in todos_los_usados]

# --- REPORTE FINAL DE AUDITORÍA ---
print("==================================================")
print("📊 RESULTADOS DEL ANÁLISIS DE DEPENDENCIAS")
print("==================================================")
print(f"✅ Archivos Python En Uso ({len(modulos_python_usados)}):")
for m in sorted(modulos_python_usados):
    print(f"  - {m}")

print(f"\n✅ Archivos HTML En Uso ({len(htmls_usados)}):")
for h in sorted(htmls_usados):
    print(f"  - {h}")

print(f"\n🗑️ ARCHIVOS COJIDOS QUE PARECEN NO ESTAR EN USO ({len(archivos_no_usados)}):")
print("*(Podrían ser scripts sueltos de pruebas, backups manuales o zips antiguos)*")
for n in sorted(archivos_no_usados):
    # Calcular tamaño aproximado para ayudarte a priorizar qué limpiar
    peso = os.path.getsize(BASE_DIR / n) / (1024 * 1024)
    print(f"  ❌ {n} ({peso:.2f} MB)")
print("==================================================")

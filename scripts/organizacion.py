import os
import re
import shutil
from pathlib import Path

# Directorio actual (donde están tus archivos)
BASE_DIR = Path(".")
BACKUP_DIR = BASE_DIR / "versiones_antiguas"
BACKUP_DIR.mkdir(exist_ok=True)

# Expresión regular para detectar el patrón de duplicados como "archivo (1).py"
pattern = re.compile(r"^(.*?)\s*\(\d+\)(\.[^.]+)?$")

# Diccionario para agrupar los archivos por su "nombre base" real
grupos = {}

for archivo in BASE_DIR.iterdir():
    if archivo.is_dir() or archivo == Path(__file__):
        continue
        
    nombre = archivo.name
    match = pattern.match(nombre)
    
    if match:
        # Es un duplicado (e.g., 'api_boe (1).py' -> base: 'api_boe', ext: '.py')
        base_name = match.group(1) + (match.group(2) if match.group(2) else "")
    else:
        # Es el archivo original/limpio
        base_name = nombre
        
    if base_name not in grupos:
        grupos[base_name] = []
    grupos[base_name].append(archivo)

print("🔍 Analizando fechas de modificación...")

# Procesar cada grupo para dejar solo el más nuevo en la raíz
for base_name, archivos in grupos.items():
    if len(archivos) == 1:
        # Si no tiene duplicados, se comprueba si tiene el nombre limpio
        archivo_unico = archivos[0]
        if archivo_unico.name != base_name:
            print(r"✏️ Renombrando único: {archivo_unico.name} -> {base_name}")
            shutil.move(archivo_unico, BASE_DIR / base_name)
        continue
        
    # Ordenar los archivos del grupo por fecha de modificación (el más nuevo al final)
    archivos.sort(key=lambda x: os.path.getmtime(x))
    
    el_mas_nuevo = archivos[-1]
    los_antiguos = archivos[:-1]
    
    fecha_nuevo = os.path.getmtime(el_mas_nuevo)
    
    print(f"\n📦 Grupo: {base_name}")
    print(f"  ✅ EL MÁS RECIENTE: {el_mas_nuevo.name} (Conservado en la raíz)")
    
    # Mover los antiguos a la carpeta de backup
    for antiguo in los_antiguos:
        print(f"  ❌ Antiguo: {antiguo.name} -> movido a versiones_antiguas/")
        shutil.move(antiguo, BACKUP_DIR / antiguo.name)
        
    # Finalmente, nos aseguramos de que el más nuevo se quede con el nombre limpio sin el "(1)"
    if el_mas_nuevo.name != base_name:
        dest_final = BASE_DIR / base_name
        # Si ya existía un archivo con el nombre base (que era más antiguo y ya se movió), se renombra el nuevo
        shutil.move(el_mas_nuevo, dest_final)
        print(f"  🔀 Renombrado a su nombre original: {base_name}")

print("\n✨ ¡Limpieza completada! Las versiones más recientes están listas en la raíz.")

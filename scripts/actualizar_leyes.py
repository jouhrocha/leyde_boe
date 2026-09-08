#!/usr/bin/env python3
"""
Actualizador de Leyes BOE
Descarga y actualiza la base de datos legislativa desde el BOE
"""

import sqlite3
import requests
import re
import time
import json
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
import sys

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "leyes_boe.db"

class ActualizadorBOE:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
        })
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.setup_db()
    
    def setup_db(self):
        """Crea las tablas si no existen"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS articulos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ley TEXT NOT NULL,
                codigo_boe TEXT,
                articulo TEXT,
                texto TEXT,
                url TEXT,
                fecha_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_ley ON articulos(ley);
            CREATE INDEX IF NOT EXISTS idx_articulo ON articulos(articulo);
            
            CREATE TABLE IF NOT EXISTS actualizaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                articulos_nuevos INTEGER,
                estado TEXT
            );
        """)
        self.conn.commit()
    
    def obtener_leyes_consolidadas(self):
        """Obtiene listado de leyes consolidadas del BOE"""
        print("[1/4] Obteniendo listado de leyes consolidadas...")
        
        # Códigos BOE de las principales leyes
        leyes = [
            ("Ley 39/2015", "BOE-A-2015-10566", "Procedimiento Administrativo Común"),
            ("Ley 40/2015", "BOE-A-2015-10567", "Régimen Jurídico del Sector Público"),
            ("Real Decreto Legislativo 1/1996", "BOE-A-1996-8930", "Texto Refundido de la Ley de Sociedades de Capital"),
            ("Real Decreto Legislativo 2/2004", "BOE-A-2004-2934", "Texto Refundido de la Ley Reguladora de las Haciendas Locales"),
            ("Real Decreto Legislativo 5/2015", "BOE-A-2015-12023", "Texto Refundido de la Ley del Estatuto Básico del Empleado Público"),
            ("Real Decreto Legislativo 8/2015", "BOE-A-2015-12024", "Texto Refundido de la Ley General de la Seguridad Social"),
            ("Ley 2/2015", "BOE-A-2015-27", "Ley de Desindexación de la Economía Española"),
            ("Ley 22/2013", "BOE-A-2013-12881", "Ley de Presupuestos Generales del Estado para el año 2014"),
            ("Real Decreto Legislativo 1/2013", "BOE-A-2013-12913", "Ley General de derechos de las personas con discapacidad"),
            ("Ley 1/2000", "BOE-A-2000-323", "Ley de Enjuiciamiento Civil"),
        ]
        
        print(f"      Preparadas {len(leyes)} leyes principales")
        return leyes
    
    def descargar_ley(self, nombre_ley, codigo_boe, descripcion):
        """Descarga una ley específica del BOE"""
        try:
            # URL del BOE para acceder a la ley
            url = f"https://www.boe.es/buscar/act.php?id={codigo_boe}"
            response = self.session.get(url, timeout=30)
            
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Buscar el texto de la ley
            contenido = soup.find('div', class_='texto')
            if not contenido:
                contenido = soup.find('div', id='texto')
            
            if contenido:
                return contenido.get_text(separator='\n')
            
            return response.text
            
        except Exception as e:
            print(f"      Error descargando {nombre_ley}: {e}")
            return None
    
    def extraer_articulos(self, texto, nombre_ley):
        """Extrae artículos del texto de una ley"""
        articulos = []
        if not texto:
            return articulos
        
        # Patrón para encontrar artículos
        patron = r'(?:Artículo|Art\.)\s+(\d+)[.\s]+(.*?)(?=(?:Artículo|Art\.)\s+\d+|\Z)'
        matches = re.finditer(patron, texto, re.DOTALL | re.IGNORECASE)
        
        for match in matches:
            num_articulo = match.group(1)
            texto_articulo = match.group(2).strip()[:5000]  # Limitar longitud
            
            articulos.append({
                'ley': nombre_ley,
                'articulo': f"Artículo {num_articulo}",
                'texto': texto_articulo,
                'url': f"https://www.boe.es/buscar/act.php?id={nombre_ley.replace(' ', '_')}"
            })
        
        return articulos
    
    def actualizar(self):
        """Proceso completo de actualización"""
        print("=" * 60)
        print("ACTUALIZADOR DE LEYES BOE")
        print("=" * 60)
        print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        leyes = self.obtener_leyes_consolidadas()
        
        print(f"[2/4] Procesando {len(leyes)} leyes...")
        
        total_articulos = 0
        errores = 0
        
        for i, (nombre_ley, codigo_boe, descripcion) in enumerate(leyes, 1):
            print(f"      [{i}/{len(leyes)}] {nombre_ley}...")
            
            texto = self.descargar_ley(nombre_ley, codigo_boe, descripcion)
            
            if texto:
                articulos = self.extraer_articulos(texto, nombre_ley)
                
                for art in articulos:
                    self.conn.execute("""
                        INSERT INTO articulos (ley, articulo, texto, url)
                        VALUES (?, ?, ?, ?)
                    """, (art['ley'], art['articulo'], art['texto'], art['url']))
                    total_articulos += 1
                
                self.conn.commit()
                print(f"            {len(articulos)} artículos extraídos")
            else:
                errores += 1
            
            time.sleep(1)  # Peticiones suaves al BOE
        
        # Registrar actualización
        print(f"[3/4] Registrando actualización...")
        self.conn.execute("""
            INSERT INTO actualizaciones (articulos_nuevos, estado)
            VALUES (?, ?)
        """, (total_articulos, f"OK" if errores == 0 else f"{errores} errores"))
        self.conn.commit()
        
        # Estadísticas finales
        print(f"[4/4] Actualización completada")
        print()
        print("=" * 60)
        print(f"Artículos nuevos: {total_articulos}")
        print(f"Errores: {errores}")
        
        stats = self.conn.execute("SELECT COUNT(*) FROM articulos").fetchone()[0]
        print(f"Total artículos en BD: {stats}")
        print("=" * 60)
        
        self.conn.close()
        
        return total_articulos

def main():
    actualizador = ActualizadorBOE()
    actualizador.actualizar()

if __name__ == '__main__':
    main()

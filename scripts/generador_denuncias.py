#!/usr/bin/env python3
"""
Generador de Denuncias Profesionales
Crea documentos de denuncia listos para presentar
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from fpdf import FPDF
import json

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "leyes_boe.db"
OUTPUT_DIR = BASE / "denuncias_generadas"

class GeneradorDenuncias:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row
        OUTPUT_DIR.mkdir(exist_ok=True)
    
    def buscar_leyes_aplicables(self, tipo_denuncia, descripcion):
        """Busca leyes aplicables según el tipo de denuncia"""
        
        # Mapeo de tipos de denuncia con palabras clave
        keywords_map = {
            'laboral': ['trabajador', 'despido', 'salario', 'jornada', 'acoso', 'discriminación'],
            'consumo': ['consumidor', 'producto', 'servicio', 'garantía', 'devolución'],
            'vivienda': ['alquiler', 'desahucio', 'comunidad', 'propiedad', 'hipoteca'],
            'accidente': ['responsabilidad', 'daños', 'indemnización', 'seguro'],
            'estafa': ['fraude', 'estafa', 'timo', 'engaño', 'falsedad'],
            'amenazas': ['amenazas', 'coacciones', 'violencia', 'maltrato'],
            'robo': ['robo', 'hurto', 'sustracción', 'propiedad'],
            'ruidos': ['ruidos', 'molestias', 'contaminación', 'actividades molestas'],
            'discriminacion': ['discriminación', 'igualdad', 'género', 'origen', 'discapacidad'],
            'proteccion_datos': ['datos personales', 'LOPD', 'privacidad', 'RGPD'],
        }
        
        keywords = keywords_map.get(tipo_denuncia, [tipo_denuncia])
        
        leyes_encontradas = []
        for keyword in keywords:
            rows = self.conn.execute("""
                SELECT DISTINCT ley, COUNT(*) as total 
                FROM articulos 
                WHERE texto LIKE ? OR articulo LIKE ?
                GROUP BY ley 
                ORDER BY total DESC 
                LIMIT 5
            """, (f'%{keyword}%', f'%{keyword}%')).fetchall()
            
            for row in rows:
                if row['ley'] not in [l['ley'] for l in leyes_encontradas]:
                    leyes_encontradas.append({
                        'ley': row['ley'],
                        'articulos': row['total']
                    })
        
        return leyes_encontradas[:10]
    
    def obtener_articulos_ley(self, nombre_ley, limite=5):
        """Obtiene artículos de una ley"""
        rows = self.conn.execute("""
            SELECT articulo, texto 
            FROM articulos 
            WHERE ley = ? 
            LIMIT ?
        """, (nombre_ley, limite)).fetchall()
        
        return [{'articulo': r['articulo'], 'texto': r['texto'][:500]} for r in rows]
    
    def generar_denuncia_pdf(self, datos):
        """Genera un PDF de denuncia profesional"""
        
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=25)
        
        # Encabezado
        pdf.set_font('Helvetica', 'B', 16)
        pdf.cell(0, 15, 'DENUNCIA', 0, 1, 'C')
        pdf.ln(5)
        
        # Fecha y lugar
        pdf.set_font('Helvetica', '', 11)
        fecha = datetime.now().strftime('%d de %B de %Y')
        pdf.cell(0, 10, f'En {datos.get("ciudad", "__________")}, a {fecha}', 0, 1, 'R')
        pdf.ln(10)
        
        # Destinatario
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 10, 'A LA AUTORIDAD COMPETENTE', 0, 1, 'L')
        pdf.set_font('Helvetica', '', 11)
        pdf.cell(0, 10, f'{datos.get("destinatario", "Juzgado / Comisaría / Organismo competente")}', 0, 1, 'L')
        pdf.ln(10)
        
        # Datos del denunciante
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 10, '1. DATOS DEL DENUNCIANTE', 0, 1, 'L')
        pdf.set_font('Helvetica', '', 11)
        pdf.ln(3)
        
        campos = [
            ('Nombre completo', datos.get('nombre', '__________')),
            ('DNI/NIE', datos.get('dni', '__________')),
            ('Domicilio', datos.get('domicilio', '__________')),
            ('Teléfono', datos.get('telefono', '__________')),
            ('Email', datos.get('email', '__________')),
        ]
        
        for label, valor in campos:
            pdf.set_font('Helvetica', 'B', 11)
            pdf.cell(45, 8, f'{label}:', 0, 0)
            pdf.set_font('Helvetica', '', 11)
            pdf.cell(0, 8, valor, 0, 1)
        
        pdf.ln(10)
        
        # Datos del denunciado
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 10, '2. DATOS DEL DENUNCIADO', 0, 1, 'L')
        pdf.set_font('Helvetica', '', 11)
        pdf.ln(3)
        
        campos_denunciado = [
            ('Nombre/Razón social', datos.get('denunciado_nombre', '__________')),
            ('DNI/CIF', datos.get('denunciado_dni', '__________')),
            ('Domicilio', datos.get('denunciado_domicilio', '__________')),
        ]
        
        for label, valor in campos_denunciado:
            pdf.set_font('Helvetica', 'B', 11)
            pdf.cell(45, 8, f'{label}:', 0, 0)
            pdf.set_font('Helvetica', '', 11)
            pdf.cell(0, 8, valor, 0, 1)
        
        pdf.ln(10)
        
        # Hechos
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 10, '3. HECHOS', 0, 1, 'L')
        pdf.set_font('Helvetica', '', 11)
        pdf.ln(3)
        
        hechos = datos.get('hechos', 'Describir aquí los hechos de forma detallada y cronológica.')
        pdf.multi_cell(0, 7, hechos)
        pdf.ln(10)
        
        # Fundamentos de derecho
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 10, '4. FUNDAMENTOS DE DERECHO', 0, 1, 'L')
        pdf.set_font('Helvetica', '', 11)
        pdf.ln(3)
        
        leyes = datos.get('leyes_aplicables', [])
        if leyes:
            pdf.multi_cell(0, 7, 'Resultan de aplicación las siguientes normas:')
            pdf.ln(3)
            for i, ley in enumerate(leyes, 1):
                pdf.set_font('Helvetica', 'B', 11)
                pdf.cell(0, 7, f'{i}. {ley["ley"]}', 0, 1)
                pdf.set_font('Helvetica', '', 10)
                pdf.multi_cell(0, 6, f'   Artículos relevantes: {ley.get("articulo", "Ver legislación")}')
                pdf.ln(2)
        else:
            pdf.multi_cell(0, 7, 'Especificar aquí los fundamentos jurídicos aplicables.')
        
        pdf.ln(10)
        
        # Petición
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 10, '5. PETICIÓN', 0, 1, 'L')
        pdf.set_font('Helvetica', '', 11)
        pdf.ln(3)
        
        peticion = datos.get('peticion', 'Por todo lo expuesto, se solicita la admisión de la presente denuncia y la adopción de las medidas oportunas.')
        pdf.multi_cell(0, 7, peticion)
        pdf.ln(15)
        
        # Firma
        pdf.set_font('Helvetica', '', 11)
        pdf.cell(0, 10, 'Firma del denunciante:', 0, 1, 'R')
        pdf.ln(20)
        pdf.cell(0, 10, '_' * 40, 0, 1, 'R')
        
        # Anexos
        if datos.get('anexos'):
            pdf.add_page()
            pdf.set_font('Helvetica', 'B', 12)
            pdf.cell(0, 10, 'ANEXOS', 0, 1, 'L')
            pdf.set_font('Helvetica', '', 11)
            pdf.ln(5)
            for anexo in datos['anexos']:
                pdf.cell(0, 7, f'- {anexo}', 0, 1)
        
        # Guardar PDF
        nombre_archivo = f"denuncia_{datos.get('tipo', 'general')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        ruta_salida = OUTPUT_DIR / nombre_archivo
        pdf.output(str(ruta_salida))
        
        return ruta_salida
    
    def generar_denuncia_texto(self, datos):
        """Genera denuncia en formato texto"""
        
        fecha = datetime.now().strftime('%d de %B de %Y')
        
        texto = f"""
{'='*70}
                        DENUNCIA
{'='*70}

En {datos.get('ciudad', '__________')}, a {fecha}

A LA AUTORIDAD COMPETENTE
{datos.get('destinatario', 'Juzgado / Comisaría / Organismo competente')}


1. DATOS DEL DENUNCIANTE
   Nombre: {datos.get('nombre', '__________')}
   DNI/NIE: {datos.get('dni', '__________')}
   Domicilio: {datos.get('domicilio', '__________')}
   Teléfono: {datos.get('telefono', '__________')}
   Email: {datos.get('email', '__________')}


2. DATOS DEL DENUNCIADO
   Nombre/Razón social: {datos.get('denunciado_nombre', '__________')}
   DNI/CIF: {datos.get('denunciado_dni', '__________')}
   Domicilio: {datos.get('denunciado_domicilio', '__________')}


3. HECHOS
{datos.get('hechos', 'Describir aquí los hechos de forma detallada y cronológica.')}


4. FUNDAMENTOS DE DERECHO
Resultan de aplicación las siguientes normas:
"""
        
        leyes = datos.get('leyes_aplicables', [])
        if leyes:
            for i, ley in enumerate(leyes, 1):
                texto += f"\n   {i}. {ley['ley']}"
                if 'articulo' in ley:
                    texto += f"\n      Artículo: {ley['articulo']}"
        else:
            texto += "\n   Especificar fundamentos jurídicos aplicables."
        
        texto += f"""


5. PETICIÓN
{datos.get('peticion', 'Por todo lo expuesto, se solicita la admisión de la presente denuncia.')}


Firma del denunciante: ___________________________


{'='*70}
"""
        
        # Guardar texto
        nombre_archivo = f"denuncia_{datos.get('tipo', 'general')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        ruta_salida = OUTPUT_DIR / nombre_archivo
        ruta_salida.write_text(texto, encoding='utf-8')
        
        return ruta_salida
    
    def cerrar(self):
        self.conn.close()


def main():
    """Ejemplo de uso"""
    gen = GeneradorDenuncias()
    
    # Buscar leyes aplicables
    print("Buscando leyes aplicables para denuncia laboral...")
    leyes = gen.buscar_leyes_aplicables('laboral', 'despido improcedente')
    print(f"Encontradas {len(leyes)} leyes")
    
    # Datos de ejemplo
    datos = {
        'tipo': 'laboral',
        'ciudad': 'Madrid',
        'destinatario': 'Juzgado de lo Social Nº 1',
        'nombre': 'Juan García López',
        'dni': '12345678A',
        'domicilio': 'Calle Mayor 1, 28001 Madrid',
        'telefono': '600123456',
        'email': 'juan@email.com',
        'denunciado_nombre': 'Empresa Ejemplo S.L.',
        'denunciado_dni': 'B12345678',
        'denunciado_domicilio': 'Avda. Empresarial 100, 28001 Madrid',
        'hechos': 'El día 1 de enero de 2024 la empresa me comunicó mi despido de forma verbal...',
        'peticion': 'Se declare la improcedencia del despido y se condene al abono de las cantidades adeudadas.',
        'leyes_aplicables': leyes,
        'anexos': ['Contrato de trabajo', 'Nóminas últimos 6 meses', 'Comunicación de despido'],
    }
    
    # Generar PDF
    print("Generando PDF...")
    ruta_pdf = gen.generar_denuncia_pdf(datos)
    print(f"PDF generado: {ruta_pdf}")
    
    # Generar texto
    print("Generando texto...")
    ruta_txt = gen.generar_denuncia_texto(datos)
    print(f"Texto generado: {ruta_txt}")
    
    gen.cerrar()
    print("\n¡Denuncia generada correctamente!")

if __name__ == '__main__':
    main()

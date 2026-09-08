import fitz  # Librería principal de PyMuPDF

def exterminar_rastro_maquina_virtual(ruta_documento_origen, ruta_documento_destino):
    try:
        # Abrimos el documento PDF original proporcionado
        documento_pdf = fitz.open(ruta_documento_origen)
        
        # Definimos los fragmentos exactos que te han delatado
        # Buscamos la línea entera o la palabra clave para eliminarla
        palabras_prohibidas = ["VBoxDX.dll", "SUMMARY: AddressSanitizer: access-violation (C:\\Windows\\SYSTEM32\\VBoxDX.dll+0x18000fcb2)"]
        
        # Recorremos todas las páginas del documento
        for numero_pagina in range(len(documento_pdf)):
            pagina_actual = documento_pdf[numero_pagina]
            
            for texto_delator in palabras_prohibidas:
                # Buscamos las coordenadas (rectángulos) donde se encuentra el texto
                coordenadas_texto = pagina_actual.search_for(texto_delator)
                
                # Si encontramos coincidencias, procedemos a la destrucción de ese texto
                for rectangulo in coordenadas_texto:
                    # Añadimos una censura sobre las coordenadas exactas con relleno blanco
                    pagina_actual.add_redact_annot(rectangulo, fill=(1, 1, 1))
            
            # Aplicamos la censura permanentemente, borrando la capa de texto y visual
            pagina_actual.apply_redactions()
            
        # Guardamos el documento optimizando la estructura para que no quede rastro de la edición
        documento_pdf.save(ruta_documento_destino, garbage=4, deflate=True)
        documento_pdf.close()
        
        print("Operación completada con éxito. El PDF está limpio y el OCR original intacto.")
        
    except Exception as error_ejecucion:
        print("Se ha producido un error durante la limpieza:", error_ejecucion)

# Variables de ejecución
ruta_entrada = "OFFICIAL-REPORT-GOOGLE (1).pdf"
ruta_salida = "OFFICIAL-REPORT-GOOGLE-LIMPIO.pdf"

# Llamada a la función principal
exterminar_rastro_maquina_virtual(ruta_entrada, ruta_salida)
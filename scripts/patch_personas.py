"""
Parchea guardar_anuncios en borme_xml_parser.py
para que solo inserte en personas/empresa_persona
cuando el tipo del acto es un cargo conocido de bormeparser.

Uso: python patch_personas.py borme_xml_parser.py
"""
import sys

VIEJO = '''        for acto in actos:
            tipo = acto.get("tipo", "")
            texto = acto.get("texto", "") or ""
            if len(texto) > 200:
                continue
            nombres = [n.strip() for n in texto.split(";") if n.strip()]
            if not nombres:
                continue
            for nombre in nombres:
                pid = _upsert_persona(cur, nombre, persona_cache)
                if pid:
                    cur.execute("""
                        INSERT OR IGNORE INTO empresa_persona
                          (id_borme, id_persona, cargo, fecha)
                        VALUES (?, ?, ?, ?)
                    """, (anuncio_id, pid, tipo, fecha))
                    n_ep += 1'''

NUEVO = '''        for acto in actos:
            tipo = acto.get("tipo", "")
            texto = acto.get("texto", "") or ""
            # Solo insertar si el tipo es un cargo conocido de bormeparser
            if tipo not in _CARGOS_CONOCIDOS:
                continue
            nombres = [n.strip() for n in texto.split(";") if n.strip()]
            if not nombres:
                continue
            for nombre in nombres:
                pid = _upsert_persona(cur, nombre, persona_cache)
                if pid:
                    cur.execute("""
                        INSERT OR IGNORE INTO empresa_persona
                          (id_borme, id_persona, cargo, fecha)
                        VALUES (?, ?, ?, ?)
                    """, (anuncio_id, pid, tipo, fecha))
                    n_ep += 1'''

CONSTANTE = '''
# Cargos conocidos de bormeparser (bormeparser.cargo.CARGO)
# Solo estos tipos de acto generan entradas en personas/empresa_persona
_CARGOS_CONOCIDOS = {
    "Presidente", "Vicepresidente", "Consejero", "Secretario", "Vicesecretario",
    "Consejero delegado mancomunado", "Administrador único", "Miembro del consejo rector",
    "Presidente del consejo rector", "Secretario del consejo rector",
    "Consejero del consejo rector", "Administrador solidario", "Apoderado",
    "Socio profesional", "Apoderado mancomunado solidario", "Apoderado mancomunado",
    "Apoderado solidario", "Apoderado Soc. Uni.", "Consejero delegado mancomunado solidario",
    "Consejero delegado solidario", "Representante", "Consejero delegado",
    "Apoderado solidario mancomunado", "Liquidador solidario", "Liquidador",
    "Apoderado sucursal", "Representante art. 143 del Reglamento del Registro Mercantil",
    "Auditor de cuentas consolidadas", "Secretario no consejero", "Vicesecretario no consejero",
    "Auditor", "Auditor individual", "Administrador concurs.", "Representante administrador concurs.",
    "Auditor suplente", "Administrador conjunto", "Entidad reg. cont.", "Entidad depositaria",
    "Consejero dominic.", "Consejero ejecutivo", "Miembro de la comisión Aud.",
    "Secretario de la comisión Aud.", "Presidente de la comisión Aud.",
    "Presidente de la comisión de control", "Secretario de la comisión de control",
    "Miembro de la comisión de control", "Representante perma.", "Liquidador mancomunado",
    "Miembro de la comisión ejecutiva", "Presidente J.G.P.V.", "Secretario J.G.P.V.",
    "Miembro J.G.P.V.", "Tesorero", "Comisario", "Socio único", "Sociedades beneficiarias",
    "Sociedades fusionadas", "Gerente", "Gerente adjunto", "Liquidador único",
    "Entidad gestora", "Auditor titular", "Auditor de cuentas", "Director general",
    "Miembro de la Junta Directiva", "Secretario de la Junta Directiva", "Socio",
    "Administrador", "Vocal de la Junta Directiva", "Director", "Administrador judicial",
    "Administrador suplente", "Gerente ejecutivo", "Secretario general", "Director ejecutivo",
    "Socio único profesional", "Administrador mancomunado", "Liquidador judicial",
    "Liquidador solidario", "Socio gestor", "Administrador solidario suplente",
    "Administrador mancomunado suplente", "Consejero suplente", "Consejero independiente",
    "Consejero externo", "Fundador", "Promotor", "Delegado", "Gerente de la sucursal",
    "Director de la sucursal", "Representante de la sucursal", "Liquidador suplente",
    "Administrador primero", "Administrador adjunto", "Consejero delegado suplente",
    "Apoderado conjunto", "Socio colectivo", "Síndico", "Interventor", "Interventor judicial",
    "Interventor solidario", "Depositario", "Mandatario", "Gestor", "Delegado general",
    "Subdirector general", "Subdirector", "Director financiero", "Director general adjunto",
    "Director general financiero", "Director técnico", "Director comercial",
    "Director de marketing", "Director de Recursos Humanos", "Director operaciones",
    "Director de relaciones laborales", "Director de departamento", "Director ejecutivo",
    "Secretario general adjunto", "Vicepresidente primero", "Vicepresidente segundo",
    "Vicepresidente tercero", "Vicepresidente honorífico", "Presidente honorífico",
    "Presidente ejecutivo", "Copresidente", "Suplente", "No definido", "Otros",
    "Socio profesional", "Letrado asesor", "Representante fiscal", "Otorgante",
    "Entidad cogestora", "Entidad promotora", "Entidad gestora Act.", "Entidad gestora Del.",
    "Entidad gestora independiente", "Entidad enc. gestora",
}

'''

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "borme_xml_parser.py"

    with open(path, encoding="utf-8") as f:
        contenido = f.read()

    if "_CARGOS_CONOCIDOS" in contenido:
        print("Ya parcheado.")
        sys.exit(0)

    if VIEJO not in contenido:
        print("ERROR: no encuentro el bloque a reemplazar. Puede que el fichero ya sea diferente.")
        sys.exit(1)

    # Insertar la constante antes de guardar_anuncios
    contenido = contenido.replace(
        "def guardar_anuncios(",
        CONSTANTE + "def guardar_anuncios("
    )
    # Reemplazar el bloque de personas
    contenido = contenido.replace(VIEJO, NUEVO)

    with open(path, "w", encoding="utf-8") as f:
        f.write(contenido)

    print(f"OK: parcheado {path}")

from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import os

app = Flask(__name__)
DB_PATH = 'catalog.db'

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return send_from_directory('.', 'borme_explorer.html')

# ── 1. Buscar persona ──────────────────────────────────────────
@app.route('/api/persona')
def buscar_persona():
    nombre = request.args.get('q', '').strip().upper()
    if len(nombre) < 3:
        return jsonify([])
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            p.id,
            p.nombre,
            COUNT(DISTINCT ep.id_borme)          as num_empresas,
            SUM(e.capital)                        as capital_total,
            MIN(e.fecha)                          as primera_empresa,
            MAX(e.fecha)                          as ultima_empresa,
            GROUP_CONCAT(DISTINCT e.provincia)    as provincias
        FROM personas p
        JOIN empresa_persona ep ON ep.id_persona = p.id
        JOIN empresas e         ON e.id_borme    = ep.id_borme
        WHERE p.nombre LIKE ?
        GROUP BY p.id
        ORDER BY num_empresas DESC
        LIMIT 50
    """, (f'%{nombre}%',))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

# ── 2. Detalle de una persona ──────────────────────────────────
@app.route('/api/persona/<int:pid>/empresas')
def empresas_de_persona(pid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            e.id_borme,
            e.nombre        as empresa,
            ep.cargo,
            e.provincia,
            e.fecha,
            e.capital,
            e.domicilio
        FROM empresa_persona ep
        JOIN empresas e ON e.id_borme = ep.id_borme
        WHERE ep.id_persona = ?
        ORDER BY e.fecha DESC
    """, (pid,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

# ── 3. Buscar empresa ──────────────────────────────────────────
@app.route('/api/empresa')
def buscar_empresa():
    nombre = request.args.get('q', '').strip().upper()
    provincia = request.args.get('provincia', '').strip()
    capital_min = request.args.get('capital_min', 0)
    capital_max = request.args.get('capital_max', 999999999999)

    if len(nombre) < 2:
        return jsonify([])

    params = [f'%{nombre}%', float(capital_min), float(capital_max)]
    filtro_prov = ""
    if provincia:
        filtro_prov = "AND e.provincia = ?"
        params.append(provincia)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT 
            e.id_borme,
            e.nombre,
            e.provincia,
            e.fecha,
            e.capital,
            e.domicilio,
            COUNT(ep.id_persona) as num_personas
        FROM empresas e
        LEFT JOIN empresa_persona ep ON ep.id_borme = e.id_borme
        WHERE e.nombre LIKE ?
          AND (e.capital >= ? OR e.capital IS NULL)
          AND (e.capital <= ? OR e.capital IS NULL)
          {filtro_prov}
        GROUP BY e.id_borme
        ORDER BY e.capital DESC NULLS LAST
        LIMIT 100
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

# ── 4. Detalle de una empresa: sus personas ────────────────────
@app.route('/api/empresa/<path:id_borme>/personas')
def personas_de_empresa(id_borme):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            p.id,
            p.nombre,
            ep.cargo,
            ep.fecha,
            COUNT(DISTINCT ep2.id_borme) as otras_empresas
        FROM empresa_persona ep
        JOIN personas p ON p.id = ep.id_persona
        LEFT JOIN empresa_persona ep2 ON ep2.id_persona = p.id
        WHERE ep.id_borme = ?
        GROUP BY p.id, ep.cargo
        ORDER BY ep.cargo
    """, (id_borme,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

# ── 5. Ranking empresarios ─────────────────────────────────────
@app.route('/api/ranking/empresarios')
def ranking_empresarios():
    provincia = request.args.get('provincia', '').strip()
    filtro = ""
    params = []
    if provincia:
        filtro = "AND e.provincia = ?"
        params.append(provincia)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT 
            p.id,
            p.nombre,
            COUNT(DISTINCT ep.id_borme)       as num_empresas,
            SUM(e.capital)                    as capital_total,
            GROUP_CONCAT(DISTINCT e.provincia) as provincias
        FROM personas p
        JOIN empresa_persona ep ON ep.id_persona = p.id
        JOIN empresas e         ON e.id_borme    = ep.id_borme
        WHERE 1=1 {filtro}
        GROUP BY p.id
        ORDER BY num_empresas DESC
        LIMIT 50
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

# ── 6. Ranking empresas por capital ───────────────────────────
@app.route('/api/ranking/capital')
def ranking_capital():
    provincia = request.args.get('provincia', '').strip()
    filtro = ""
    params = []
    if provincia:
        filtro = "AND provincia = ?"
        params.append(provincia)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id_borme, nombre, provincia, fecha, capital, domicilio
        FROM empresas
        WHERE capital > 0 {filtro}
        ORDER BY capital DESC
        LIMIT 50
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

# ── 7. Stats generales ─────────────────────────────────────────
@app.route('/api/stats')
def stats():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as total FROM empresas")
    total_empresas = cur.fetchone()['total']

    cur.execute("SELECT COUNT(*) as total FROM personas")
    total_personas = cur.fetchone()['total']

    cur.execute("SELECT COUNT(*) as total FROM empresa_persona")
    total_vinculos = cur.fetchone()['total']

    cur.execute("SELECT SUM(capital) as total FROM empresas WHERE capital > 0")
    capital_total = cur.fetchone()['total'] or 0

    cur.execute("""
        SELECT provincia, COUNT(*) as total 
        FROM empresas WHERE provincia IS NOT NULL
        GROUP BY provincia ORDER BY total DESC LIMIT 10
    """)
    por_provincia = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT strftime('%Y', fecha) as anio, COUNT(*) as total
        FROM empresas WHERE fecha IS NOT NULL
        GROUP BY anio ORDER BY anio
    """)
    por_anio = [dict(r) for r in cur.fetchall()]

    conn.close()
    return jsonify({
        'total_empresas': total_empresas,
        'total_personas': total_personas,
        'total_vinculos': total_vinculos,
        'capital_total': capital_total,
        'por_provincia': por_provincia,
        'por_anio': por_anio
    })

# ── 8. Conexiones entre personas (comparten empresa) ──────────
@app.route('/api/persona/<int:pid>/red')
def red_persona(pid):
    conn = get_conn()
    cur = conn.cursor()
    # Personas que comparten al menos una empresa con pid
    cur.execute("""
        SELECT 
            p2.id,
            p2.nombre,
            COUNT(DISTINCT ep1.id_borme) as empresas_comunes
        FROM empresa_persona ep1
        JOIN empresa_persona ep2 ON ep2.id_borme = ep1.id_borme AND ep2.id_persona != ep1.id_persona
        JOIN personas p2 ON p2.id = ep2.id_persona
        WHERE ep1.id_persona = ?
        GROUP BY p2.id
        ORDER BY empresas_comunes DESC
        LIMIT 30
    """, (pid,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

if __name__ == '__main__':
    print("BORME Explorer corriendo en http://localhost:5000")
    app.run(debug=False, port=5000)

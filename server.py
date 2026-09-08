#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py — LEYDE BOE
Motor juridico legislativo local (front HTML + API Flask + SQLite FTS5).

Mejoras v2.0:
- Compresión gzip para respuestas
- Cacheo de consultas frecuentes
- Rate limiting basico
- Headers de seguridad
- Mejor manejo de errores
- API de autenticacion

Arranque:  python3 server.py
"""

import os
import json
import sqlite3
import hashlib
import time
import threading
from pathlib import Path
from functools import wraps
from collections import defaultdict
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, Response, send_from_directory, session
from flask_compress import Compress
from werkzeug.security import generate_password_hash, check_password_hash

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "leyes_boe.db"
USERS_DB = BASE / "users.db"

app = Flask(__name__, static_folder=None)
app.secret_key = os.urandom(32)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Habilitar compresion gzip
Compress(app)

# Cache en memoria para consultas frecuentes
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 300  # 5 minutos

# Rate limiting basico
_rate_limits = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # 1 minuto
RATE_LIMIT_MAX = 100  # peticiones por minuto

# -------------------------------------------------------------------------- #
# MIDDLEWARE DE SEGURIDAD Y CACHE
# -------------------------------------------------------------------------- #
@app.after_request
def add_security_headers(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "SAMEORIGIN"
    r.headers["X-XSS-Protection"] = "1; mode=block"
    r.headers["Cache-Control"] = "public, max-age=3600"
    return r

def rate_limit(f):
    """Decorator para limitar peticiones por IP"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        ip = request.remote_addr
        now = time.time()
        
        with _cache_lock:
            # Limpiar entradas antiguas
            _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < RATE_LIMIT_WINDOW]
            
            if len(_rate_limits[ip]) >= RATE_LIMIT_MAX:
                return jsonify({"error": "Demasiadas peticiones. Intenta mas tarde."}), 429
            
            _rate_limits[ip].append(now)
        
        return f(*args, **kwargs)
    return decorated_function

def cache_response(ttl=CACHE_TTL):
    """Decorator para cachear respuestas de la API"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Crear clave de cache
            cache_key = f"{f.__name__}:{request.full_path}:{request.data.decode()}"
            cache_key = hashlib.md5(cache_key.encode()).hexdigest()
            
            with _cache_lock:
                if cache_key in _cache:
                    result, timestamp = _cache[cache_key]
                    if time.time() - timestamp < ttl:
                        return result
            
            result = f(*args, **kwargs)
            
            with _cache_lock:
                _cache[cache_key] = (result, time.time())
            
            return result
        return decorated_function
    return decorator

# -------------------------------------------------------------------------- #
# CONEXION A BASE DE DATOS
# -------------------------------------------------------------------------- #
def get_conn():
    db = str(DB_PATH)
    conn = sqlite3.connect(db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def get_users_conn():
    conn = sqlite3.connect(str(USERS_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP,
            is_active BOOLEAN DEFAULT 1
        )
    """)
    conn.commit()
    return conn

# Inicializar base de datos de usuarios
get_users_conn().close()

# -------------------------------------------------------------------------- #
# CLASIFICADOR DE RAMA
# -------------------------------------------------------------------------- #
RAMAS = [
    ("Constitucional", ("constitucion", "constitucional")),
    ("Penal", ("codigo penal", "penal", "delito", "condena")),
    ("Civil", ("codigo civil", "civil", "matrimonio", "herencia", "propiedad")),
    ("Mercantil", ("mercantil", "codigo de comercio", "sociedades")),
    ("Laboral", ("laboral", "estatuto de los trabajadores", "trabajadores", "sindicatos")),
    ("Administrativo", ("administrat", "procedimiento administrativo", "sector publico")),
    ("Procesal", ("procesal", "enjuiciamiento", "procedimiento")),
    ("Tributario", ("tributario", "fiscal", "impuestos", "iva", "irpf")),
    ("Tecnologico", ("digital", "tecnologic", "proteccion de datos", "inteligencia artificial")),
]

def rama_de(ley):
    t = (ley or "").lower()
    for rama, keys in RAMAS:
        for k in keys:
            if k in t:
                return rama
    return "Otras"

# -------------------------------------------------------------------------- #
# PAGINAS ESTATICAS
# -------------------------------------------------------------------------- #
PAGES = {
    "/": "index.html",
    "/maquetador.html": "maquetador.html",
    "/leyde-nav-drawer.html": "leyde-nav-drawer.html",
    "/login.html": "login.html",
    "/planes.html": "index.html",
    "/nuevo-modulo.html": "index.html",
}

@app.route("/")
def index():
    fname = BASE / "index.html"
    if fname.exists():
        return Response(fname.read_bytes(), content_type="text/html")
    return "Index no encontrado", 404

@app.route("/<path:path>")
def serve(path):
    # Estaticos: /public/** e /icons/**
    if path.startswith("public/") or path.startswith("icons/"):
        seg = path.split("/", 1)[1]
        folder = path.split("/", 1)[0]
        return send_from_directory(str(BASE / folder), seg)

    # Rutas de pagina conocidas
    if path in PAGES:
        fname = BASE / PAGES[path]
        if fname.exists():
            return Response(fname.read_bytes(), content_type="text/html")
        return "Pagina no disponible", 404

    # /privacidad  y  /cookies
    if path in ("privacidad", "cookies"):
        fname = BASE / "index.html"
        return Response(fname.read_bytes(), content_type="text/html")

    # Si no es una ruta conocida, servir index.html (SPA fallback)
    fname = BASE / "index.html"
    if fname.exists():
        return Response(fname.read_bytes(), content_type="text/html")
    
    return "Pagina no encontrada", 404

# -------------------------------------------------------------------------- #
# API: ESTADISTICAS
# -------------------------------------------------------------------------- #
@app.route("/api/stats")
@rate_limit
@cache_response(ttl=600)
def api_stats():
    conn = get_conn()
    arts = conn.execute("SELECT COUNT(*) FROM articulos").fetchone()[0]
    leyes = conn.execute("SELECT COUNT(DISTINCT ley) FROM articulos").fetchone()[0]
    conn.close()
    return jsonify({"articulos": int(arts), "leyes": int(leyes)})

# -------------------------------------------------------------------------- #
# API: LISTADO DE LEYES
# -------------------------------------------------------------------------- #
@app.route("/api/leyes")
@rate_limit
@cache_response(ttl=300)
def api_leyes():
    search = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 200)), 500)
    
    conn = get_conn()
    
    if search:
        rows = conn.execute(
            "SELECT DISTINCT ley, COUNT(*) as total FROM articulos WHERE ley LIKE ? GROUP BY ley ORDER BY total DESC LIMIT ?",
            (f"%{search}%", limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT ley, COUNT(*) as total FROM articulos GROUP BY ley ORDER BY total DESC LIMIT ?",
            (limit,)
        ).fetchall()
    
    conn.close()
    
    result = []
    for r in rows:
        result.append({
            "nombre": r["ley"],
            "rama": rama_de(r["ley"]),
            "total": int(r["total"])
        })
    
    return jsonify(result)

# -------------------------------------------------------------------------- #
# API: BUSQUEDA CON STREAMING
# -------------------------------------------------------------------------- #
@app.route("/api/buscar")
@rate_limit
def api_buscar():
    q = request.args.get("q", "").strip()
    ley = request.args.get("ley", "").strip()
    rama = request.args.get("rama", "").strip()
    sort = request.args.get("sort", "relevancia")
    offset = max(0, int(request.args.get("offset", 0)))
    limit = min(int(request.args.get("limit", 20)), 100)

    def stream():
        # Metadata inicial
        conn = get_conn()
        
        # Contar resultados
        where = ""
        params = []
        
        if q:
            # Verificar si FTS5 esta disponible
            try:
                conn.execute("SELECT rowid FROM articulos_busqueda WHERE articulos_busqueda MATCH 'test' LIMIT 1")
                fts_available = True
            except:
                fts_available = False
            
            if fts_available:
                where += " AND b.rowid IN (SELECT rowid FROM articulos_busqueda WHERE articulos_busqueda MATCH ?)"
                params.append(q)
            else:
                where += " AND (a.texto LIKE ? OR a.articulo LIKE ?)"
                params.extend([f"%{q}%", f"%{q}%"])
        
        if ley:
            where += " AND a.ley = ?"
            params.append(ley)
        
        count_query = f"SELECT COUNT(*) FROM articulos a LEFT JOIN articulos_busqueda b ON a.id = b.rowid WHERE 1=1{where}"
        total = conn.execute(count_query, params).fetchone()[0]
        
        yield json.dumps({"type": "meta", "total": total}) + "\n"
        
        # Obtener resultados
        order_clause = "rank" if (q and sort == "relevancia") else "a.id"
        if sort == "fecha":
            order_clause = "a.id DESC"
        
        query = (
            f"SELECT a.id, a.ley, a.codigo_boe, a.articulo, a.texto, a.url "
            f"FROM articulos a LEFT JOIN articulos_busqueda b ON a.id = b.rowid "
            f"WHERE 1=1{where} ORDER BY {order_clause} LIMIT ? OFFSET ?"
        )
        
        p2 = params + [limit, offset]
        rows = conn.execute(query, p2).fetchall()
        
        for r in rows:
            texto = r["texto"] or ""
            if rama and rama_de(r["ley"]) != rama:
                continue
            
            result = {
                "type": "result",
                "id": r["id"],
                "codigo_boe": r["codigo_boe"],
                "articulo": r["articulo"],
                "ley": r["ley"],
                "rama": rama_de(r["ley"]),
                "fragmento": _fragmento(texto, q) if q else texto[:350],
                "texto": texto,
                "url": r["url"],
            }
            yield json.dumps(result, ensure_ascii=False) + "\n"
        
        conn.close()

    return Response(stream(), mimetype="application/x-ndjson")

def _fragmento(texto, query, largo=160):
    if not texto:
        return ""
    t = (texto or "").lower()
    q = (query or "").lower()
    for p in q.split():
        if not p:
            continue
        idx = t.find(p)
        if idx == -1:
            continue
        ini = max(0, idx - 40)
        lt = len(texto)
        fin = min(lt, ini + largo)
        frag = texto[ini:fin]
        if ini > 0:
            frag = "…" + frag
        if fin < lt:
            frag += "…"
        return frag
    return (texto or "")[:largo] + ("…" if len(texto or "") > largo else "")

# -------------------------------------------------------------------------- #
# API: AUTENTICACION
# -------------------------------------------------------------------------- #
@app.route("/api/auth/login", methods=["POST"])
@rate_limit
def api_login():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()
    password = data.get("password", "")
    
    if not email or not password:
        return jsonify({"error": "Email y contraseña son requeridos"}), 400
    
    conn = get_users_conn()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Credenciales incorrectas"}), 401
    
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session.permanent = True
    
    conn = get_users_conn()
    conn.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()
    
    return jsonify({
        "success": True,
        "redirect": "/",
        "user": {"username": user["username"], "email": user["email"]}
    })

@app.route("/api/auth/register", methods=["POST"])
@rate_limit
def api_register():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")
    
    if not username or not email or not password:
        return jsonify({"error": "Todos los campos son requeridos"}), 400
    
    if len(password) < 8:
        return jsonify({"error": "La contraseña debe tener al menos 8 caracteres"}), 400
    
    conn = get_users_conn()
    
    # Verificar si el usuario ya existe
    existing = conn.execute(
        "SELECT id FROM users WHERE email = ? OR username = ?",
        (email, username)
    ).fetchone()
    
    if existing:
        conn.close()
        return jsonify({"error": "El email o nombre de usuario ya está registrado"}), 409
    
    password_hash = generate_password_hash(password)
    
    try:
        conn.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (username, email, password_hash)
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Cuenta creada correctamente"}), 201
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})

@app.route("/api/auth/me")
def api_auth_me():
    if "user_id" in session:
        return jsonify({
            "authenticated": True,
            "user": {"username": session.get("username"), "id": session.get("user_id")}
        })
    return jsonify({"authenticated": False})

# -------------------------------------------------------------------------- #
# API: EXPORTAR PDF
# -------------------------------------------------------------------------- #
@app.route("/api/exportar-pdf", methods=["POST"])
@rate_limit
def export_pdf():
    import subprocess, tempfile, pathlib
    
    data = request.get_json(silent=True) or {}
    inner = data.get("html", "")
    
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        "body{font-family:Georgia,serif;font-size:12pt;line-height:1.8;padding:48px}"
        "@page{size:A4;margin:24mm}</style></head><body>" + inner + "</body></html>"
    )
    
    with tempfile.TemporaryDirectory() as tmp:
        h = pathlib.Path(tmp) / "doc.html"
        p = pathlib.Path(tmp) / "doc.pdf"
        h.write_text(html, encoding="utf-8")
        
        try:
            subprocess.run(
                ["chromium-browser", "--headless", "--disable-gpu", "--no-sandbox",
                 f"--print-to-pdf={p}", "--no-margins", h],
                check=True, timeout=30, capture_output=True
            )
        except subprocess.TimeoutExpired:
            return jsonify({"error": "timeout"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        
        pdf = p.read_bytes()
    
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="documento_juridico.pdf"'}
    )

# -------------------------------------------------------------------------- #
# API: BUSQUEDA RAPIDA (para autocomplete)
# -------------------------------------------------------------------------- #
@app.route("/api/sugerencias")
@rate_limit
@cache_response(ttl=60)
def api_sugerencias():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT articulo FROM articulos WHERE articulo LIKE ? LIMIT 10",
        (f"%{q}%",)
    ).fetchall()
    conn.close()
    
    return jsonify([r["articulo"] for r in rows])

# -------------------------------------------------------------------------- #
# API: APAGADO DEL SERVIDOR
# -------------------------------------------------------------------------- #
@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Cierra el servidor web de forma ordenada desde el frontend."""
    import signal as _signal

    def _apagar():
        time.sleep(0.5)  # deja tiempo a que la respuesta llegue al cliente
        # SIGTERM (no ignorado por procesos en background) apaga el servidor de forma limpia
        os.kill(os.getpid(), _signal.SIGTERM)

    threading.Thread(target=_apagar, daemon=True).start()
    return jsonify({"success": True, "message": "Servidor cerrado correctamente"}), 200

# -------------------------------------------------------------------------- #
# LIMPIEZA DE CACHE
# -------------------------------------------------------------------------- #
def cleanup_cache():
    """Limpia entradas de cache expiradas"""
    while True:
        time.sleep(300)  # Cada 5 minutos
        now = time.time()
        with _cache_lock:
            expired = [k for k, (_, ts) in _cache.items() if now - ts > CACHE_TTL]
            for k in expired:
                del _cache[k]

# Iniciar hilo de limpieza
cleanup_thread = threading.Thread(target=cleanup_cache, daemon=True)
cleanup_thread.start()

# -------------------------------------------------------------------------- #
# ARRANQUE
# -------------------------------------------------------------------------- #
if __name__ == "__main__":
    if not DB_PATH.exists():
        print("AVISO: no se encuentra leyes_boe.db en", DB_PATH)
    print("LEYDE BOE listo: http://localhost:5000")
    print(f"Base de datos: {DB_PATH.stat().st_size / (1024*1024):.1f} MB")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

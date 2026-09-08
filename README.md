# LEYDE BOE - Motor Jurídico Legislativo

Motor de búsqueda legislativa español con base de datos local SQLite.

## 🚀 Inicio Rápido

### 1. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 2. Iniciar el servidor
```bash
python3 server.py
```

### 3. Abrir en el navegador
```
http://localhost:5000
```

## 📁 Estructura del Proyecto

```
LEYDE_BOE/
├── server.py              # Servidor Flask con API
├── index.html             # Página principal (buscador)
├── login.html             # Sistema de autenticación
├── maquetador.html  # Maquetador de documentos
├── requirements.txt       # Dependencias Python
├── leyes_boe.db          # Base de datos SQLite (1.5GB)
├── public/               # Imágenes y recursos estáticos
├── scripts/              # Scripts de utilidad
└── documentos_api/       # Documentos BOE organizados
```

## 🔧 Mejoras v2.0

- ✅ **Compresión gzip** - Respuestas más rápidas
- ✅ **Cache inteligente** - Consultas frecuentes cacheadas
- ✅ **Rate limiting** - Protección contra abusos
- ✅ **Headers de seguridad** - XSS, CSRF protection
- ✅ **API de autenticación** - Login/registro funcional
- ✅ **Autocomplete** - Sugerencias de búsqueda
- ✅ **Búsqueda por leyes** - Filtrar por ley específica
- ✅ **Rama Tributaria** - Nueva categoría añadida

## 📊 API Endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/stats` | Estadísticas (artículos, leyes) |
| GET | `/api/leyes` | Listado de leyes |
| GET | `/api/buscar` | Búsqueda con streaming |
| GET | `/api/sugerencias` | Autocomplete |
| POST | `/api/auth/login` | Iniciar sesión |
| POST | `/api/auth/register` | Registrarse |
| POST | `/api/auth/logout` | Cerrar sesión |
| GET | `/api/auth/me` | Usuario actual |
| POST | `/api/exportar-pdf` | Exportar a PDF |

## 🔐 Variables de Entorno

```bash
export FLASK_ENV=production
export SECRET_KEY=tu_clave_secreta
```

## 📝 Notas

- La base de datos SQLite ocupa ~1.5GB
- Requiere Chromium para exportar PDFs
- Compatible con Python 3.8+

---

**LEYDE BOE** - Motor Jurídico Legislativo © 2026
# leyde_boe

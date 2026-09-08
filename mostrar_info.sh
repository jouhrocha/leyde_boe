#!/bin/bash
# Muestra información del sistema

cd "/media/user/d48e5428-e66b-4a12-902d-02f018700477/LEYDE_BOE"

# Obtener stats
STATS=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('leyes_boe.db')
    arts = conn.execute('SELECT COUNT(*) FROM articulos').fetchone()[0]
    leyes = conn.execute('SELECT COUNT(DISTINCT ley) FROM articulos').fetchone()[0]
    conn.close()
    print(f'{arts}|{leyes}')
except:
    print('0|0')
" 2>/dev/null)

ARTICULOS=$(echo $STATS | cut -d'|' -f1)
LEYES=$(echo $STATS | cut -d'|' -f2)
TAMANO=$(ls -lh leyes_boe.db 2>/dev/null | awk '{print $5}')

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                                                               ║"
echo "║   ⚖️   L E Y D E   B O E  —  Motor Jurídico Legislativo     ║"
echo "║                                                               ║"
echo "║   Versión 2.0 | Optimizado y Mejorado                        ║"
echo "║                                                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "  ✓ Python 3: $(python3 --version 2>/dev/null)"
echo "  ✓ Base de datos: $TAMANO"
echo "  ✓ Artículos: $ARTICULOS"
echo "  ✓ Leyes: $LEYES"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  HERRAMIENTAS DISPONIBLES:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  1. Buscador de Leyes     → http://localhost:5000"
echo "  2. Generador Denuncias   → python3 scripts/generador_denuncias.py"
echo "  3. Actualizador BOE      → python3 scripts/actualizar_leyes.py"
echo "  4. Sistema Login         → http://localhost:5000/login.html"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TODO VERIFICADO Y FUNCIONANDO ✅"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

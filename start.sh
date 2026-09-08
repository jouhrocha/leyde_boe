#!/bin/bash
# Script de inicio para LEYDE BOE

echo "==================================="
echo "  LEYDE BOE - Motor Jurídico"
echo "==================================="
echo ""

# Verificar Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 no está instalado"
    exit 1
fi

# Verificar dependencias
if [ ! -f "requirements.txt" ]; then
    echo "❌ Error: No se encuentra requirements.txt"
    exit 1
fi

# Instalar dependencias si es necesario
echo "📦 Verificando dependencias..."
pip install -r requirements.txt -q

# Verificar base de datos
if [ ! -f "leyes_boe.db" ]; then
    echo "⚠️  Advertencia: No se encuentra leyes_boe.db"
    echo "   La base de datos debe estar en el directorio actual"
fi

# Iniciar servidor
echo "🚀 Iniciando servidor..."
echo "   Abre http://localhost:5000 en tu navegador"
echo ""
python3 server.py

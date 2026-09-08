#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  LEYDE BOE - Inicio del Servidor
# ═══════════════════════════════════════════════════════════════

cd "/media/user/d48e5428-e66b-4a12-902d-02f018700477/LEYDE_BOE"

# Instalación de dependencias
#sudo apt update; sudo apt install python3-pip -y
#pip3 install -r requirements.txt --break-system-packages

# Colores
GREEN='\e[0;32m'
CYAN='\e[0;36m'
YELLOW='\e[1;33m'
RED='\e[0;31m'
NC='\e[0m'

# Función para liberar el puerto 5000
liberar_puerto() {
    PID=$(lsof -ti:5000 2>/dev/null)
    if [ ! -z "$PID" ]; then
        echo -e "${YELLOW}Liberando puerto 5000...${NC}"
        kill $PID 2>/dev/null
        sleep 1
    fi
}

# Iniciar servidor
iniciar_servidor() {
    echo ""
    echo -e "${GREEN}🚀 Iniciando servidor...${NC}"
    echo -e "${CYAN}   Abre: http://localhost:5000${NC}"
    echo ""
    liberar_puerto
    python3 server.py
}

# Cerrar servidor desde el frontend
cerrar_servidor() {
    echo -e "${GREEN}🛑 Cerrando servidor...${NC}"
    curl -s -X POST http://localhost:5000/api/shutdown
    echo -e "${GREEN}✅ Servidor cerrado correctamente.${NC}"
}

# Menú principal
mostrar_menu() {
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  MENÚ PRINCIPAL${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "  1) 🚀 Iniciar servidor web (buscador de leyes)"
    echo "  2) ❌ Cerrar servidor desde el frontend"
    echo "  3) ❌ Salir"
    echo ""
}

# Programa principal
while true; do
    mostrar_menu
    read -n 1 -p "  Selecciona una opción [1-3]: " opcion
    echo ""

    case $opcion in
        1) iniciar_servidor ;;
        2) cerrar_servidor ;;
        3) echo "¡Hasta luego!"; exit 0 ;;
        *) echo -e "${RED}Opción no válida${NC}" ;;
    esac

    echo ""
    read -n 1 -p "  Presiona cualquier tecla para continuar..."
    echo ""
done

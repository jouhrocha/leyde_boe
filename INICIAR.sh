#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  LEYDE BOE - Inicio directo de la aplicación
#  Al ejecutarlo arranca el servidor y abre la app en el navegador.
#  Para cerrar: botón "Apagar" dentro de la app (menú Nav).
# ═══════════════════════════════════════════════════════════════

cd "$(dirname "$0")"

GREEN='\e[0;32m'
CYAN='\e[0;36m'
YELLOW='\e[1;33m'
RED='\e[0;31m'
NC='\e[0m'

# 1) Dependencias solo si faltan
if ! python3 -c "import flask" 2>/dev/null; then
    echo -e "${YELLOW}Instalando dependencias...${NC}"
    python3 -m pip install -r requirements.txt --break-system-packages
fi

# 2) Liberar el puerto 5000 si algo lo ocupa
PID=$(lsof -ti:5000 2>/dev/null)
if [ -n "$PID" ]; then
    echo -e "${YELLOW}Liberando puerto 5000...${NC}"
    kill $PID 2>/dev/null
    sleep 1
fi

URL="http://localhost:5000"
echo -e "${GREEN}🚀 Arrancando LEYDE BOE...${NC}"
echo -e "${CYAN}   Abriendo: ${URL}${NC}"

# 3) Arrancar el servidor en segundo plano
python3 server.py &
SERVER_PID=$!

# 4) Esperar a que el servidor responda
printf "Esperando al servidor"
for i in $(seq 1 40); do
    if curl -s -o /dev/null --max-time 1 http://localhost:5000/api/stats 2>/dev/null; then
        printf " ${GREEN}✔ listo${NC}\n"
        break
    fi
    printf "."
    sleep 1
done

# 5) Abrir la app en el navegador
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
elif command -v sensible-browser >/dev/null 2>&1; then
    sensible-browser "$URL" >/dev/null 2>&1 &
elif command -v chromium >/dev/null 2>&1; then
    chromium "$URL" >/dev/null 2>&1 &
fi

echo -e "${GREEN}✅ App abierta. Para apagarla usa el botón \"Apagar\" dentro de la app.${NC}"

# 6) Mantener el script vivo hasta que se apague desde la app
wait $SERVER_PID
echo -e "${RED}🛑 Servidor apagado. Hasta luego.${NC}"

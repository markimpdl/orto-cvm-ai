#!/bin/bash
# Dois cliques aqui instalam o CVM neste Mac e abrem o assistente no navegador. Rodar de novo é seguro.
cd "$(dirname "$0")" || exit 1
exec /bin/bash scripts/install/mac.sh "$@"

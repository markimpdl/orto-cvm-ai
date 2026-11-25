#!/usr/bin/env bash
# Installs what features need (tools + models) with automatic retry (the connection may drop); resumes partial files
# and skips what is already there. Same path as the install wizard: python -m cvm.setup.install run.
# Usage: scripts/download-models.sh [base ai_images avatar animation kids music | model ids like voxcpm2 whisper]
cd "$(dirname "$0")/../backend"
until .venv/bin/python -m cvm.setup.install run "${@:-base}"; do
  echo "algo falhou, tentando de novo em 30 s…"; sleep 30
done
.venv/bin/python -m cvm.models_registry

#!/usr/bin/env bash
# Stops the CVM API, worker and UI. Warns first when a video is mid-step (lipsync, narration, render):
# stopping then makes that step start over when the worker is turned on again.
working=$(curl -s --max-time 5 http://127.0.0.1:8790/jobs | python3 -c '
import json, sys
try:
    jobs = json.load(sys.stdin)
except ValueError:
    sys.exit()
for j in jobs:
    p = j.get("progress") or {}
    if p.get("pid") and not p.get("waiting") and j.get("state") in (
            "scripting", "voicing", "sourcing_visuals", "lipsyncing", "composing", "rendering", "seo"):
        print(f"  #{j[\"project_id\"]} {j[\"state\"]}: {p.get(\"label\") or \"\"}")
' 2>/dev/null)

if [ -n "$working" ]; then
  echo "Atenção: estes vídeos estão no meio de uma etapa e ela vai recomeçar do zero:"
  echo "$working"
  read -r -p "Desligar mesmo assim? (s/N) " ok
  [ "$ok" = "s" ] || [ "$ok" = "S" ] || { echo "Nada foi desligado."; exit 0; }
fi

for pattern in "bin/cvm-worker" "bin/cvm-api" "next dev|next start|next-server" "caffeinate -i uv run cvm-worker"; do
  pkill -f "$pattern" 2>/dev/null && echo "■ parado: $pattern"
done
echo "CVM desligado."

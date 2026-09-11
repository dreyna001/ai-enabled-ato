#!/usr/bin/env bash
# wsl-stack-teardown.sh -- Stop local WSL ATO stack (API, workers, portal dev server).
if grep -q $'\r' "$0" 2>/dev/null; then
  exec /usr/bin/env bash <(sed 's/\r$//' "$0") "$@"
fi
set -euo pipefail
IFS=$'\n\t'

info() { echo "  $*"; }

echo "=== ATO WSL stack teardown ==="

info "Stopping systemd units"
for unit in ato-api.service ato-analyzer-worker.service ato-intake-worker.service ato-synthetic-intake-worker.service ato-synthetic-intake-worker.timer; do
  if systemctl is-active --quiet "$unit" 2>/dev/null; then
    systemctl stop "$unit"
    info "Stopped $unit"
  fi
done

info "Killing portal/vite listeners on 5173/5174 and API on 8000/8001"
for port in 5173 5174 8000 8001; do
  mapfile -t pids < <(ss -ltnp 2>/dev/null | awk -v p=":$port " '$4 ~ p { while (match($0, /pid=([0-9]+)/, m)) { print m[1]; $0=substr($0, RSTART+RLENGTH) } }' | sort -u)
  if ((${#pids[@]})); then
    info "Port $port: kill ${pids[*]}"
    kill "${pids[@]}" 2>/dev/null || true
    sleep 1
    kill -9 "${pids[@]}" 2>/dev/null || true
  else
    info "Port $port: nothing listening"
  fi
done

pkill -f 'vite.*517[34]' 2>/dev/null || true
pkill -f 'start-portal.sh' 2>/dev/null || true

echo ""
echo "Stack teardown complete."
echo "  ato-api: $(systemctl is-active ato-api.service 2>/dev/null || echo inactive)"
remaining=$(ss -ltnp 2>/dev/null | grep -E ':5173|:5174|:8000|:8001' || true)
if [[ -n "$remaining" ]]; then
  echo "  Still listening:"
  echo "$remaining"
else
  echo "  No API/portal ports listening."
fi

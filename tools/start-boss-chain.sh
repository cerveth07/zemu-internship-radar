#!/usr/bin/env bash
set -euo pipefail
radar_mode=${1:-web}
if [[ "$radar_mode" != web && "$radar_mode" != --desktop-backend ]]; then
  echo "Usage: $0 [--desktop-backend]"
  exit 2
fi
radar_project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
radar_boss_dir=${RADAR_BOSSHUNTER_DIR:-"$(dirname -- "$radar_project_dir")/tools/BossHunter"}
if [[ ! -x "$radar_boss_dir/.venv/bin/bosshunter" || ! -f "$radar_boss_dir/config.yaml" ]]; then
  echo "BossHunter is not configured at $radar_boss_dir. See docs/boss-collection.md."
  exit 1
fi
if [[ "$radar_mode" == web && ! -x "$radar_project_dir/.venv/bin/python" ]]; then
  echo "Zemu virtual environment is missing. See README.md."
  exit 1
fi
# Upstream probes these port-metadata files before configured ports. Do not attach another session.
for radar_cdp_metadata in "$HOME/.config/google-chrome/DevToolsActivePort" "$HOME/.config/chromium/DevToolsActivePort"; do
  if [[ -e "$radar_cdp_metadata" ]]; then
    echo "Another Linux Chrome debugging profile is active. Close it before starting this dedicated chain."
    exit 1
  fi
done
# Only the dedicated Windows Chrome profile is launched; existing profiles are not read.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$radar_project_dir/tools/start-boss-browser.ps1")"
mkdir -p "$radar_boss_dir/data" "$radar_project_dir/data"
if ! curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8686/api/agent/state >/dev/null 2>&1; then
  setsid --fork "$radar_boss_dir/.venv/bin/python" "$radar_project_dir/tools/boss_radar_bridge.py" --config "$radar_boss_dir/config.yaml" > "$radar_boss_dir/data/radar-service.log" 2>&1 < /dev/null
fi
if [[ "$radar_mode" == web ]]; then
  if ! curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8785/api/health >/dev/null 2>&1; then
    (cd -- "$radar_project_dir" && setsid --fork .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8785 > data/service.log 2>&1 < /dev/null)
  fi
fi
radar_service_ports=(8686)
if [[ "$radar_mode" == web ]]; then radar_service_ports+=(8785); fi
for radar_service_port in "${radar_service_ports[@]}"; do
  radar_service_ready=false
  for radar_attempt in {1..30}; do
    if curl --noproxy '*' -fsS --max-time 2 "http://127.0.0.1:$radar_service_port/" >/dev/null 2>&1; then radar_service_ready=true; break; fi
    sleep 0.5
  done
  if [[ "$radar_service_ready" != true ]]; then
    echo "Service $radar_service_port did not start; check the local data/service log."
    exit 1
  fi
done
if ! curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8686/api/radar/capabilities >/dev/null 2>&1; then
  echo 'BossHunter is running without the Zemu results bridge. Stop that service, then run this launcher again.'
  exit 1
fi
if [[ "$radar_mode" == web ]]; then
  printf '%s\n' 'Zemu: http://127.0.0.1:8785' 'BossHunter: http://127.0.0.1:8686' 'Log in to BOSS in the dedicated Chrome, then preview collection in Zemu.'
  powershell.exe -NoProfile -Command 'Start-Process "http://127.0.0.1:8785"'
else
  printf '%s\n' 'BossHunter backend ready for the Zemu desktop app.'
fi

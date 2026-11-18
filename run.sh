#!/usr/bin/env bash
# 一键启动：夹具服务 + 演示数据 + 仪表盘
set -euo pipefail
cd "$(dirname "$0")"

FIXTURE_PORT="${FIXTURE_PORT:-8088}"
DASH_PORT="${DASH_PORT:-8090}"

cleanup() {
  [[ -n "${FIXTURE_PID:-}" ]] && kill "$FIXTURE_PID" 2>/dev/null || true
  [[ -n "${DASH_PID:-}" ]] && kill "$DASH_PID" 2>/dev/null || true
}
trap cleanup EXIT

python3 -m pricehunter.cli serve-fixtures --port "$FIXTURE_PORT" &
FIXTURE_PID=$!
sleep 1

python3 -m pricehunter.cli run-demo --fixture-url "http://127.0.0.1:$FIXTURE_PORT"

python3 -m pricehunter.cli dashboard --port "$DASH_PORT" \
  --fixture-url "http://127.0.0.1:$FIXTURE_PORT" &
DASH_PID=$!

echo
echo "夹具服务:  http://127.0.0.1:$FIXTURE_PORT/healthz"
echo "运维仪表盘: http://127.0.0.1:$DASH_PORT  (Ctrl-C 退出)"
wait

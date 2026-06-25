#!/usr/bin/env bash
# WSL(CentOS7) 대시보드 개발 실행 스크립트 (Flask + Python 3.6)
# 사용:  ./run_dev.sh                          # mock 모드(기본, 노드 불필요)
#        DASHBOARD_MODE=check ./run_dev.sh      # ansible --check(dry-run)
#        DASHBOARD_MODE=real  ./run_dev.sh      # 실제 ansible 실행
set -e
cd "$(dirname "$0")"

export DASHBOARD_MODE="${DASHBOARD_MODE:-mock}"
export ANSIBLE_DIR="${ANSIBLE_DIR:-$(pwd)/ansible}"
export ANSIBLE_INVENTORY="${ANSIBLE_INVENTORY:-$ANSIBLE_DIR/inventory/hosts.ini}"
export ASSET_DEST="${ASSET_DEST:-$(pwd)/assets}"
export PORT="${PORT:-8800}"
PYBIN="${PYBIN:-python3}"

echo "[dashboard] mode=$DASHBOARD_MODE  port=$PORT  python=$($PYBIN -V 2>&1)"
echo "[dashboard] asset_dest=$ASSET_DEST"
echo "[dashboard] http://localhost:$PORT  (WSL→Windows localhost 자동 포워딩)"

# backend 패키지로 임포트되도록 레포 루트에서 모듈 실행
exec "$PYBIN" -m backend.app

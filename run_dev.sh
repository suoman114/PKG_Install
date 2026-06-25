#!/usr/bin/env bash
# WSL(CentOS7) 대시보드 실행 스크립트 (Flask + Python 3.6)
#   ./run_dev.sh  →  무조건 실제 연동(real). DASHBOARD_MODE 를 따로 지정할 필요 없음.
#   (개발용 시뮬레이션이 필요하면 backend/orchestrator.py 의 MODE 참고)
set -e
cd "$(dirname "$0")"

# 실제 ansible 실행 모드 고정
export DASHBOARD_MODE=real
export ANSIBLE_DIR="${ANSIBLE_DIR:-$(pwd)/ansible}"
export ANSIBLE_INVENTORY="${ANSIBLE_INVENTORY:-$ANSIBLE_DIR/inventory/hosts.ini}"
export ASSET_DEST="${ASSET_DEST:-$(pwd)/assets}"
export PORT="${PORT:-8800}"
PYBIN="${PYBIN:-python3}"

echo "[dashboard] mode=real(실제 연동)  port=$PORT  python=$($PYBIN -V 2>&1)"
echo "[dashboard] asset_dest=$ASSET_DEST"

# real 모드 사전 점검: ansible 필요
if ! command -v ansible-playbook >/dev/null 2>&1; then
  echo "[dashboard] ⚠ ansible-playbook 미설치 — 실제 실행 단계가 실패합니다."
  echo "[dashboard]   설치: (사내망) pip install ansible  또는  yum install -y ansible"
fi
echo "[dashboard] ⚠ 실제 연동: 인벤토리 노드 IP/시크릿(SSH)·🔌 연결 확인 후 실행하세요."
echo "[dashboard] http://localhost:$PORT  (WSL→Windows localhost 자동 포워딩)"

# backend 패키지로 임포트되도록 레포 루트에서 모듈 실행
exec "$PYBIN" -m backend.app

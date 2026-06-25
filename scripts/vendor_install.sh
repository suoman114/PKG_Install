#!/usr/bin/env bash
# vendor_install.sh — 폐쇄망(인터넷 X)에서 vendor/ 의 의존성을 오프라인 설치
#
# 사전: vendor_fetch.sh 로 받은 vendor/ 가 이관돼 있어야 한다.
# 사용:  ./scripts/vendor_install.sh [소스디렉토리=vendor]
set -e
cd "$(dirname "$0")/.."
PYBIN="${PYBIN:-python3}"
SRC="${1:-vendor}"

if [ ! -d "$SRC" ] || [ -z "$(ls -A "$SRC" 2>/dev/null)" ]; then
  echo "[vendor] '$SRC' 가 없거나 비어 있음 — 사내망에서 vendor_fetch.sh 먼저 실행/이관"
  exit 1
fi

echo "[vendor] python=$($PYBIN -V 2>&1)  ←  $SRC (오프라인)"

# pip 가 너무 낮으면 먼저 오프라인 업그레이드(선반입돼 있을 때만)
$PYBIN -m pip install --no-index --find-links "$SRC" --upgrade pip 2>/dev/null || true

$PYBIN -m pip install --no-index --find-links "$SRC" -r backend/requirements.txt

echo "[vendor] 설치 완료. 검증:"
$PYBIN -c "import flask; print('  flask', flask.__version__)"
echo "[vendor] 실행:  ./run_dev.sh"

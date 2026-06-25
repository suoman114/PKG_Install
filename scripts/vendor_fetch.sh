#!/usr/bin/env bash
# vendor_fetch.sh — 사내망(인터넷 O)에서 대시보드 파이썬 의존성을 vendor/ 로 선반입
#
# 폐쇄망 타깃과 "동일 OS/아키텍처/파이썬"(CentOS7 · Python 3.6 · x86_64)에서 실행해야
# 호환 wheel/sdist 가 받아진다. 받은 vendor/ 를 폐쇄망으로 옮겨 vendor_install.sh 로 설치한다.
#
# 사용:  ./scripts/vendor_fetch.sh [대상디렉토리=vendor]
set -e
cd "$(dirname "$0")/.."
PYBIN="${PYBIN:-python3}"
DEST="${1:-vendor}"
mkdir -p "$DEST"

echo "[vendor] python=$($PYBIN -V 2>&1)  →  $DEST"
echo "[vendor] requirements: backend/requirements.txt"
$PYBIN -m pip download -r backend/requirements.txt -d "$DEST"

# pip/설치 도구도 함께 선반입(폐쇄망에 없을 수 있음)
$PYBIN -m pip download pip setuptools wheel -d "$DEST" || \
  echo "[vendor] (경고) pip/setuptools/wheel 선반입 실패 — 타깃에 이미 있으면 무시"

echo "[vendor] 완료: $(ls -1 "$DEST" | wc -l) 파일"
echo "[vendor] 다음: '$DEST' 를 폐쇄망으로 이관 후  ./scripts/vendor_install.sh"

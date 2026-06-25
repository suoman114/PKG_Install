# WSL(CentOS7)에서 대시보드 개발 실행 가이드

폐쇄망 실제 타깃(el7/el8, 시스템 Python 3.6)과 동일한 런타임으로 WSL CentOS7에서 대시보드를 띄워 개발한다.

## 1. 사전 준비 (1회)
```bash
# python3 / pip 확인 (CentOS7 기본 3.6)
python3 -V        # Python 3.6.x 기대
sudo yum install -y python3 python3-pip || sudo yum install -y python36 python36-pip

# 의존성 설치 (개발 PC는 인터넷 가능 가정)
cd <repo>/PKG_Install
python3 -m pip install --user -r backend/requirements.txt
```

> **폐쇄망 반입 시**: 인터넷 되는 곳에서
> `pip download -r backend/requirements.txt -d vendor/` →
> 대상에서 `pip install --no-index --find-links vendor/ -r backend/requirements.txt`.

## 2. 실행
```bash
chmod +x run_dev.sh
./run_dev.sh                      # mock 모드(기본) — 실제 노드 없이 동작
```
브라우저에서 **http://localhost:8800** 접속. (WSL은 localhost가 Windows로 포워딩됨)

## 3. 실행 모드
| 모드 | 명령 | 설명 |
|------|------|------|
| `mock` | `./run_dev.sh` | 노드 없이 가짜 진행 로그 스트리밍. 대시보드 UI/흐름 개발용(기본) |
| `check` | `DASHBOARD_MODE=check ./run_dev.sh` | `ansible-playbook --check` (dry-run, 변경 없음). ansible 설치 필요 |
| `real` | `DASHBOARD_MODE=real ./run_dev.sh` | 실제 ansible 실행. 인벤토리 노드 SSH 접근 필요 |

환경변수: `PORT`(기본 8800), `ANSIBLE_DIR`, `ANSIBLE_INVENTORY`, `PYBIN`.

## 4. WSL 주의점
- **systemd**: WSL 기본 환경은 systemd가 꺼져 있어 `mariadb`/`vcs` 등 서비스 기동(real 모드) 단계가 실패할 수 있다. 대시보드 개발은 `mock`으로 충분하다. real 검증은 실제 el8 노드에서.
- **포트 충돌**: `PORT=9000 ./run_dev.sh` 로 변경.
- **방화벽**: localhost 접속만 쓰면 별도 설정 불필요.

## 5. 동작 확인 체크리스트
- [ ] `/` 접속 시 4-Phase 보드(24 step)와 mode 배지 표시
- [ ] `▶ 전체 실행` → step이 순서대로 running→success, 로그 패널 실시간 스트리밍
- [ ] `■ 중지`로 진행 중단, `↺ 초기화`로 상태/로그 리셋
- [ ] `📋 요약`으로 성공/실패 집계 표시

## 6. 구조
```
backend/app.py          FastAPI 라우트 + SSE
backend/orchestrator.py 실행 엔진(mock/check/real) + 이벤트 버스
backend/pipeline.py     24개 step 정의(단일 진실)
backend/state.py        SQLite 상태/로그
frontend/index.html     대시보드(vanilla JS, 무빌드/무CDN)
```

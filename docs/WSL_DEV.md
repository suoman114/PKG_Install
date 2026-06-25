# WSL(CentOS7)에서 대시보드 개발 실행 가이드

폐쇄망 실제 타깃(el7/el8, 시스템 Python 3.6)과 동일한 런타임으로 WSL CentOS7에서 대시보드를 띄워 개발한다.
웹 프레임워크는 **Flask**(Python 3.6 네이티브, async 불필요)를 사용한다.

## 전체 설치 흐름 (사내망 → 폐쇄망)
```
[사내망 / 인터넷 O]                          [폐쇄망 현장 / 인터넷 X]
1. 대시보드 실행                              4. (서버 이관 후) 대시보드 실행
2. 자산 동기화: Git URL 입력 → Clone/Pull  →  5. OS → PKG → Config 파이프라인 실행
   (RPM·설정·패키지를 asset_dest로 선반입)     6. 결과 보고서 확인
3. 의존성/자산 셋업 완료된 서버 준비
```
> 즉, **인터넷이 되는 사내망에서 git으로 자산을 먼저 받아 셋업**한 뒤, 그 서버를 폐쇄망 현장으로 옮겨 설치를 진행한다.

## 1. 사전 준비 (1회)
```bash
python3 -V        # CentOS7 기본 3.6.x 기대
sudo yum install -y python3 python3-pip git
cd <repo>/PKG_Install
python3 -m pip install --user -r backend/requirements.txt
```
> **폐쇄망 반입 시**: 사내망에서 `pip download -r backend/requirements.txt -d vendor/` →
> 현장에서 `pip install --no-index --find-links vendor/ -r backend/requirements.txt`.

## 2. 실행
```bash
chmod +x run_dev.sh
./run_dev.sh                      # mock 모드(기본) — 실제 노드 없이 동작
```
브라우저에서 **http://localhost:8800** 접속. (WSL은 localhost가 Windows로 포워딩됨)

## 3. 자산 동기화 (Git) 사용법
대시보드 좌측 상단 **① 자산 동기화** 패널:
1. `git URL`, `branch`, `asset_dest`(자산 저장 경로) 입력 → **저장**
2. **⤓ Clone/Pull** 클릭 → 진행 로그가 우측 로그 패널에 실시간 스트리밍
3. 완료 시 `files=N rpm=M` 요약 + "저장소 존재" 표시

- `asset_dest` 기본값: `<repo>/assets` (환경변수 `ASSET_DEST`로 변경 가능).
- 플레이북이 참조하는 사이트 경로(`/root/lter_vcs_gimhae/`)는 추후 `{{ asset_root }}` 변수로
  `asset_dest`와 매핑한다(백로그, CLAUDE.md §7).

## 4. 실행 모드
| 모드 | 명령 | 설명 |
|------|------|------|
| `mock` | `./run_dev.sh` | 노드 없이 가짜 진행 로그. 대시보드 UI/흐름 개발용(기본) |
| `check` | `DASHBOARD_MODE=check ./run_dev.sh` | `ansible-playbook --check`(dry-run). ansible 설치 필요 |
| `real` | `DASHBOARD_MODE=real ./run_dev.sh` | 실제 ansible 실행. 인벤토리 노드 SSH 접근 필요 |

**HA 2노드 / 멱등성**: 파이프라인 컨트롤의 `대상` 드롭다운으로 특정 노드만(`--limit`) 실행할 수 있고,
`멱등성 2회` 체크 시 각 멱등 step을 2회차 실행해 `changed=0`(통과) 여부를 step·보고서에 기록한다.
비멱등 step(00·08-3·15b·17·18)은 검사 대상에서 제외(N/A)된다.

환경변수: `PORT`(기본 8800), `ANSIBLE_DIR`, `ANSIBLE_INVENTORY`, `ASSET_DEST`, `PYBIN`.

## 5. WSL 주의점
- **systemd**: WSL 기본은 systemd가 꺼져 있어 `real` 모드의 서비스 기동(15b)·mariadb 단계가 실패할 수 있다. UI/흐름 개발은 `mock`으로 충분. real 검증은 실제 el8 노드에서.
- **SQLite**: CentOS7 시스템 SQLite(3.7)는 UPSERT 미지원 → 코드에서 `INSERT OR REPLACE` 사용(대응 완료).
- **포트 충돌**: `PORT=9000 ./run_dev.sh`.

## 6. 인벤토리 편집 (노드 / 2-노드 HA)
대시보드 좌측 **① 인벤토리** 패널(접기/펼치기):
- 그룹 변수: `ansible_user`, `asset_root` (플레이북 `src:`가 자산을 읽는 경로 — **자산 동기화(②)의 `asset_dest`와 일치**시킬 것. `⟸ 동기화경로` 버튼으로 자동 입력)
- 노드별 폼: `노드명`, `ansible_host`, `server_id`(HA), `peer_ip`(복제 상대), `app_user`, `ramdisk_size`, `광 NIC`(쉼표 구분)
- **+ 노드** / **삭제** 로 노드 증감, **💾 인벤토리 저장** 시 `hosts.ini` + `host_vars/<node>.yml` **재생성**
- 검증: 노드명 형식/중복, `ansible_host` 필수, `server_id` 정수. 실패 시 사유 표시.

> 저장은 표준 템플릿으로 재생성하므로 주석/NTP 플레이스홀더는 유지되지만, 폼 외 커스텀 변수를
> 직접 넣었다면 `host_vars/*.yml`을 직접 편집하세요(폼 범위 밖 필드는 재생성 시 제외됨).

### 시크릿(비밀번호)
좌측 **🔒 시크릿** 패널에서 비밀번호 5종(SSH, MariaDB root/vcsm, 복제, RabbitMQ vcm)을 입력·저장한다.
- 저장 위치: `ansible/inventory/group_vars/vcs.yml` (vcs 그룹 자동 로드, **.gitignore·0600 권한·커밋 안 됨**)
- 입력한 값만 갱신(빈 칸은 기존 유지). API는 "설정됨" 여부만 반환하고 **값은 노출하지 않음**.
- 최초 설정: `cp group_vars/vcs.yml.example group_vars/vcs.yml` 후 편집하거나 패널에서 입력.
- 강화: `ansible-vault encrypt ansible/inventory/group_vars/vcs.yml` (이 경우 패널은 잠금 표시, CLI로 편집).
- 플레이북에서 평문 비밀번호는 제거됨 → 시크릿 미설정 시 `real`/`check` 실행은 변수 미정의로 실패(의도된 동작).

## 7. 동작 확인 체크리스트
- [ ] `/` 접속 시 인벤토리 + 자산 동기화 패널 + 4-Phase 보드(24 step) + mode 배지 표시
- [ ] 인벤토리: 노드 필드 편집 → 저장 → hosts.ini/host_vars 재생성 확인
- [ ] 자산 동기화: Git Clone/Pull → 로그 스트리밍 → "저장소 존재"
- [ ] `▶ 전체 실행` → step 순차 running→success, 로그 실시간 스트리밍
- [ ] **대상 노드** 선택(전체/node1/node2) → `--limit` 분리 실행 (HA)
- [ ] **멱등성 2회** 체크 → 멱등 step 2회차 changed=0 검증(step에 `♻✓`, 보고서 멱등성 열)
- [ ] `■ 중지` / `↺ 초기화` / `📋 요약` 동작
- [ ] `📄 보고서 보기`(HTML 새 탭) / `⤓ MD` / `⤓ HTML` 다운로드 — 단계결과·검증·멱등성·실행이력 집계

## 8. 구조
```
backend/app.py          Flask 라우트 + SSE + git/인벤토리 API
backend/orchestrator.py 실행 엔진(mock/check/real, 스레드 기반)
backend/gitassets.py    Git clone/pull 자산 동기화
backend/inventory.py    인벤토리 구조화 읽기/쓰기(PyYAML 무의존)
backend/secrets.py      시크릿(비밀번호) → group_vars/vcs.yml(0600, gitignore)
backend/events.py       공용 이벤트 버스(SSE 브로드캐스트)
backend/report.py       결과 보고서 엔진(Markdown/HTML, 멱등성·검증 집계)
backend/pipeline.py     24개 step 정의(단일 진실)
backend/state.py        SQLite 상태/로그/설정
frontend/index.html     대시보드(vanilla JS, 무빌드/무CDN)
```

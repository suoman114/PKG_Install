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
> **폐쇄망 반입(오프라인 vendoring)**: 아래 스크립트 사용. 사내망/타깃은 **동일 OS·파이썬**(CentOS7·py3.6·x86_64) 권장.
> ```bash
> # 사내망(인터넷 O): 의존성 선반입 → vendor/
> ./scripts/vendor_fetch.sh
> # vendor/ 를 폐쇄망으로 이관 후 (인터넷 X): 오프라인 설치
> ./scripts/vendor_install.sh
> ```
> 프론트엔드는 무빌드·무CDN(vanilla JS)이라 별도 vendoring 불필요. RPM/설치 자산은 대시보드 ② 자산 동기화(Git)로 선반입.

## 2. 실행
```bash
chmod +x run_dev.sh
./run_dev.sh                      # 무조건 실제 연동(real) — ansible 실제 실행
```
브라우저에서 **http://localhost:8800** 접속. (WSL은 localhost가 Windows로 포워딩됨)

> `run_dev.sh`는 **항상 real 모드**로 뜹니다(별도로 `DASHBOARD_MODE` 지정 불필요). 따라서
> **ansible 설치 + 인벤토리 노드 IP/시크릿(SSH) 설정 + 🔌 연결 확인**이 선행돼야 단계가 성공합니다.
> ansible 미설치 시 기동은 되지만 실행 단계에서 실패하며, 스크립트가 경고를 출력합니다.
> (UI/흐름만 시뮬레이션으로 보려면 `DASHBOARD_MODE=mock python3 -m backend.app` 로 직접 기동)

## 3. 자산 동기화 (Git) 사용법
대시보드 **⚙ 설정 → 자산 동기화** 패널:
1. `git URL`, `branch`, `asset_dest`(자산 저장 경로) 입력 → **저장**
2. **⤓ Clone/Pull** 클릭 → 진행 로그가 우측 로그 패널에 실시간 스트리밍
3. 완료 시 `files=N rpm=M` 요약 + "저장소 존재" 표시
4. **■ 중지**: 진행 중인 clone/pull을 즉시 종료. 신규 clone 중지 시 **부분 받은 디렉토리는 자동 정리**.

**사설 저장소(id/pw·토큰)**: `사용자` + `비밀번호/토큰` 입력 후 저장.
- http(s) 저장소에 `Authorization: Basic` 헤더로 전달 → **`.git/config`에 저장 안 됨**, 로그엔 **마스킹**(`***`).
- 토큰 값은 API로 반환하지 않음(설정 여부만 `인증✓`). 빈 칸 저장은 기존 토큰 유지, 지우려면 토큰 칸에 `-`.
- GitHub/GitLab은 비밀번호 대신 **PAT(토큰)** 사용 권장. **SSH URL**(`git@…`)은 컨트롤러에 SSH 키 등록으로 인증(대시보드 인증칸은 http(s) 전용).

- `asset_dest` 기본값: `<repo>/assets` (환경변수 `ASSET_DEST`로 변경 가능).
- 플레이북이 참조하는 사이트 경로(`/root/lter_vcs_gimhae/`)는 추후 `{{ asset_root }}` 변수로
  `asset_dest`와 매핑한다(백로그, CLAUDE.md §7).

## 4. 실행 모드
| 모드 | 명령 | 설명 |
|------|------|------|
| `real` | `./run_dev.sh` | **기본/고정**. 실제 ansible 실행. 인벤토리 노드 SSH 접근 + ansible 필요 |
| `check` | `DASHBOARD_MODE=check python3 -m backend.app` | `ansible-playbook --check`(dry-run) |
| `mock` | `DASHBOARD_MODE=mock python3 -m backend.app` | 시뮬레이션(노드 없이 가짜 로그, 항상 성공) — UI/흐름 개발용 |

**노드 연결 확인(🔌)**: ▶ 설치 탭에서 `대상` 선택 후 **🔌 연결 확인** → 각 노드의 SSH(22) 도달성을 확인.
- 1차: TCP 22 포트 도달성 — **mock 모드에서도 실제로 확인**(인증·ansible 불필요, 네트워크/방화벽 점검). 결과 `✓/✗`.
- 2차(real/check + ansible 설치 시): `ansible -m ping`으로 SSH 인증 + 원격 파이썬까지 확인.
- ⚠ **파이프라인 실행과 혼동 금지**: `mode=mock` 파이프라인은 시뮬레이션이라 SSH/설치 없이 항상 성공으로 표시됨. **실제 연결 여부는 🔌 연결 확인으로 점검**하세요.

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
- 그룹 변수: `ansible_user`, `asset_root` (플레이북 `src:`가 자산을 읽는 경로 — **자산 동기화(②)의 `asset_dest`와 일치**시킬 것. `⟸ 동기화경로` 버튼으로 자동 입력), `ntp_ip1`/`ntp_ip2` (폐쇄망 NTP — 비우면 04는 timezone만 적용)
- **등록된 노드 (N대)**: 등록된 노드를 카드 리스트로 관리(편집·삭제). 필드: `노드명`, `ansible_host`, `server_id`(HA), `peer_ip`(복제 상대), `app_user`, `ramdisk_size`, `광 NIC`(쉼표 구분)
- **노드 추가**: 전용 입력폼(`노드명` + `ansible_host`) → **+ 추가** (server_id 자동, 중복·형식 검증). 추가 후 리스트에서 세부 편집
- 변경은 화면에 staged → **💾 인벤토리 저장** 시 `hosts.ini` + `host_vars/<node>.yml` **재생성**
- 검증: 노드명 형식/중복, `ansible_host` 필수, `server_id` 정수. 실패 시 사유 표시.

> 저장은 표준 템플릿으로 재생성하므로 주석/NTP 플레이스홀더는 유지되지만, 폼 외 커스텀 변수를
> 직접 넣었다면 아래 **📝 파일 편집** 패널로 `host_vars/*.yml`을 직접 편집하세요(폼 범위 밖 필드는 폼 저장 시 제외됨).

### 파일 편집 (YAML/INI — 고급)
**📝 파일 편집** 패널에서 `ansible/` 하위 파일을 직접 편집한다.
- 대상: `playbooks/*.yml`, `inventory/hosts.ini`, `inventory/host_vars/*.yml`, `ansible.cfg`
- 안전장치: ansible 디렉토리 밖 경로·허용외 확장자 차단, **시크릿 `group_vars/vcs.yml` 제외**(🔒 패널 전용), 저장 시 **YAML 문법 검사**(오류 시 거부, 파일 보존)
- 주의: hosts.ini/host_vars를 직접 고친 뒤 ① 인벤토리 폼으로 저장하면 폼 값으로 덮어쓰여질 수 있음.

### 시크릿(비밀번호)
좌측 **🔒 시크릿** 패널에서 비밀번호 5종(SSH, MariaDB root/vcsm, 복제, RabbitMQ vcm)을 입력·저장한다.
- 저장 위치: `ansible/inventory/group_vars/vcs.yml` (vcs 그룹 자동 로드, **.gitignore·0600 권한·커밋 안 됨**)
- 입력한 값만 갱신(빈 칸은 기존 유지). API는 "설정됨" 여부만 반환하고 **값은 노출하지 않음**.
- 최초 설정: `cp group_vars/vcs.yml.example group_vars/vcs.yml` 후 편집하거나 패널에서 입력.
- 강화: `ansible-vault encrypt ansible/inventory/group_vars/vcs.yml` (이 경우 패널은 잠금 표시, CLI로 편집).
- 플레이북에서 평문 비밀번호는 제거됨 → real/check 실행 시 **필요한 시크릿이 없으면 실행 전에 막고** 어떤 시크릿이 필요한지 알려줌(사전 점검). 예: step 00은 `ssh_password`, 08-1은 mariadb root/vcsm 등.
- ⚠ **비밀번호 SSH는 컨트롤러에 `sshpass` 필요**: `ssh_password`로 처음 접속(플레이북 00)하려면 `yum install -y sshpass`(또는 `apt install sshpass`). 키 교환(00) 후엔 키 인증이라 불필요.

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
backend/files.py        YAML/INI 파일 직접 편집(경로가드·확장자제한·시크릿제외·YAML검사)
backend/nodecheck.py    노드 연결 확인(TCP22 도달성 + ansible ping)
backend/events.py       공용 이벤트 버스(SSE 브로드캐스트)
backend/report.py       결과 보고서 엔진(Markdown/HTML, 멱등성·검증 집계)
backend/pipeline.py     24개 step 정의(단일 진실)
backend/state.py        SQLite 상태/로그/설정
frontend/index.html     대시보드(vanilla JS, 무빌드/무CDN)
```

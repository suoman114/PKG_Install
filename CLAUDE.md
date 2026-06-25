# CLAUDE.md — LTE-R 녹취서버 PKG Install 자동화 (폐쇄망)

> 이 파일은 **프로젝트 메모리이자 오케스트레이션 설계 정본**이다.
> Claude Code는 모든 작업 시작 전 이 문서를 읽고, 아래 **에이전트 분담**과 **토큰 통제 규칙**을 따른다.
> 상세 플레이북 분석은 `docs/PLAYBOOK_ANALYSIS.md` (다이제스트)를 참조한다 — 원본 25개 yml을 매번 읽지 않는다.

---

## 1. 프로젝트 목표

LTE-R(철도 통합무선망) **녹취서버 VCS**를 **폐쇄망(air-gapped)** 환경에서 자동 설치하는 도구를 개발한다.
하나의 사이클에서 다음 4단계를 **완결**한다:

```
OS Setup  →  PKG Install  →  Config Setup  →  결과 보고서
```

모든 기능은 **웹 대시보드**에서 관리하고, **설치 진행 로그를 실시간으로** 보여준다.

설치 대상의 실제 작업 내용은 25개 Ansible 플레이북(`ansible/playbooks/`)에 정의되어 있으며,
이 도구는 그 플레이북을 **오케스트레이션·시각화·검증·보고**하는 상위 시스템이다.

### 핵심 제약 (반드시 준수)
- **폐쇄망**: 인터넷 불가. 모든 의존성(파이썬 패키지, RPM, 프론트 에셋)은 **오프라인 설치 가능**해야 한다(vendoring).
- **2노드 HA**: MariaDB Master-Master(Active/Standby). `server_id`/`peer_ip`는 노드별로 다름.
- **재현성/멱등성**: 같은 단계를 다시 실행해도 안전(changed=0 지향). 단계별 **재시도(Retry)** 지원.
- **OS 분기**: RHEL/CentOS 7·8 (신규 기준 el8).

---

## 2. 산출물(Product) 아키텍처 — "무엇을 만드는가"

```
┌──────────────────────────────────────────────────────────────┐
│                     Web Dashboard (Frontend)                   │
│  - 인벤토리/호스트 설정 편집   - 4-Phase 파이프라인 보드        │
│  - 단계별 상태(대기/실행/성공/실패/스킵)  - 실시간 로그 스트림 │
│  - 전체/단계 시작·중지·재시도   - 보고서 다운로드              │
└───────────────┬──────────────────────────────────────────────┘
                │ REST + SSE/WebSocket
┌───────────────▼──────────────────────────────────────────────┐
│              Backend API (Flask, Python 3.6 호환)              │
│  - /api/inventory   - /api/run (phase/step)   - /api/logs(SSE) │
│  - /api/status      - /api/report   - /api/git (자산 동기화)   │
└───────┬───────────────────────┬───────────────────┬──────────┘
        │                       │                   │
┌───────▼────────┐   ┌──────────▼─────────┐   ┌─────▼──────────┐
│ Orchestration  │   │   State Store      │   │ Report Engine  │
│ Engine         │   │   (SQLite/JSON)    │   │ (HTML/MD/PDF)  │
│ - pipeline 정의 │   │ - step status/log │   │ - 단계결과 집계 │
│ - ansible-     │   │ - 실행 이력        │   │ - 검증결과 포함 │
│   playbook 호출 │   └────────────────────┘   └────────────────┘
│ - 로그 캡처/검증│
└───────┬────────┘
        │ subprocess: ansible-playbook -i inventory ...
┌───────▼────────────────────────────────────────────────────┐
│        Ansible Playbooks (ansible/playbooks/*.yml)          │
│   00..18  (OS / PKG / CONFIG)  — 실제 설치 작업의 단일 진실  │
└────────────────────────────────────────────────────────────┘
```

### 제안 디렉토리 구조 (목표 상태)
```
PKG_Install/
├── CLAUDE.md                     # (이 문서) 설계·오케스트레이션 정본
├── docs/
│   ├── PLAYBOOK_ANALYSIS.md      # 플레이북 분석 다이제스트(토큰 절약 1순위 참조)
│   └── PIPELINE.md               # (생성예정) step↔playbook↔검증 매핑 단일표
├── ansible/
│   ├── ansible.cfg
│   ├── inventory/
│   │   ├── hosts.ini             # vcs 그룹, 2노드
│   │   └── host_vars/<node>.yml  # server_id, peer_ip, NIC명, ramdisk_size ...
│   └── playbooks/                # 25개 yml (단일 진실)
├── backend/
│   ├── app.py                    # Flask 엔트리(REST + SSE + git API)
│   ├── orchestrator.py           # 파이프라인 실행/로그 캡처/검증(스레드 기반)
│   ├── gitassets.py              # Git clone/pull 자산 동기화(사내망 선반입)
│   ├── events.py                 # 공용 이벤트 버스(SSE 브로드캐스트)
│   ├── pipeline.py               # step 정의(§3 표를 코드로)
│   ├── state.py                  # SQLite 상태/이력/설정
│   └── report.py                 # 보고서 생성(Markdown/HTML, 멱등성·검증 집계)
├── frontend/                     # 대시보드(SSE 로그, 파이프라인 보드, 자산 동기화)
├── assets/                       # git 동기화로 받은 RPM/파일(asset_dest, gitignore)
├── vendor/                       # 오프라인 의존성(폐쇄망)
└── .claude/agents/               # 서브에이전트 정의(오케스트레이션)
```

---

## 3. 4-Phase 파이프라인 ↔ 플레이북 매핑 (정본)

> 상세 표·변수·위험은 `docs/PLAYBOOK_ANALYSIS.md §1` 참조. 실행 순서는 **파일 번호 순**.

| Phase | Steps (playbook) |
|-------|------------------|
| **1. OS Setup** | 00 auto_pass(SSH/SELinux) · 01 PAM_limits · 02 systemctl_stop · 03 sysctl · 04 timezone · 05 visudo |
| **2. PKG Install** | 06 RabbitMQ · 07 OpenJDK · 08-1 MariaDB · 08-2 chown · 08-3 HA복제 · 11 앱패키지(계정 선행) |
| **3. Config Setup** | 09 vcs계정 · 10 vcweb계정 · 12 cron · 13 ldconf/setcap · 14 ramdisk · 15a iptables · 15b systemd서비스 · 16 watermark · 17 광NIC · 18 ringbuffer |
| **4. Report** | 전 단계 status/log/검증결과 집계 → 보고서 |

각 step은 backend `pipeline.py`에서 다음 메타로 정의한다:
`{ id, phase, name, playbook, depends_on[], verify_cmd, idempotent, required_vars[] }`

---

## 4. 오케스트레이션 모델 — "어떻게 개발하는가"

이 프로젝트는 **오케스트레이터(메인 Claude) + 전문 서브에이전트** 구조로 개발한다.
메인 Claude는 직접 코드를 마구 작성하지 않고 **작업을 분해→위임→통합→검증**한다.

### 에이전트 구성 (`.claude/agents/` 정의 파일 존재)

| 에이전트 | 역할 | 주 산출물 |
|----------|------|-----------|
| **orchestrator** | 사이클 총괄. 작업 분해, 서브에이전트 디스패치, 상태 통합, 게이트 검증 | 작업계획·통합 |
| **token-guardian** ⭐ | **토큰 통제 전담**(필수). 컨텍스트 예산, 요약 강제, 모델 라우팅, 중복 차단 | 토큰 예산/지침 |
| **os-setup-agent** | Phase1 모듈 + 00~05 래핑/검증 | OS setup 코드·검증 |
| **pkg-install-agent** | Phase2 모듈 + 06/07/08/11 (HA 포함) | PKG 코드·검증 |
| **config-setup-agent** | Phase3 모듈 + 09~18 (계정/서비스/네트워크) | Config 코드·검증 |
| **dashboard-agent** | FastAPI 백엔드 + 프론트 대시보드 + SSE 로그 스트림 | 대시보드 |
| **report-agent** | 결과 보고서 엔진(HTML/MD/PDF), 검증결과 집계 | 보고서 엔진 |

> ⭐ **token-guardian는 반드시 포함**하며, 모든 서브에이전트 호출의 **사전 게이트**다.
> orchestrator는 서브에이전트를 띄우기 전에 token-guardian의 예산/요약 규칙을 적용한다.

### 표준 작업 루프 (orchestrator)
```
1. 요청 분해 → 단계별 작업 티켓 생성
2. token-guardian에 예산/모델/요약정책 질의 → 제약 확정
3. 적합한 전문 에이전트에 "최소 컨텍스트 + 명확한 산출물 기준"으로 위임
4. 산출물 수령 → 게이트 검증(빌드/멱등성/검증커맨드)
5. 상태 다이제스트 갱신(.claude 상태 메모) → 다음 단계
6. Phase 완료마다 report-agent에 결과 누적
```

---

## 5. 토큰 효율 규칙 (전 에이전트 강제) — token-guardian 정책

> 폐쇄망/대규모 반복 작업에서 **토큰 낭비가 곧 비용·지연**이다. 아래는 위반 불가 규칙.

1. **다이제스트 우선**: 플레이북 내용은 `docs/PLAYBOOK_ANALYSIS.md`로 참조한다. 원본 yml은 *수정/정밀확인이 필요한 1개*만 범위 지정해 읽는다. 25개 일괄 재읽기 금지.
2. **출력 요약**: ansible 실행 로그 전문을 컨텍스트로 되붙이지 않는다. **changed/ok/failed 카운트 + 실패 task명 + 마지막 에러 10줄**만 요약해 전달.
3. **검색 우선**: 전체 파일 읽기 대신 Grep/Glob로 위치를 먼저 좁힌다.
4. **모델 라우팅**: 설계/통합/디버깅=상위 모델, 단순 변환/포맷/보일러플레이트=하위(haiku)로 위임 제안.
5. **상태 다이제스트**: 진행상황은 짧은 상태표 1개로 유지·갱신(재서술 금지). 이미 확정된 사실 재유도 금지.
6. **배치 호출**: 의존성 없는 도구 호출은 한 번에 병렬로.
7. **재진입 비용**: 5분(prompt cache TTL) 넘는 대기/슬립 지양. 불필요한 폴링 금지.
8. **산출물 기준 선언**: 모든 위임은 "완료 정의(Definition of Done)"를 1~3줄로 먼저 명시 → 왕복 횟수 최소화.
9. **중복 차단**: token-guardian는 이미 수행된 분석/생성을 감지하면 재실행을 막고 기존 산출물을 가리킨다.

---

## 6. 검증 게이트 (단계 성공 판정)

각 step은 `verify_cmd`로 사후 검증한다(대시보드/보고서에 반영). 대표 기준(`PLAYBOOK_ANALYSIS.md §5`):
- 06 `rabbitmqctl status`, user `vcm` 존재
- 07 `java -version`
- 08-1 `VCSM` DB + import + `vcsm` 로그인
- 08-3 `SHOW SLAVE STATUS`: `Slave_IO_Running=Yes` & `Slave_SQL_Running=Yes`
- 15b `systemctl is-active vcs vcweb vcapi` = active
- 17/18 광 NIC ifcfg 존재 + RX ringbuffer=2047

**멱등성 게이트**: 가능한 단계는 2회 실행해 `changed=0` 확인.

---

## 7. 보안/리팩토링 백로그 (product가 흡수)
- 평문 비밀번호(`root.123` 등) → **Ansible Vault / 대시보드 입력**으로 이전
- 버전 경로 하드코딩(RMQ 3.7.13, JDK 1.8.0.362, mariadb el8) → 변수/glob
- NIC명 하드코딩(18) → 17 자동탐지 결과 재사용
- ~~사이트 경로 `/root/lter_vcs_gimhae/` → `{{ asset_root }}` 변수화~~ **(완료)** — 플레이북 `src:` 22곳을 `{{ asset_root }}`(그룹변수)로 치환. 대시보드 인벤토리에서 편집, git `asset_dest`와 일치시킬 것.
- 폐쇄망 NTP(04) 정책 확정(주석 해제/대체)

---

## 8. 개발 단계 로드맵 (orchestrator 기본 계획)
1. ~~**M1 골격**~~ ✅: `pipeline.py`(step 정의=§3 표), inventory 예시
2. ~~**M2 엔진**~~ ✅: `orchestrator.py`(ansible 호출/로그 캡처/검증, 스레드), `state.py`(SQLite)
3. ~~**M3 대시보드**~~ ✅: **Flask** + SSE 로그 + 파이프라인 보드 + 시작/중지/재시도 + Git 자산동기화 + 인벤토리 편집
4. ~~**M4 보고서**~~ ✅: `report.py`(단계결과·검증·멱등성 집계 → HTML/MD, `/api/report.md|.html`)
5. **M5 강화**(진행 중): ~~HA 2노드 분리실행(`--limit`)~~ ✅ · ~~멱등성 2회 회귀(changed=0, 보고서 반영)~~ ✅ · Vault(비밀번호) · 오프라인 vendoring(예정)

---

## 9. 작업 규칙 (Claude Code 운영)
- 개발 브랜치: `claude/charming-wright-xn7fco`. 다른 브랜치 푸시 금지.
- PR은 사용자가 명시 요청할 때만 생성.
- 커밋 메시지는 명확하게. 푸시는 `git push -u origin <branch>` (네트워크 실패 시 지수 백오프 재시도).
- 새 작업 시작 전 이 문서 + `docs/PLAYBOOK_ANALYSIS.md` + 현재 상태 다이제스트를 먼저 확인.

---
name: dashboard-agent
description: 웹 대시보드 + FastAPI 백엔드 + 실시간 로그 스트림 담당. 파이프라인 보드(4-Phase, 단계 상태), 시작/중지/재시도, SSE/WebSocket 로그, 인벤토리 편집, 보고서 다운로드 UI를 개발한다. 대시보드/백엔드/오케스트레이션 엔진 작업에 사용.
tools: Read, Grep, Glob, Edit, Write, Bash
model: sonnet
---

# Dashboard Agent — 대시보드 & 엔진

CLAUDE.md §2 아키텍처를 구현한다. **폐쇄망 + CentOS7 시스템 Python 3.6** 제약 →
웹 프레임워크는 **Flask**(async 불필요, 스레드 기반), 의존성은 오프라인 설치 가능해야 한다(vendoring).

## 백엔드(Flask) 산출물 (M3 구현 완료)
- `backend/app.py`: 라우트
  - `GET /api/pipeline` (step 정의+mode), `GET /api/inventory`
  - `POST /api/run` (all/phase/step 실행, 재시도), `POST /api/stop`, `POST /api/reset`
  - `GET /api/status`, `GET /api/logs`, `GET /api/logs/stream` (SSE)
  - `GET /api/report` (간이 집계 → 정식 보고서는 report-agent)
  - `GET /api/git`, `POST /api/git/config`, `POST /api/git/sync` (자산 동기화)
- `backend/orchestrator.py`: `ansible-playbook` subprocess 실행(스레드), **stdout 라인 캡처→이벤트 버스**, RECAP 파싱(changed/ok/failed), `mock|check|real` 모드.
- `backend/gitassets.py`: 사내망에서 git clone/pull로 RPM/파일 선반입(asset_dest).
- `backend/events.py`: 공용 이벤트 버스(queue 기반 SSE 브로드캐스트).
- `backend/pipeline.py`: §3 표를 step 메타 리스트로(코드 단일 진실).
- `backend/state.py`: SQLite — step 상태, 로그, 실행 이력, settings(git 설정).

## 남은 작업(백로그)
- 인벤토리 **편집/저장** UI(현재 읽기 전용) — server_id/peer_ip/광NIC 폼.
- mock 실패 주입 토글(실패/재시도 UI 검증).
- step별 로그 필터/검증결과 패널.

## 프론트 산출물
- 4-Phase 파이프라인 보드: step 카드 + 상태 배지 + 진행률.
- 실시간 로그 패널(SSE 구독, 자동 스크롤, step별 필터).
- 컨트롤: 전체 실행 / Phase 실행 / step 실행·재시도 / 중지.
- 인벤토리 폼(server_id, peer_ip, NIC명 등 §PLAYBOOK_ANALYSIS §2 변수).

## 원칙
- 로그는 **스트림**으로만 보여주고, 백엔드는 메모리에 전문을 무한 적재하지 않는다(파일/SQLite로 흘림).
- 상태 폴링 대신 SSE 푸시.
- 토큰 절약: 대용량 로그를 LLM 컨텍스트로 되붙이지 않는다.

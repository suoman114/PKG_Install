# LTE-R 녹취서버 PKG Install 자동화 (폐쇄망)

LTE-R(철도 통합무선망) **녹취서버 VCS**를 **폐쇄망**에서 자동 설치하는 도구.
한 사이클에 **OS Setup → PKG Install → Config Setup → 결과 보고서**를 완결하며,
웹 대시보드에서 모든 단계를 관리하고 **실시간 설치 로그**를 보여준다.

## 문서 지도
- **[CLAUDE.md](./CLAUDE.md)** — 설계·오케스트레이션 정본(에이전트 분담, 토큰 통제 규칙, 아키텍처). **여기서 시작**.
- **[docs/PLAYBOOK_ANALYSIS.md](./docs/PLAYBOOK_ANALYSIS.md)** — 25개 Ansible 플레이북 분석 다이제스트(파이프라인/변수/위험).
- **[.claude/agents/](./.claude/agents/)** — Claude Code 서브에이전트 정의(orchestrator, token-guardian ⭐, os/pkg/config/dashboard/report).

## 구성
```
ansible/playbooks/   # 실제 설치 작업(00~18) — 단일 진실
ansible/inventory/   # vcs 그룹, 2노드 HA host_vars
backend/             # Flask + 오케스트레이션 엔진 + git 자산 동기화 (M3 완료)
frontend/            # 대시보드(SSE 로그, 4-Phase 보드, 자산 동기화)
docs/                # 분석/파이프라인 문서, WSL 실행 가이드
```

## 빠른 실행 (WSL CentOS7)
```bash
python3 -m pip install --user -r backend/requirements.txt
./run_dev.sh                 # mock 모드 → http://localhost:8800
```
상세는 **[docs/WSL_DEV.md](./docs/WSL_DEV.md)**. 사내망에서 Git으로 RPM/자산을
선반입(자산 동기화) 후 폐쇄망 현장에서 OS→PKG→Config 설치를 진행한다.

## 개발 방식
오케스트레이터(메인 Claude)가 작업을 분해해 전문 서브에이전트에 위임한다.
모든 위임은 **token-guardian**의 토큰 예산·요약·중복차단 게이트를 거친다(CLAUDE.md §5).

## 핵심 제약
- 폐쇄망(오프라인 vendoring), 2노드 MariaDB Master-Master(HA), 멱등성, RHEL/CentOS 7·8.

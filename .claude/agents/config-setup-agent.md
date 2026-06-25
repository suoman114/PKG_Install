---
name: config-setup-agent
description: Phase 3 Config Setup 담당. 계정/권한(09,10), cron(12), ld.so/setcap(13), ramdisk(14), iptables(15a), systemd 서비스(15b), watermark(16), 광 NIC(17), ringbuffer(18)를 파이프라인 step으로 래핑·검증한다. 계정·서비스·네트워크 구성 작업에 사용.
tools: Read, Grep, Glob, Edit, Write, Bash
model: sonnet
---

# Config Setup Agent — Phase 3

담당 플레이북: `9-1/9-2`(vcs계정), `10-1/10-2`(vcweb계정), `12_Cron_root`, `13_ldconf_el8`, `14_ramdisk`, `15_rclocal`, `15_service_start_el8`, `16_watermark`, `17_get_fibre_nic`, `18_ringbuffer_conf`.
세부는 `docs/PLAYBOOK_ANALYSIS.md §1` 09~18 행 참조.

## 핵심 도메인
- **계정**: vcs(uid802/gid801), vcweb(uid1002/gid1002). 이후 단계(11,12,15b)의 선행 조건.
- **서비스 기동(15b)**: vcs/vcweb/vcapi systemd 유닛 생성+enable+start — Phase3의 핵심 게이트.
- **광 NIC(17/18)**: NIC명이 현장마다 다름. 18의 하드코딩(ens1f0np0 등)을 17의 자동탐지 결과로 치환하도록 통합 제안.

## verify_cmd
- 09/10: `id vcs`, `id vcweb` 성공
- 12: `crontab -l -u vcs` 항목 존재
- 13: `ldconfig -p | grep oam`, java `getcap` 권한 확인
- 14: `mount | grep /home/vcs/ramdisk` tmpfs
- 15a: `iptables -t nat -L PREROUTING | grep 17080`
- 15b: `systemctl is-active vcs vcweb vcapi` = active
- 16: `/home/vcs/REC/watermark.png` 존재
- 17/18: 광 NIC ifcfg 존재 + `ethtool -g <nic>` RX=2047

## 주의 / 백로그
- 18 NIC명 하드코딩 → 파라미터화(17 결과 재사용).
- 13 java 경로 버전 하드코딩 → glob.
- 계정 비밀번호 평문 파생 → Vault.

## 출력 규약
실행 로그는 요약(`changed/ok/failed` + 실패 task + 에러 ≤10줄)만 전달.

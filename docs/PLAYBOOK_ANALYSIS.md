# 플레이북 분석 다이제스트 (Playbook Analysis Digest)

> **목적**: 25개 Ansible 플레이북(`ansible/playbooks/`)의 핵심을 1회 분석해 압축 저장한 문서.
> 모든 에이전트는 원본 yml 25개를 매번 다시 읽지 말고 **이 문서를 먼저 참조**한다. (토큰 절약 1순위 규칙)
> 원본을 봐야 할 때만 해당 파일 1개를 범위 지정해 읽는다.

---

## 0. 프로젝트 도메인 요약

- **대상 시스템**: LTE-R(철도 통합무선망) **녹취(음성 레코딩) 서버 VCS** — 김해(gimhae) 사이트 기준 자산 경로 사용
- **운영 환경**: **폐쇄망(air-gapped)** — 인터넷 불가, 모든 RPM/패키지는 로컬 경로(`/root/lter_vcs_gimhae/...`)에서 공급
- **대상 OS**: RHEL/CentOS **7 / 8** (el7, el8 분기 존재). 신규 기준은 **el8**
- **Ansible 호스트 그룹**: `vcs` (대부분 `connection: local` 또는 `remote_user: root`)
- **이중화(HA)**: MariaDB **Master-Master 복제 (Active/Standby)** — 2노드 구성
- **앱 계정 2종**:
  - `vcs`  : uid 802 / gid 801 (녹취 엔진)
  - `vcweb`: uid 1002 / gid 1002 (웹/API)
- **systemd 서비스 3종**: `vcs.service`, `vcweb.service`, `vcapi.service` + 인프라 `mariadb`, `rabbitmq-server`
- **앱 홈**: `/home/vcs/HOME` (UASYS_HOME), `/home/vcweb`

---

## 1. 표준 설치 파이프라인 (정본 순서 = 파일 번호 순)

> 번호가 곧 **의존성 순서**다. 대시보드는 이 순서대로 단계를 표시/실행한다.
> Phase 컬럼: **OS** = OS Setup, **PKG** = 패키지 설치, **CFG** = Config Setup.

| Step | 파일 | Phase | 한 줄 요약 | 핵심 입력 변수 | 위험/주의 |
|------|------|-------|-----------|---------------|-----------|
| 00 | `0_auto_pass.yml` | OS | 노드 간 SSH 키 교환(known_hosts/authorized_keys), **SELinux disabled** | `ansible_host`, `ansible_password=root.123` | 평문 비밀번호 하드코딩, ssh-keygen 비대화식 |
| 01 | `1_PAM_limits.yml` | OS | `pam_limits` nofile/stack/nproc=65535, core=20480 (root, @vcs, @vcweb) | 없음 | 멱등(idempotent) |
| 02 | `2_systemctl_stop.yml` | OS | cron 권한 600, net-snmp/postfix 제거, NM/chronyd/avahi/firewalld 중지, rsyslog에 `local0→/var/log/vcs.log` | 없음 | el7/el8 분기(`< 8`), rpm 버전 문자열 하드코딩 |
| 03 | `3_sysctl.yml` | OS | 커널/네트워크 튜닝 → `/etc/sysctl.d/vcs_system.conf` (rmem/wmem, msg*, ipv6 off, core_pattern) | 없음 | 멱등 |
| 04 | `4_chrony_el8.yml` | OS | **timezone=Asia/Seoul** 만 활성 (NTP 설치/동기화 블록은 전부 주석) | (`ntp_ip1`,`ntp_ip2` 주석됨) | NTP 미구성 — 폐쇄망 NTP 필요시 주석 해제 설계 필요 |
| 05 | `5_visudo_vcs.yml` | OS | `/etc/sudoers`에 `%vcs ALL=(ALL) ALL` (visudo -cf 검증) | 없음 | 멱등 |
| 06 | `6_RMQ_vcs.yml` | PKG | RabbitMQ RPM 설치+config(watermark 2GB,heartbeat1)+plugin(mgmt,timestamp)+user `vcm/vcm.123(administrator)`, `/etc/hosts`·`rabbitmq-env.conf` NODENAME 설정 | `hostname`(파생) | RPM 로컬 copy 후 `find`→`yum`, plugin .ez 경로 버전 하드코딩(3.7.13) |
| 06b | `6_RMQ_vcs_account_only.yml` | PKG | (옵션) RMQ 계정만 생성 — 재설치/계정 누락 복구용 | 없음 | 06의 부분집합 |
| 07 | `7_openjdk.yml` | PKG | OpenJDK RPM 로컬 설치(`find`→`yum`) | 없음 | gpg_check 비활성 |
| 08-1 | `8-1_mariaDB.yml` | PKG | 기존 mariadb 제거 → el8 RPM 설치 → my.cnf 덮어쓰기 → `server-id` 치환 → DB `VCSM` 생성 → `LTE_R_DB_init.sql` import → 계정 `vcsm/vcsm.123`(% 및 localhost) | `server_id`, `new_password=root.123`, `vcsm_password=vcsm.123` | **server_id 호스트별 필수**, 초기 덤프 경로 의존 |
| 08-2 | `8-2_mariaDB_chown.yml` | PKG | `/var/log/mysql` chown mysql + mariadb 재시작 | 없음 | 보조 |
| 08-3 | `8-3_ha_setting.yml` | PKG | MariaDB **Master-Master 복제** 설정(replication_gtid.cnf, repl user, changemaster, start slave, 상태확인) | `peer_ip`, `server_id`, `replication_user=replication`, `replication_pass=repl.123` | **host_vars에 peer_ip/server_id 필수**, 2노드 양방향 |
| 09-1 | `9-1_group_vcs.yml` | CFG | group `vcs`(gid801) + user `vcs`(uid802, openssl passwd `vcs.123`) + sshkey | `var1=vcs`,`acs_gid=801`,`acs_uid=802` | 평문 파생 비밀번호 |
| 09-2 | `9-2_chmod_vcs.yml` | CFG | `/home/vcs` owner vcs, mode 04755 | 없음 | |
| 10-1 | `10-1_group_vcweb.yml` | CFG | group `vcweb`(gid1002) + user `vcweb`(uid1002) + sshkey | `acs_gid=1002`,`acs_uid=1002` | |
| 10-2 | `10-2_chmod_vcweb.yml` | CFG | `/home/vcweb/vcweb/REC` owner vcweb, mode 04777 | 없음 | |
| 11 | `11_vcs_dic.yml` | PKG/CFG | 앱 패키지 대량 배포: bash/script/dic tar 풀기, `vcs_gimhae_pkg/usttif/oam` 배포, `vcapi` RPM 설치, `/home/vcweb/vcweb/REC → /home/vcs/REC` 심볼릭링크 | `vcapi_rpm_name=vcapi-0.0.1-4.noarch.rpm` | **계정(09/10) 선행 필수**, 다수 tar.gz 로컬 의존 |
| 12 | `12_Cron_root.yml` | CFG | crontab: hw 점검(매분), `vced/bin/run.sh start`(매분), `cron/bin/run.sh`(매일 0:30) | `var1=vcs` | |
| 13 | `13_ldconf_el8.yml` | CFG | `ld.so.conf.d`에 oam.conf/java_el8.conf 복사, java에 `setcap cap_net_raw,cap_net_admin`, `ldconfig` | `app_user` | **java 경로 버전 하드코딩**(1.8.0.362) |
| 14 | `14_ramdisk.yml` | CFG | tmpfs ramdisk `/home/vcs/ramdisk` 10g mount(01777, owner vcs) | `ramdisk_size=10g` | fstab 영속 mount |
| 15a | `15_rclocal.yml` | CFG | iptables nat `80→17080` 리다이렉트 (rc.local + iptables 모듈 이중) | 없음 | |
| 15b | `15_service_start_el8.yml` | CFG | systemd 유닛 3종(vcs/vcweb/vcapi) 생성 + daemon-reload + enable+start | (유닛 내 환경변수 다수) | **핵심 서비스 기동 단계**, 환경변수/경로 하드코딩 |
| 16 | `16_watermark.yml` | CFG | `watermark.png` → `/home/vcs/REC/` 복사(owner vcs) | 없음 | |
| 17 | `17_get_fibre_nic.yml` | CFG | FIBRE(광) NIC 자동탐지 → ifcfg 없는 포트 생성(BOOTPROTO=none)+ifup | 없음 | `ethtool ... FIBRE` 필터 |
| 18 | `18_ringbuffer_conf.yml` | CFG | 광 NIC RX ringbuffer 2047 적용 + rc.local 영속화 | 없음 | **NIC명 하드코딩**(ens1f0np0/ens1f1np1/ens4f0np0/ens4f1np1) |

---

## 2. 필수 인벤토리/변수 (host_vars에서 반드시 공급해야 하는 값)

플레이북이 `hostvars[...]` / `{{ var }}` 로 참조하지만 **정의되지 않은** 변수들 → 인벤토리/호스트변수에서 주입 필요:

| 변수 | 사용 위치 | 의미 | 비고 |
|------|----------|------|------|
| `ansible_host` | 00 | 노드 IP | 인벤토리 |
| `server_id` | 08-1, 08-3 | MariaDB 복제용 서버 ID | **노드마다 유일** (예: 1, 2) |
| `peer_ip` | 08-3 | 상대 노드 IP(복제 대상) | Active/Standby 짝 |
| `app_user` | 13 | 앱 사용자(=vcs) | |
| `ntp_ip1`,`ntp_ip2` | 04(주석) | 폐쇄망 NTP 서버 | NTP 활성화 시 |
| `ramdisk_size` | 14 | tmpfs 크기 | 기본 10g |
| NIC명 목록 | 18 | 광포트 인터페이스명 | **현장마다 다름 → 파라미터화 필요** |

---

## 3. 하드코딩 / 리팩토링 대상 (자동화 product가 파라미터로 빼야 할 것)

1. **평문 비밀번호**: `root.123`, `vcm.123`, `vcsm.123`, `repl.123`, `vcs.123`, `vcweb.123` → Ansible Vault 또는 대시보드 입력값으로 분리
2. **버전 의존 경로**: RabbitMQ `3.7.13` plugin 경로, JDK `1.8.0.362` setcap 경로, mariadb el8 RPM 디렉토리 → 변수/glob 처리
3. **NIC명 하드코딩**(18) → 17의 자동탐지 결과(`optical_ifaces`)를 재사용하도록 통합
4. **rpm 버전 문자열**(02 net-snmp/postfix `el7`) → el8 대응 분기
5. **사이트 경로** `/root/lter_vcs_gimhae/` → `{{ asset_root }}` 변수화 (사이트 이식성)
6. **NTP(04)**: 폐쇄망 시간동기화가 주석 처리됨 → 운영 정책상 활성/대체 설계 필요

---

## 4. 단계 그룹핑 (대시보드 4-Phase 매핑)

- **Phase 1 · OS Setup**: 00, 01, 02, 03, 04, 05
- **Phase 2 · PKG Install**: 06, 07, 08-1, 08-2, 08-3, 11(앱패키지)
- **Phase 3 · Config Setup**: 09-1, 09-2, 10-1, 10-2, 12, 13, 14, 15a, 15b, 16, 17, 18
- **Phase 4 · Report**: 전 단계 결과 집계 → 보고서 생성

> 주의: 11은 PKG 성격이지만 계정(09/10) 선행 필요 → 실행 순서상 09/10 뒤에 둔다.
> 실제 실행 정본 순서는 §1 표의 번호 순서를 따른다 (대시보드 파이프라인 = §1).

---

## 5. 멱등성/검증 포인트 (각 단계 성공 판정 기준 후보)

- 01/03/05: 설정파일 라인 존재 → 재실행 시 changed=0 이어야 정상
- 06: `rabbitmqctl status` OK, user `vcm` 존재
- 07: `java -version` 동작
- 08-1: `VCSM` DB 존재 + 테이블 import 성공 + `vcsm` 로그인
- 08-3: `SHOW SLAVE STATUS` 의 `Slave_IO_Running=Yes`, `Slave_SQL_Running=Yes`
- 15b: `systemctl is-active vcs vcweb vcapi` = active
- 17/18: 광 NIC ifcfg 존재 + ringbuffer RX=2047

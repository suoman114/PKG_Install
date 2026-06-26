"""
pipeline.py — 설치 파이프라인 정의 (단일 진실)

CLAUDE.md §3 / docs/PLAYBOOK_ANALYSIS.md §1 의 표를 코드로 옮긴 것.
실행 순서는 리스트 순서(= 파일 번호 순 = 의존성 순)를 따른다.

각 Step 메타:
  id           : 파이프라인 단계 식별자
  phase        : OS | PKG | CFG
  name         : 사람이 읽는 단계명
  playbook     : ansible/playbooks/ 하위 yml 파일명
  depends_on   : 선행 step id 목록
  required_vars: 인벤토리/host_vars에서 반드시 공급돼야 하는 변수
  verify_cmd   : 성공 사후 검증 셸 커맨드(대시보드/보고서 반영)
  idempotent   : 2회 실행 시 changed=0 기대 여부
"""

PHASES = [
    ("OS", "1. OS Setup"),
    ("PKG", "2. PKG Install"),
    ("CFG", "3. Config Setup"),
]


class Step(object):
    def __init__(self, id, phase, name, playbook, depends_on=None,
                 required_vars=None, verify_cmd=None, idempotent=True,
                 required_secrets=None):
        self.id = id
        self.phase = phase
        self.name = name
        self.playbook = playbook
        self.depends_on = depends_on or []
        self.required_vars = required_vars or []
        self.verify_cmd = verify_cmd
        self.idempotent = idempotent
        # 실행에 반드시 필요한 시크릿(group_vars/vcs.yml) — 🔒 시크릿 패널 키와 일치
        self.required_secrets = required_secrets or []

    def to_dict(self):
        return {
            "id": self.id,
            "phase": self.phase,
            "name": self.name,
            "playbook": self.playbook,
            "depends_on": self.depends_on,
            "required_vars": self.required_vars,
            "verify_cmd": self.verify_cmd,
            "idempotent": self.idempotent,
            "required_secrets": self.required_secrets,
        }


STEPS = [
    # ---- Phase 1: OS Setup ----
    Step("00", "OS", "SSH 키 교환 / SELinux 비활성", "0_auto_pass.yml",
         required_vars=["ansible_host"], required_secrets=["ssh_password"],
         verify_cmd="getenforce", idempotent=False),
    Step("01", "OS", "PAM limits (nofile/stack/nproc)", "1_PAM_limits.yml",
         verify_cmd="grep -R 65535 /etc/security/limits.* | head -n1"),
    Step("02", "OS", "불필요 서비스 중지 / rsyslog", "2_systemctl_stop.yml",
         depends_on=["00"],
         verify_cmd="systemctl is-active firewalld; grep -q vcs.log /etc/rsyslog.conf && echo rsyslog-ok"),
    Step("03", "OS", "sysctl 커널/네트워크 튜닝", "3_sysctl.yml",
         verify_cmd="sysctl -n vm.vfs_cache_pressure"),
    Step("04", "OS", "timezone (Asia/Seoul)", "4_chrony_el8.yml",
         verify_cmd="timedatectl | grep 'Time zone'"),
    Step("05", "OS", "sudoers (%vcs)", "5_visudo_vcs.yml",
         verify_cmd="visudo -cf /etc/sudoers"),

    # ---- Phase 2: PKG Install ----
    Step("06", "PKG", "RabbitMQ 설치/설정/계정", "6_RMQ_vcs.yml",
         depends_on=["02"], required_secrets=["rmq_vcm_password"],
         verify_cmd="rabbitmqctl list_users | grep vcm"),
    Step("07", "PKG", "OpenJDK 설치", "7_openjdk.yml",
         verify_cmd="java -version"),
    Step("08-1", "PKG", "MariaDB 설치 / VCSM DB import", "8-1_mariaDB.yml",
         required_vars=["server_id"],
         required_secrets=["mariadb_root_password", "mariadb_vcsm_password"],
         verify_cmd="mysql -uroot -e \"SHOW DATABASES\" | grep VCSM"),
    Step("08-2", "PKG", "MariaDB 로그 권한 / 재기동", "8-2_mariaDB_chown.yml",
         depends_on=["08-1"],
         verify_cmd="systemctl is-active mariadb"),
    Step("08-3", "PKG", "MariaDB Master-Master 복제(HA)", "8-3_ha_setting.yml",
         depends_on=["08-1"], required_vars=["server_id", "peer_ip"],
         required_secrets=["replication_password"],
         verify_cmd="mysql -e \"SHOW SLAVE STATUS\\G\" | grep -E 'Slave_IO_Running|Slave_SQL_Running'",
         idempotent=False),

    # ---- Phase 3: 계정 (앱패키지 11의 선행) ----
    Step("09-1", "CFG", "vcs 계정/그룹 생성", "9-1_group_vcs.yml",
         required_secrets=["vcs_os_password"], verify_cmd="id vcs"),
    Step("09-2", "CFG", "vcs 홈 권한(04755)", "9-2_chmod_vcs.yml",
         depends_on=["09-1"], verify_cmd="stat -c '%U %a' /home/vcs"),
    Step("10-1", "CFG", "vcweb 계정/그룹 생성", "10-1_group_vcweb.yml",
         required_secrets=["vcweb_os_password"], verify_cmd="id vcweb"),
    Step("10-2", "CFG", "vcweb REC 권한(04777)", "10-2_chmod_vcweb.yml",
         depends_on=["10-1"], verify_cmd="stat -c '%U %a' /home/vcweb/vcweb/REC"),

    # ---- Phase 2: 앱 패키지 (계정 선행 필요) ----
    Step("11", "PKG", "앱 패키지 배포 / vcapi 설치", "11_vcs_dic.yml",
         depends_on=["09-1", "10-1"],
         verify_cmd="rpm -q vcapi; test -L /home/vcweb/vcweb/REC && echo link-ok"),

    # ---- Phase 3: Config Setup ----
    Step("12", "CFG", "cron 등록(root/vcs)", "12_Cron_root.yml",
         depends_on=["09-1"], verify_cmd="crontab -l -u vcs"),
    Step("13", "CFG", "ld.so.conf / setcap / ldconfig", "13_ldconf_el8.yml",
         depends_on=["07"], required_vars=["app_user"],
         verify_cmd="ldconfig -p | grep -i oam || true"),
    Step("14", "CFG", "ramdisk(tmpfs) mount", "14_ramdisk.yml",
         depends_on=["09-1"], required_vars=["ramdisk_size"],
         verify_cmd="mount | grep /home/vcs/ramdisk"),
    Step("15a", "CFG", "iptables 80→17080 리다이렉트", "15_rclocal.yml",
         verify_cmd="iptables -t nat -L PREROUTING | grep 17080"),
    Step("15b", "CFG", "systemd 서비스 기동(vcs/vcweb/vcapi)", "15_service_start_el8.yml",
         depends_on=["11"],
         verify_cmd="systemctl is-active vcs vcweb vcapi",
         idempotent=False),
    Step("16", "CFG", "watermark.png 배포", "16_watermark.yml",
         depends_on=["09-1"], verify_cmd="test -f /home/vcs/REC/watermark.png && echo ok"),
    Step("17", "CFG", "광 NIC ifcfg 생성/활성", "17_get_fibre_nic.yml",
         verify_cmd="ls /etc/sysconfig/network-scripts/ifcfg-* 2>/dev/null", idempotent=False),
    Step("18", "CFG", "광 NIC RX ringbuffer=2047", "18_ringbuffer_conf.yml",
         depends_on=["17"], required_vars=["optical_nics"],
         verify_cmd="ethtool -g $(ls /sys/class/net | grep -v lo | head -n1) | grep RX",
         idempotent=False),
]

_BY_ID = {s.id: s for s in STEPS}


def all_step_ids():
    return [s.id for s in STEPS]


def get_step(step_id):
    return _BY_ID.get(step_id)


def steps_for_phase(phase):
    return [s for s in STEPS if s.phase == phase]


def as_dicts():
    return [s.to_dict() for s in STEPS]

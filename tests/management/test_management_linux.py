"""管理VMがUbuntu・SSH公開鍵・Linux監視として配備されることを検証する。

Bicepのコンパイル結果から、パスワード無効化・Entra SSH拡張・Linux AMA/DCR・Azureホスト時刻設定を確認する。実VMへのログイン成功は別の確認である。
"""
from tests.support.bicep_helpers import compile_bicep
from tests.support.arm_helpers import ArmExpression
from tests.audit.test_apim_audit_retention import AuditExpression

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from tests import REPOSITORY

CORE = REPOSITORY


class ManagementProfileExpression(ArmExpression):
    """管理VMのコンパイル式に使われる空値検査と浅いobject結合だけを補う。"""

    @staticmethod
    def render(value, parameters):
        if isinstance(value, dict):
            return {key: ManagementProfileExpression.render(item, parameters) for key, item in value.items()}
        if isinstance(value, list):
            return [ManagementProfileExpression.render(item, parameters) for item in value]
        if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
            return ManagementProfileExpression(value).evaluate(parameters)
        return value

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        if tree[0] == "call" and tree[1] in ("empty", "shallowMerge"):
            values = [self.evaluate(parameters, index, item, bindings) for item in tree[2]]
            if tree[1] == "empty":
                return not values[0]
            return {key: value for mapping in values[0] for key, value in mapping.items()}
        return super().evaluate(parameters, index, tree, bindings)


class LinuxTimeConfigurationTests(unittest.TestCase):
    """設定生成・PHC判定と時刻工程を合成fixtureで実行し、実サービスや時計には触れない。"""
    HOST_LINE = "refclock PHC /dev/ptp_hyperv poll 3 dpoll -2 offset 0 stratum 2 refid AZUR"
    TRACKING = "Reference ID    : 415A5552 (AZUR)\nLeap status     : Normal\n"

    @classmethod
    def setUpClass(cls):
        cls.source = (CORE / "infra/guest/configure-audit.sh").read_text(encoding="utf-8")
        cls.helpers = cls.source.split("# BEGIN TIME CONFIG HELPERS\n", 1)[1].split("# END TIME CONFIG HELPERS", 1)[0]

    def run_helper(self, expression, *arguments):
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + self.helpers + "\n" + expression,
                               "time-config-fixture", *map(str, arguments)],
                              capture_output=True, text=True, timeout=10, check=False,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def fixture(self, root):
        config, confdir, sources, dhcp = root / "chrony.conf", root / "conf.d", root / "sources.d", root / "dhcp"
        timesyncd, transaction = root / "timesyncd.conf", root / "transaction"
        for path in (confdir, sources, dhcp):
            path.mkdir()
        original = (f"confdir {confdir}\n"
                    "pool ntp.ubuntu.com iburst maxsources 4\n"
                    "pool 0.ubuntu.pool.ntp.org iburst maxsources 1\n"
                    "pool 1.ubuntu.pool.ntp.org iburst maxsources 1\n"
                    "pool 2.ubuntu.pool.ntp.org iburst maxsources 2\n"
                    f"sourcedir {dhcp}\nsourcedir {sources}\n"
                    "driftfile /var/lib/chrony/chrony.drift\nmakestep 1 3\n"
                    "refclock PHC /dev/ptp_hyperv poll 3 dpoll -2 offset 0\n")
        config.write_text(original, encoding="utf-8")
        return (config, confdir, sources, dhcp, timesyncd, transaction), original

    def transaction(self, mode, paths):
        return self.run_helper('time_configuration "$@"', mode, "synthetic-time-run", *paths)

    @staticmethod
    def active_lines(path):
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]

    def test_requires_chrony_and_never_falls_back_to_timesyncd(self):
        for states, expected in [
            (("loaded", "loaded"), "chrony.service"),
            (("not-found", "loaded"), "chronyd.service"),
        ]:
            with self.subTest(states=states):
                result = self.run_helper('select_time_service "$1" "$2"', *states)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
        result = self.run_helper('select_time_service "$1" "$2"', "not-found", "not-found")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_phc_device_requires_character_symlink_and_hyperv_clock(self):
        with tempfile.TemporaryDirectory(prefix="core-host-phc-") as folder:
            root = Path(folder)
            device = root / "device"
            device.symlink_to("/dev/null")
            sysfs = root / "ptp7"
            sysfs.mkdir()
            clock = sysfs / "clock_name"
            expression = 'readlink() { printf "/dev/ptp7\\n"; }; validate_host_clock "$1" "$2"'
            for name, success in (("hyperv\n", True), ("mlx5\n", False)):
                with self.subTest(clock=name):
                    clock.write_text(name)
                    result = self.run_helper(expression, device, root)
                    self.assertEqual(result.returncode == 0, success, result.stderr)
            clock.unlink()
            self.assertNotEqual(self.run_helper(expression, device, root).returncode, 0)
            device.unlink()
            device.write_text("ordinary file")
            clock.write_text("hyperv\n")
            self.assertNotEqual(self.run_helper(expression, device, root).returncode, 0)
            device.unlink()
            device.symlink_to("/dev/null")
            self.assertNotEqual(self.run_helper('validate_host_clock "$1" "$2"', device, root).returncode, 0)

    def test_known_os_sources_are_disabled_and_tuning_is_preserved_on_reapply(self):
        with tempfile.TemporaryDirectory(prefix="core-chrony-host-") as folder:
            paths, original = self.fixture(Path(folder))
            config, confdir, sources, dhcp, _, transaction = paths
            config.chmod(0o640)
            tuning = confdir / "tuning.conf"
            tuning.write_text("maxupdateskew 100.0\nrtcsync\n")
            host = confdir / "hyperv.conf"
            host.write_text("refclock PHC /dev/ptp_hyperv poll 3 dpoll -2 offset 0 stratum 2\n")
            (sources / "ubuntu.sources").write_text("pool ntp.ubuntu.com iburst\n")
            (dhcp / "lease.sources").write_text("server ntp.ubuntu.com iburst\n")
            before_sources = (sources / "ubuntu.sources").read_text()
            first_config = None
            for _ in range(2):
                result = self.transaction("prepare", paths)
                self.assertEqual(result.returncode, 0, result.stderr)
                active = self.active_lines(config)
                self.assertIn(self.HOST_LINE, active)
                self.assertEqual(sum(line.startswith("refclock ") for line in active), 1)
                self.assertFalse(any(line.split()[0] in ("server", "pool", "peer", "sourcedir") for line in active))
                self.assertIn(f"confdir {confdir}", active)
                self.assertIn("makestep 1 3", active)
                self.assertEqual(tuning.read_text(), "maxupdateskew 100.0\nrtcsync\n")
                self.assertEqual(self.active_lines(host), [])
                self.assertEqual((sources / "ubuntu.sources").read_text(), before_sources)
                self.assertEqual(Path(str(config) + ".core-platform-original").read_text(), original)
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o640)
                self.assertEqual(stat.S_IMODE(Path(str(config) + ".core-platform-original").stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(transaction.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((transaction / "changes.json").stat().st_mode), 0o600)
                current = config.read_text()
                if first_config is not None:
                    self.assertEqual(current, first_config)
                    state = json.loads((transaction / "changes.json").read_text())
                    self.assertEqual(state["changes"], [])
                first_config = current
                self.assertEqual(self.transaction("commit", paths).returncode, 0)
                self.assertFalse(transaction.exists())

    def test_unknown_sources_and_include_paths_fail_before_any_changes(self):
        conditions = [
            ("root", "server time.organization.example iburst\n"),
            ("root", "peer ntp.ubuntu.com\n"),
            ("root", "include /etc/chrony/organization.conf\n"),
            ("root", "confdir /etc/chrony/organization.d\n"),
            ("root", "sourcedir /run/organization-time\n"),
            ("root", "refclock PHC /dev/ptp0 poll 3\n"),
            ("root", "local stratum 10\n"),
            ("conf", "pool time.organization.example iburst\n"),
            ("conf", "include /etc/chrony/organization.conf\n"),
            ("sources", "server time.organization.example iburst\n"),
            ("dhcp", "server time.organization.example iburst\n"),
        ]
        for target, content in conditions:
            with self.subTest(target=target, content=content):
                with tempfile.TemporaryDirectory(prefix="core-time-reject-") as folder:
                    paths, original = self.fixture(Path(folder))
                    config, confdir, sources, dhcp, _, transaction = paths
                    affected = config if target == "root" else {
                        "conf": confdir / "organization.conf",
                        "sources": sources / "organization.sources",
                        "dhcp": dhcp / "organization.sources",
                    }[target]
                    affected.write_text((original if target == "root" else "") + content)
                    before = affected.read_text()
                    result = self.transaction("prepare", paths)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("manual review", result.stderr)
                    self.assertEqual(affected.read_text(), before)
                    self.assertFalse(transaction.exists())
                    self.assertFalse(Path(str(config) + ".core-platform-original").exists())

    def test_configuration_and_backup_symlinks_are_rejected(self):
        for kind in ("root-link", "directory-link", "backup-link"):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory(prefix="core-time-preserve-") as folder:
                    root = Path(folder)
                    paths, original = self.fixture(root)
                    config, confdir, _, _, _, transaction = paths
                    external = root / "untouched"
                    external.write_text("synthetic original")
                    if kind == "root-link":
                        config.unlink()
                        config.symlink_to(external)
                    elif kind == "directory-link":
                        confdir.rmdir()
                        confdir.symlink_to(root)
                    elif kind == "backup-link":
                        Path(str(config) + ".core-platform-original").symlink_to(external)
                    result = self.transaction("prepare", paths)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("manual review", result.stderr)
                    self.assertEqual(external.read_text(), "synthetic original")
                    if kind != "root-link":
                        self.assertEqual(config.read_text(), original)
                    self.assertFalse(transaction.exists())

    def test_pending_transaction_is_not_discarded_or_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="core-time-pending-") as folder:
            paths, _ = self.fixture(Path(folder))
            transaction = paths[-1]
            transaction.mkdir()
            marker = transaction / "changes.json"
            marker.write_text("synthetic pending state")
            result = self.transaction("prepare", paths)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(), "synthetic pending state")

    def test_check_mode_validates_the_plan_without_creating_or_changing_files(self):
        with tempfile.TemporaryDirectory(prefix="core-time-check-only-") as folder:
            root = Path(folder)
            paths, _ = self.fixture(root)
            before = {str(path.relative_to(root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                      for path in root.rglob("*") if path.is_file()}
            result = self.transaction("check", paths)
            self.assertEqual(result.returncode, 0, result.stderr)
            proof = json.loads(result.stdout)
            self.assertIs(proof["configurationKnown"], True)
            self.assertIs(proof["hostConfigurationCurrent"], False)
            after = {str(path.relative_to(root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                     for path in root.rglob("*") if path.is_file()}
            self.assertEqual(after, before)
            self.assertFalse(paths[-1].exists())
            paths[-1].mkdir()
            result = self.transaction("check", paths)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual({str(path.relative_to(root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                              for path in root.rglob("*") if path.is_file()}, before)

    def test_rollback_requires_ownership_and_preserves_independent_edits(self):
        with tempfile.TemporaryDirectory(prefix="core-time-restore-boundary-") as folder:
            paths, _ = self.fixture(Path(folder))
            config, confdir, _, _, _, transaction = paths
            included = confdir / "hyperv.conf"
            included.write_text("refclock PHC /dev/ptp_hyperv poll 3 dpoll -2 offset 0\n")
            prepared = self.transaction("prepare", paths)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            planned_root = config.read_text()
            original_manifest = (transaction / "changes.json").read_text()
            wrong_owner = self.run_helper('time_configuration "$@"', "rollback", "another-time-run", *paths)
            self.assertNotEqual(wrong_owner.returncode, 0)
            self.assertEqual(config.read_text(), planned_root)
            self.assertEqual((transaction / "changes.json").read_text(), original_manifest)
            included.write_text("# independently edited\n")
            restored = self.transaction("rollback", paths)
            self.assertNotEqual(restored.returncode, 0)
            self.assertIn("manual restoration", restored.stderr)
            # 全件確認前に最初のrootだけを復旧しないことも確認する。
            self.assertEqual(config.read_text(), planned_root)
            self.assertEqual(included.read_text(), "# independently edited\n")
            self.assertEqual((transaction / "changes.json").read_text(), original_manifest)

    def test_sync_verification_rejects_unsynchronised_or_network_sources(self):
        valid = "#,*,AZUR,2,3,377,1,0,0,0\n"
        cases = [
            (valid, self.TRACKING, True),
            ("#,?,AZUR,2,3,0,0,0,0,0\n", self.TRACKING, False),
            ("^,*,time.organization.example,2,3,377,1,0,0,0\n", self.TRACKING, False),
            (valid + "^,?,ntp.ubuntu.com,2,3,0,0,0,0,0\n", self.TRACKING, False),
            (valid, self.TRACKING.replace("Normal", "Not synchronised"), False),
            (valid, self.TRACKING.replace("415A5552 (AZUR)", "00000000 ()"), False),
        ]
        for sources, tracking, success in cases:
            with self.subTest(sources=sources, tracking=tracking):
                result = self.run_helper('verify_host_time "$1" "$2"', sources, tracking)
                self.assertEqual(result.returncode == 0, success, result.stderr)

    def run_time_phase(self, root, failure="", pending=False, time_only=False):
        """時刻工程の実コードを、明示fixtureのパスとサービスmockだけで実行する。"""
        import shlex
        paths, original = self.fixture(root)
        config, confdir, sources, dhcp, timesyncd, transaction = paths
        if pending:
            transaction.mkdir()
            (transaction / "changes.json").write_text("synthetic previous transaction")
        device = root / "device"
        device.symlink_to("/dev/null")
        sysroot = root / "ptp"
        (sysroot / "ptp7").mkdir(parents=True)
        (sysroot / "ptp7/clock_name").write_text("hyperv\n")
        calls = root / "calls"
        rsyslog = root / "rsyslog"
        ssh = root / "ssh"
        rsyslog.mkdir()
        ssh.mkdir()
        (rsyslog / "00-core-command-redaction.conf").write_text("synthetic existing rsyslog configuration\n")
        (ssh / "00-core-access.conf").write_text("synthetic existing SSH configuration\n")
        stubs = r'''
calls=__CALLS__
failure=__FAILURE__
time_only_fixture=__TIME_ONLY__
restart_count=0
id() { printf '0\n'; }
uname() { printf 'Linux\n'; }
command() {
    if [[ $1 == -v && $2 == sshd && $time_only_fixture == true ]]; then
        printf 'unexpected command -v sshd\n' >> "$calls"
        return 1
    fi
    builtin command "$@"
}
sshd() { printf 'unexpected sshd\n' >> "$calls"; return 1; }
rsyslogd() { printf 'unexpected rsyslogd\n' >> "$calls"; return 1; }
apt-get() { printf 'unexpected apt-get\n' >> "$calls"; return 1; }
logger() { printf 'unexpected logger\n' >> "$calls"; return 1; }
readlink() { printf '/dev/ptp7\n'; }
systemctl() {
    printf 'systemctl %s\n' "$*" >> "$calls"
    case "$1" in
        show) if [[ $2 == chronyd.service ]]; then printf 'not-found\n'; else printf 'loaded\n'; fi;;
        is-enabled) printf 'enabled\n';;
        is-active) return 0;;
        restart)
            restart_count=$((restart_count + 1))
            if [[ $failure == restart && $restart_count == 1 ]]; then return 1; fi;;
    esac
}
chronyd() {
    printf 'chronyd %s\n' "$*" >> "$calls"
    [[ $failure != syntax ]]
}
chronyc() {
    printf 'chronyc %s\n' "$*" >> "$calls"
    if [[ $1 == waitsync ]]; then
        if [[ $failure == edit ]]; then printf '# independent edit\n' >> __CONFIG__; fi
        [[ $failure != sync ]]; return
    fi
    if [[ $1 == -c ]]; then
        if [[ $failure == selection ]]; then printf '^,*,time.organization.example,2,3,377,1,0,0,0\n'
        else printf '#,*,AZUR,2,3,377,1,0,0,0\n'; fi
    else printf 'Reference ID    : 415A5552 (AZUR)\nLeap status     : Normal\n'; fi
}
'''.replace("__CALLS__", shlex.quote(str(calls))).replace("__FAILURE__", shlex.quote(failure)).replace(
            "__CONFIG__", shlex.quote(str(config))).replace("__TIME_ONLY__", "true" if time_only else "false")
        if time_only:
            # 新moduleが実際に生成した全文を実行し、後段SSH/syslogへ進まないことを確認する。
            from tests.management.test_guest_deployment_payloads import PayloadExpression
            template = compile_bicep("infra/modules/management/management-host-time.bicep")
            command, = [item for item in template["resources"]
                        if item["type"] == "Microsoft.Compute/virtualMachines/runCommands"]
            prefix = PayloadExpression(command["properties"]["source"]["script"],
                                       template.get("variables", {}), "https://login.microsoftonline.com/").evaluate({}, 0)
        else:
            prefix = self.source.split("# 2.", 1)[0]
        substitutions = [
            ("/etc/systemd/timesyncd.conf.d/90-core-platform.conf", timesyncd),
            ("/etc/chrony/chrony.conf", config), ("/etc/chrony/conf.d", confdir),
            ("/etc/chrony/sources.d", sources), ("/run/chrony-dhcp", dhcp),
            ("/run/core-management-time", transaction),
            ("/etc/rsyslog.d", rsyslog), ("/etc/ssh/sshd_config.d", ssh),
        ]
        for actual, fixture in substitutions:
            prefix = prefix.replace(actual, str(fixture))
        prefix = prefix.replace("validate_host_clock /dev/ptp_hyperv /sys/class/ptp",
                                "validate_host_clock " + shlex.quote(str(device)) + " " + shlex.quote(str(sysroot)))
        result = subprocess.run(["bash", "-c", stubs + "\n" + prefix + "\nprintf 'time-phase-ok\\n'"],
                                capture_output=True, text=True, timeout=10, check=False,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return result, paths, original, calls.read_text()

    def test_time_phase_waits_for_phc_and_commits_only_after_sync(self):
        with tempfile.TemporaryDirectory(prefix="core-time-phase-") as folder:
            result, paths, _, calls = self.run_time_phase(Path(folder))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("time-phase-ok", result.stdout)
            self.assertIn("chronyd -p -f ", calls)
            self.assertIn("systemctl disable --now systemd-timesyncd.service", calls)
            self.assertIn("chronyc waitsync 30 0.1 0 2", calls)
            self.assertLess(calls.index("chronyd -p"), calls.index("systemctl restart chrony.service"))
            self.assertLess(calls.index("chronyc waitsync"), calls.index("chronyc -c sources"))
            self.assertFalse(paths[-1].exists())

    def test_actual_time_only_module_updates_phc_without_touching_ssh_or_syslog(self):
        for failure in ("", "sync"):
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory(prefix="core-time-only-") as folder:
                    root = Path(folder)
                    result, paths, _, calls = self.run_time_phase(root, failure=failure, time_only=True)
                    status = json.loads(result.stdout)
                    if failure:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(status["stage"], "host-time-synchronization")
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(status, {"status": "configured", "timeService": "chrony.service",
                            "timeSource": "azure-host-phc", "timeSynchronized": True, "timeOnly": True})
                        self.assertIn(self.HOST_LINE, self.active_lines(paths[0]))
                    self.assertNotIn("unexpected", calls)
                    self.assertNotIn("rsyslog", calls)
                    self.assertNotIn("systemctl reload ssh", calls)
                    self.assertEqual((root / "rsyslog/00-core-command-redaction.conf").read_text(),
                                     "synthetic existing rsyslog configuration\n")
                    self.assertEqual((root / "ssh/00-core-access.conf").read_text(),
                                     "synthetic existing SSH configuration\n")

    def test_time_phase_failures_restore_configuration_and_previous_services(self):
        for failure, stage in (("syntax", "host-time-configuration"), ("restart", "host-time-service"),
                               ("sync", "host-time-synchronization"), ("selection", "host-time-synchronization")):
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory(prefix="core-time-rollback-") as folder:
                    result, paths, original, calls = self.run_time_phase(Path(folder), failure)
                    self.assertNotEqual(result.returncode, 0)
                    status = json.loads(result.stdout)
                    self.assertEqual(status["stage"], stage)
                    self.assertEqual(status["code"], "LinuxAuditConfigurationFailed")
                    self.assertEqual(paths[0].read_text(), original)
                    self.assertFalse(paths[-1].exists())
                    if failure == "syntax":
                        self.assertNotIn("systemctl restart", calls)
                    else:
                        self.assertIn("systemctl enable systemd-timesyncd.service", calls)
                        self.assertIn("systemctl start systemd-timesyncd.service", calls)

    def test_time_phase_does_not_restore_or_remove_a_previous_transaction(self):
        with tempfile.TemporaryDirectory(prefix="core-time-previous-run-") as folder:
            result, paths, original, calls = self.run_time_phase(Path(folder), pending=True)
            self.assertNotEqual(result.returncode, 0)
            status = json.loads(result.stdout)
            self.assertEqual(status["stage"], "host-time-configuration")
            self.assertEqual(paths[0].read_text(), original)
            self.assertEqual((paths[-1] / "changes.json").read_text(), "synthetic previous transaction")
            self.assertNotIn("systemctl restart", calls)

    def test_time_phase_rejects_independent_edit_during_sync_and_preserves_it(self):
        with tempfile.TemporaryDirectory(prefix="core-time-sync-edit-") as folder:
            result, paths, _, calls = self.run_time_phase(Path(folder), failure="edit")
            self.assertNotEqual(result.returncode, 0)
            status = json.loads(result.stdout)
            self.assertEqual(status["stage"], "host-time-synchronization")
            self.assertEqual(status["code"], "TimeConfigurationRollbackFailed")
            self.assertIn("# independent edit\n", paths[0].read_text())
            self.assertTrue((paths[-1] / "changes.json").is_file())
            self.assertNotIn("systemctl start systemd-timesyncd.service", calls)

    def test_commit_checks_unchanged_configuration_and_new_include_files(self):
        for kind in ("unchanged-root", "new-include"):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory(prefix="core-time-commit-check-") as folder:
                    paths, _ = self.fixture(Path(folder))
                    config, confdir, _, _, _, transaction = paths
                    self.assertEqual(self.transaction("prepare", paths).returncode, 0)
                    self.assertEqual(self.transaction("commit", paths).returncode, 0)
                    self.assertEqual(self.transaction("prepare", paths).returncode, 0)
                    self.assertEqual(json.loads((transaction / "changes.json").read_text())["changes"], [])
                    if kind == "unchanged-root":
                        config.write_text(config.read_text() + "# independent edit\n")
                    else:
                        (confdir / "organization.conf").write_text("server time.organization.example iburst\n")
                    result = self.transaction("commit", paths)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue((transaction / "changes.json").is_file())


class LinuxManagementTemplateTests(unittest.TestCase):
    """管理VMがUbuntu・SSH公開鍵・Linux監視として配備されることを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'management': compile_bicep('infra/modules/management/management.bicep'),
            'monitoring': compile_bicep('infra/modules/monitoring/monitoring.bicep'),
            'management-audit': compile_bicep('infra/modules/management/management-audit.bicep'),
            'management-host-time': compile_bicep('infra/modules/management/management-host-time.bicep'),
        }

    @staticmethod
    def resources(template, resource_type):
        return [resource for resource in template["resources"] if resource["type"] == resource_type]

    def test_vm_requires_public_key_and_keeps_ssh_password_login_disabled(self):
        template = self.templates["management"]
        self.assertIn("sshPublicKey", template["parameters"])
        self.assertNotIn("adminPassword", template["parameters"])
        self.assertIn("initialSerialGeneration", template["parameters"])
        vm, = self.resources(template, "Microsoft.Compute/virtualMachines")
        supplied = {name: definition["defaultValue"]
                    for name, definition in template["parameters"].items() if "defaultValue" in definition}
        supplied.update({"adminUsername": "fixtureoperator", "sshPublicKey": "synthetic-public-key",
                         "__variables": {"computerName": "mgmt-synthetic-fixture"}})
        for generation in ("", "INITIAL_SERIAL_GENERATION_FIXTURE"):
            with self.subTest(initial_generation=bool(generation)):
                profile = ManagementProfileExpression.render(vm["properties"]["osProfile"],
                    {**supplied, "initialSerialGeneration": generation})
                self.assertNotIn("adminPassword", profile)
                self.assertEqual(profile["adminUsername"], supplied["adminUsername"])
                self.assertNotIn("windowsConfiguration", profile)
                linux = profile["linuxConfiguration"]
                self.assertTrue(linux["disablePasswordAuthentication"])
                self.assertTrue(linux["provisionVMAgent"])
                self.assertEqual(linux["ssh"]["publicKeys"], [{
                    "path": "/home/" + supplied["adminUsername"] + "/.ssh/authorized_keys",
                    "keyData": supplied["sshPublicKey"],
                }])
        self.assertEqual(vm["properties"]["storageProfile"]["osDisk"]["osType"], "Linux")

    def test_vm_uses_linux_entra_and_monitor_extensions_and_dcr(self):
        template = self.templates["management"]
        self.assertFalse(self.resources(template, "Microsoft.Authorization/roleAssignments"))
        extensions = self.resources(template, "Microsoft.Compute/virtualMachines/extensions")
        self.assertEqual({extension["properties"]["type"] for extension in extensions},
                         {"AADSSHLoginForLinux", "AzureMonitorLinuxAgent"})
        association, = self.resources(template, "Microsoft.Insights/dataCollectionRuleAssociations")
        self.assertIn("linuxDataCollectionRuleId", association["properties"]["dataCollectionRuleId"])

    def test_dcr_uses_linux_syslog_and_linux_counters_without_process_arguments(self):
        template = self.templates["monitoring"]
        rule, = [item for item in self.resources(template, "Microsoft.Insights/dataCollectionRules")
                  if item.get("kind") == "Linux"]
        self.assertEqual(rule["kind"], "Linux")
        sources = rule["properties"]["dataSources"]
        self.assertNotIn("windowsEventLogs", sources)
        self.assertEqual(set(sources["syslog"][0]["facilityNames"]), {"auth", "authpriv"})
        self.assertEqual(sources["performanceCounters"][0]["counterSpecifiers"], [
            "Processor(*)\\% Processor Time", "Memory(*)\\Available MBytes Memory", "Logical Disk(*)\\% Free Space",
        ])
        flow, = [flow for flow in rule["properties"]["dataFlows"] if "Microsoft-Syslog" in flow["streams"]]
        self.assertIn("redacted", flow["transformKql"])
        self.assertNotIn("Process(*)", json.dumps(sources))
        archive = AuditExpression("[variables('archive')]", template["variables"]).evaluate({})
        self.assertIn("Syslog", archive["exportTables"])

    def test_dcr_waits_for_its_workspace_tables_on_a_fresh_deployment(self):
        """初回は空のWorkspaceなので、収集規則より前に出力テーブルを作る。"""
        template = self.templates["monitoring"]
        rule, = [item for item in self.resources(template, "Microsoft.Insights/dataCollectionRules")
                  if item.get("kind") == "Linux"]
        tables, = self.resources(template, "Microsoft.OperationalInsights/workspaces/tables")
        # Bicepの行順ではAzureの作成順は決まらない。コンパイル済みARMの
        # 依存グラフが、テーブル配列全件の完了をDCRの開始条件にすることを確認する。
        self.assertIn(tables["copy"]["name"], rule.get("dependsOn", []),
                      "Linux DCR must wait for workspace table creation, not only the workspace")
        streams = {flow.get("outputStream", stream)
                   for flow in rule["properties"]["dataFlows"] for stream in flow["streams"]}
        self.assertEqual(streams, {"Microsoft-Syslog", "Microsoft-Perf"})

    def test_audit_uses_shell_and_requires_azure_host_time(self):
        template = self.templates["management-audit"]
        command, = self.resources(template, "Microsoft.Compute/virtualMachines/runCommands")
        self.assertIn("configure-linux-audit", command["name"])
        script = command["properties"]["source"]["script"]
        self.assertNotIn("ntpServers", template["parameters"])
        self.assertTrue(script.startswith("[replace("))
        guest, = [value for value in template["variables"].values()
                  if isinstance(value, str) and "LinuxAuditConfigurationFailed" in value]
        self.assertTrue(guest.startswith("#!/usr/bin/env bash\n"))
        payload = json.dumps(template)
        self.assertIn("time_configuration", payload)
        self.assertIn("validate_host_clock", payload)
        self.assertIn("verify_host_time", payload)
        self.assertIn("refclock PHC /dev/ptp_hyperv", payload)
        self.assertIn("chronyc waitsync 30 0.1 0 2", payload)
        self.assertIn("timeSynchronized", guest)
        self.assertNotIn("__CORE_NTP_SERVERS_BASE64__", guest)
        self.assertIn("90-core-platform.sources", payload)
        self.assertIn("chronyd -p -f /etc/chrony/chrony.conf", payload)
        self.assertIn("systemd-timesyncd", payload)
        self.assertNotIn("configure-audit.ps1", payload)
        self.assertIn("UnsupportedTimeService", payload)
        # rsyslog 8 で不可の native msg 代入を戻さず、PAM を除くコマンドイベントだけを stop する。
        redaction = guest.split("<<'RSYSLOG'\n", 1)[1].split("\nRSYSLOG", 1)[0]
        self.assertNotIn("set $msg", redaction)
        self.assertEqual(redaction.count("\n    stop\n"), 2)
        self.assertIn('not re_match($msg, "^[[:space:]]*pam_', redaction)
        self.assertIn("stage=syslog-configuration", guest)
        self.assertIn("stage=syslog-service", guest)

    def test_host_time_module_only_creates_its_fixed_managed_command(self):
        template = self.templates["management-host-time"]
        self.assertEqual({item["type"] for item in template["resources"]},
                         {"Microsoft.Compute/virtualMachines/runCommands"})
        command, = template["resources"]
        self.assertIn("configure-host-time", command["name"])
        properties = command["properties"]
        self.assertIs(properties["asyncExecution"], False)
        self.assertEqual(properties["timeoutInSeconds"], 300)
        self.assertIs(properties["treatFailureAsDeploymentFailure"], True)
        self.assertNotIn("parameters", properties)
        self.assertNotIn("protectedParameters", properties)
        self.assertEqual(set(template["parameters"]), {"vmName", "location", "tags"})


if __name__ == "__main__":
    unittest.main()

"""管理VMがUbuntu・SSH公開鍵・Linux監視として配備されることを検証する。

Bicepのコンパイル結果から、パスワード無効化・Entra SSH拡張・Linux AMA/DCR・NTP入力を確認する。実VMへのログイン成功は別の確認である。
"""
from bicep_helpers import compile_bicep

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]


class LinuxTimeConfigurationTests(unittest.TestCase):
    """時刻設定の関数だけを一時フォルダーで実行し、ホストのサービスには触れない。"""
    @classmethod
    def setUpClass(cls):
        source = (CORE / "guest/configure-audit.sh").read_text()
        cls.helpers = source.split("# BEGIN TIME CONFIG HELPERS\n", 1)[1].split("# END TIME CONFIG HELPERS", 1)[0]

    def run_helper(self, expression, *arguments):
        """生成関数に明示した fixture パスだけを渡す。systemctl は実行しない。"""
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + self.helpers + "\n" + expression,
                               "time-config-fixture", *map(str, arguments)],
                              capture_output=True, text=True, timeout=10, check=False)

    def test_selects_existing_chrony_or_timesyncd_without_requiring_both(self):
        for states, expected in [
            (("loaded", "loaded", "not-found"), "chrony.service"),
            (("not-found", "loaded", "not-found"), "chronyd.service"),
            (("not-found", "not-found", "loaded"), "systemd-timesyncd.service"),
        ]:
            with self.subTest(states=states):
                result = self.run_helper('select_time_service "$1" "$2" "$3"', *states)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_no_existing_time_service_is_not_reported_as_supported(self):
        result = self.run_helper('select_time_service "$1" "$2" "$3"', *["not-found"] * 3)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_chrony_preserves_phc_os_sources_and_includes_on_reapply(self):
        with tempfile.TemporaryDirectory(prefix="core-chrony-fixture-") as folder:
            root = Path(folder)
            directory = root / "sources.d"
            directory.mkdir()
            other = directory / "organization.sources"
            other.write_text("pool existing.organization.example iburst\n")
            config = root / "chrony.conf"
            original = (f"confdir {root / 'conf.d'}\nsourcedir /run/chrony-dhcp\n"
                        f"sourcedir {directory}\ndriftfile /var/lib/chrony/chrony.drift\n"
                        "refclock PHC /dev/ptp_hyperv poll 3 dpoll -2 offset 0\n")
            config.write_text(original)
            managed = directory / "90-core-platform.sources"
            for servers in ("ntp.ubuntu.com ntp.organization.example", "ntp.organization.example"):
                result = self.run_helper('write_chrony_sources "$1" "$2" "$3"', servers, config, directory)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(config.read_text(), original)
                self.assertEqual(other.read_text(), "pool existing.organization.example iburst\n")
                expected = [f"server {name} iburst" for name in servers.split()]
                self.assertEqual([line for line in managed.read_text().splitlines() if not line.startswith("#")], expected)
                if os.name != "nt":
                    self.assertEqual(stat.S_IMODE(managed.stat().st_mode), 0o644)

    def test_chrony_requires_root_config_to_load_the_managed_directory(self):
        with tempfile.TemporaryDirectory(prefix="core-chrony-unloaded-") as folder:
            root = Path(folder)
            config = root / "chrony.conf"
            config.write_text("refclock PHC /dev/ptp_hyperv poll 3\n")
            directory = root / "sources.d"
            result = self.run_helper('write_chrony_sources "$1" "$2" "$3"', "ntp.ubuntu.com", config, directory)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must load", result.stderr)
            self.assertFalse(directory.exists())

    def test_timesyncd_keeps_its_existing_drop_in_format(self):
        with tempfile.TemporaryDirectory(prefix="core-timesyncd-fixture-") as folder:
            target = Path(folder) / "timesyncd.conf.d/90-core-platform.conf"
            result = self.run_helper('write_timesyncd_config "$1" "$2"', "ntp.organization.example", target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(), "[Time]\nNTP=ntp.organization.example\nFallbackNTP=\n")


class LinuxManagementTemplateTests(unittest.TestCase):
    """管理VMがUbuntu・SSH公開鍵・Linux監視として配備されることを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'management': compile_bicep('infra/modules/management.bicep'),
            'monitoring': compile_bicep('infra/modules/monitoring.bicep'),
            'management-audit': compile_bicep('infra/modules/management-audit.bicep'),
        }

    @staticmethod
    def resources(template, resource_type):
        return [resource for resource in template["resources"] if resource["type"] == resource_type]

    def test_vm_requires_public_key_and_disables_password_login(self):
        template = self.templates["management"]
        self.assertIn("sshPublicKey", template["parameters"])
        self.assertNotIn("adminPassword", template["parameters"])
        vm, = self.resources(template, "Microsoft.Compute/virtualMachines")
        profile = vm["properties"]["osProfile"]
        self.assertNotIn("adminPassword", profile)
        self.assertNotIn("windowsConfiguration", profile)
        linux = profile["linuxConfiguration"]
        self.assertTrue(linux["disablePasswordAuthentication"])
        self.assertEqual(len(linux["ssh"]["publicKeys"]), 1)
        self.assertIn("sshPublicKey", linux["ssh"]["publicKeys"][0]["keyData"])
        self.assertEqual(vm["properties"]["storageProfile"]["osDisk"]["osType"], "Linux")

    def test_vm_uses_linux_entra_and_monitor_extensions_and_dcr(self):
        template = self.templates["management"]
        extensions = self.resources(template, "Microsoft.Compute/virtualMachines/extensions")
        self.assertEqual({extension["properties"]["type"] for extension in extensions},
                         {"AADSSHLoginForLinux", "AzureMonitorLinuxAgent"})
        association, = self.resources(template, "Microsoft.Insights/dataCollectionRuleAssociations")
        self.assertIn("linuxDataCollectionRuleId", association["properties"]["dataCollectionRuleId"])

    def test_dcr_uses_linux_syslog_and_linux_counters_without_process_arguments(self):
        template = self.templates["monitoring"]
        rule, = self.resources(template, "Microsoft.Insights/dataCollectionRules")
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
        self.assertIn("Syslog", template["variables"]["exportTables"])

    def test_dcr_waits_for_its_workspace_tables_on_a_fresh_deployment(self):
        """初回は空のWorkspaceなので、収集規則より前に出力テーブルを作る。"""
        template = self.templates["monitoring"]
        rule, = self.resources(template, "Microsoft.Insights/dataCollectionRules")
        tables, = self.resources(template, "Microsoft.OperationalInsights/workspaces/tables")
        # Bicepの行順ではAzureの作成順は決まらない。コンパイル済みARMの
        # 依存グラフが、テーブル配列全件の完了をDCRの開始条件にすることを確認する。
        self.assertIn(tables["copy"]["name"], rule.get("dependsOn", []),
                      "Linux DCR must wait for workspace table creation, not only the workspace")
        streams = {flow.get("outputStream", stream)
                   for flow in rule["properties"]["dataFlows"] for stream in flow["streams"]}
        self.assertEqual(streams, {"Microsoft-Syslog", "Microsoft-Perf"})

    def test_audit_uses_shell_and_encodes_ntp_as_data(self):
        template = self.templates["management-audit"]
        command, = self.resources(template, "Microsoft.Compute/virtualMachines/runCommands")
        self.assertIn("configure-linux-audit", command["name"])
        script = command["properties"]["source"]["script"]
        self.assertIn("base64(string(parameters('ntpServers')))", script)
        self.assertTrue(script.startswith("[replace("))
        guest, = [value for value in template["variables"].values()
                  if isinstance(value, str) and "LinuxAuditConfigurationFailed" in value]
        self.assertTrue(guest.startswith("#!/usr/bin/env bash\n"))
        payload = json.dumps(template)
        self.assertIn("write_chrony_sources", payload)
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


if __name__ == "__main__":
    unittest.main()

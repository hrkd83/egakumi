"""Compile actual deployment payloads with Windows line endings and cloud URLs.

Only local compilation, shell syntax checks and a harmless shebang probe run.
No guest setup, cloud deployment, credential handling or host service changes run.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from arm_helpers import ArmExpression
from bicep_helpers import bicep_executable


CORE = Path(__file__).resolve().parents[1]


class PayloadExpression(ArmExpression):
    def __init__(self, expression, variables, login_endpoint):
        super().__init__(expression)
        self.variables = variables
        self.login_endpoint = login_endpoint

    def resolve(self, value, parameters):
        if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
            return PayloadExpression(value, self.variables, self.login_endpoint).evaluate(parameters, 0)
        if isinstance(value, list):
            return [self.resolve(item, parameters) for item in value]
        if isinstance(value, dict):
            return {key: self.resolve(item, parameters) for key, item in value.items()}
        return value

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        if tree[0] == "call":
            _, name, arguments = tree
            if name in {"variables", "replace", "base64", "string", "split", "environment"}:
                values = [self.evaluate(parameters, index, item, bindings) for item in arguments]
                if name == "variables":
                    return self.resolve(self.variables[values[0]], parameters)
                if name == "replace":
                    return values[0].replace(values[1], values[2])
                if name == "base64":
                    return base64.b64encode(values[0].encode()).decode()
                if name == "string":
                    return json.dumps(values[0], separators=(",", ":"))
                if name == "split":
                    return values[0].split(values[1])
                if name == "environment":
                    return {"authentication": {"loginEndpoint": self.login_endpoint}}
        return super().evaluate(parameters, index, tree, bindings)


class GuestDeploymentPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bicep = bicep_executable()
        cls.templates = {}
        with tempfile.TemporaryDirectory(prefix="core-guest-payloads-") as folder:
            root = Path(folder)
            for source in (CORE / "infra/modules").glob("*.bicep"):
                target = root / "infra/modules" / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            (root / "infra/nsg-rule-priorities.json").write_bytes((CORE / "infra/nsg-rule-priorities.json").read_bytes())
            (root / "infra/guest").mkdir()
            for newline in (b"\n", b"\r\n"):
                for name in ("configure-audit.sh", "certificate-bootstrap.sh", "certificate.ps1"):
                    content = (CORE / "infra/guest" / name).read_bytes().replace(b"\r\n", b"\n")
                    (root / "infra/guest" / name).write_bytes(content.replace(b"\n", newline))
                for name in ("management-audit", "management-host-time", "certificate", "portal-network"):
                    output = root / (name + ".json")
                    result = subprocess.run(
                        [bicep, "build", str(root / "infra/modules" / (name + ".bicep")), "--outfile", str(output)],
                        capture_output=True, text=True, check=False, timeout=60,
                        env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(root / "dotnet")},
                    )
                    if result.returncode:
                        raise AssertionError(result.stderr)
                    cls.templates[newline, name] = json.loads(output.read_text())

    @staticmethod
    def payload(template, expression, login_endpoint="https://login.microsoftonline.com/"):
        return PayloadExpression(expression, template.get("variables", {}), login_endpoint).evaluate(
            {"managementPrefix": "<MANAGEMENT_SUBNET_PREFIX>",
             "acaPrefix": "<ACA_SUBNET_PREFIX>", "agwPrefix": "<APPGW_SUBNET_PREFIX>"}, 0)

    def test_actual_run_command_scripts_are_lf_and_have_a_working_linux_shebang(self):
        for newline in (b"\n", b"\r\n"):
            for name in ("management-audit", "management-host-time", "certificate"):
                with self.subTest(source_newline=newline, module=name):
                    template = self.templates[newline, name]
                    command, = [item for item in template["resources"]
                                if item["type"] == "Microsoft.Compute/virtualMachines/runCommands"]
                    script = self.payload(template, command["properties"]["source"]["script"])
                    self.assertNotIn("\r", script)
                    self.assertIn(script.partition("\n")[0], ("#!/usr/bin/env bash", "#!/bin/bash"))
                    syntax = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
                    self.assertEqual(syntax.returncode, 0, syntax.stderr)
                    if os.name != "nt":
                        # Preserve the emitted shebang bytes; execute no guest setup commands.
                        with tempfile.TemporaryDirectory(prefix="core-shebang-") as folder:
                            probe = Path(folder) / "probe"
                            probe.write_bytes((script[:script.index("\n") + 1] + "printf 'ready\\n'\n").encode())
                            probe.chmod(0o700)
                            result = subprocess.run([str(probe)], capture_output=True, text=True, timeout=5)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(result.stdout, "ready\n")
                    if name == "certificate":
                        worker = (CORE / "infra/guest/certificate.ps1").read_bytes().replace(b"\r\n", b"\n")
                        self.assertIn(base64.b64encode(worker).decode(), script)

    def test_host_time_only_payload_shares_the_audit_script_with_one_fixed_mode_change(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(source_newline=newline):
                scripts = {}
                for name in ("management-audit", "management-host-time"):
                    template = self.templates[newline, name]
                    command, = [item for item in template["resources"]
                                if item["type"] == "Microsoft.Compute/virtualMachines/runCommands"]
                    scripts[name] = self.payload(template, command["properties"]["source"]["script"])
                mode = "readonly core_host_time_only=false"
                self.assertEqual(scripts["management-audit"].count(mode), 1)
                self.assertEqual(scripts["management-host-time"], scripts["management-audit"].replace(
                    mode, "readonly core_host_time_only=true"))
                self.assertEqual(set(self.templates[newline, "management-host-time"]["parameters"]),
                                 {"vmName", "location", "tags"})

    def test_portal_firewall_uses_hosts_without_url_separators_in_each_cloud(self):
        template = self.templates[b"\n", "portal-network"]
        resources = template["resources"]
        resources = resources.values() if isinstance(resources, dict) else resources
        group, = [item for item in resources
                  if item["type"] == "Microsoft.Network/firewallPolicies/ruleCollectionGroups"]
        for host in ("login.microsoftonline.com", "login.microsoftonline.us", "login.chinacloudapi.cn"):
            for suffix in ("", "/"):
                with self.subTest(host=host, suffix=suffix):
                    endpoint = "https://" + host + suffix
                    reader = PayloadExpression("[true()]", template.get("variables", {}), endpoint)
                    parameters = {"managementPrefix": "<MANAGEMENT_SUBNET_PREFIX>",
                                  "acaPrefix": "<ACA_SUBNET_PREFIX>", "brokerPrefix": "<BROKER_SUBNET_PREFIX>",
                                  "agwPrefix": "<APPGW_SUBNET_PREFIX>"}
                    properties = reader.resolve(group["properties"], parameters)
                    values = [fqdn for collection in properties["ruleCollections"]
                              for rule in collection["rules"] for fqdn in rule.get("targetFqdns", [])]
                    self.assertIn(host, values)
                    self.assertIn("*." + host, values)
                    for value in values:
                        self.assertRegex(value, r"^(?:\*\.)?[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


if __name__ == "__main__":
    unittest.main()

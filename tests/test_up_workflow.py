"""Exercise the foundation-to-application hook boundary without starting Azure work."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import unittest
from unittest import mock

from lifecycle_fixtures import LifecycleFixture, lifecycle
import deploy_applications as applications


ROOT = Path(__file__).resolve().parents[1]


def hooks():
    """Read the project's simple hook mappings without an extra YAML dependency."""
    result, active, inside = {}, None, False
    for line in (ROOT / "azure.yaml").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line == "hooks:":
            inside = True
            continue
        if not inside:
            continue
        if not line.startswith(" "):
            break
        header = re.fullmatch(r"  ([A-Za-z][A-Za-z0-9_-]*):", line)
        if header:
            active = header[1]
            if active in result:
                raise AssertionError("Duplicate hook definitions")
            result[active] = {}
            continue
        field = re.fullmatch(r"    ([A-Za-z][A-Za-z0-9_-]*): (.+)", line)
        if not field or active is None:
            raise AssertionError("Unsupported hook mapping; update the reader for the new YAML structure")
        if field[1] in result[active]:
            raise AssertionError("Duplicate hook setting")
        result[active][field[1]] = field[2]
    return result


def hook_arguments(name, script):
    command = shlex.split(hooks()[name]["run"])
    if len(command) < 2 or Path(command[1]).name != script:
        raise AssertionError("The hook must invoke its supported Python entry point")
    return command[2:]


class UpWorkflowTests(LifecycleFixture, unittest.TestCase):
    def test_application_completion_hook_has_terminal_access_and_propagates_failure(self):
        hook = hooks()["postup"]
        self.assertEqual(hook["interactive"], "true")
        self.assertEqual(hook["shell"], "sh")
        self.assertEqual(hook.get("continueOnError", "false"), "false")
        self.assertIn("--execute", hook_arguments("postup", "deploy_applications.py"))
        self.assertEqual(hook_arguments("postprovision", "lifecycle.py"), ["postprovision"])

    def test_explicit_false_stops_up_before_local_preparation_and_any_azure_access(self):
        config = {**self.config, "publishApis": False}
        with mock.patch.object(lifecycle, "load_configuration", return_value=config), \
             mock.patch.object(lifecycle, "local_preflight") as local, \
             mock.patch.object(lifecycle, "azure_preflight") as azure, \
             mock.patch.object(lifecycle, "run") as cli, \
             mock.patch.object(lifecycle, "ArmTransport") as arm, \
             contextlib.redirect_stderr(io.StringIO()) as messages:
            self.assertEqual(lifecycle.main(["preup", "--config", "params/selected.local.json"]), 1)
        local.assert_not_called()
        azure.assert_not_called()
        cli.assert_not_called()
        arm.assert_not_called()
        self.assertIn("publishApis", messages.getvalue())
        self.assertFalse(lifecycle.PARAMETERS.exists())

    def test_postprovision_stays_foundation_only_then_postup_uses_its_saved_state_and_selected_params(self):
        selected = self.root / "company inputs" / "selected.local.json"
        selected.parent.mkdir()
        selected.write_text("{}", encoding="utf-8")
        outputs = self.outputs()
        outputs["endpoints"] = {"public": "https://gateway.example.invalid"}
        children = []

        def foundation_cli(arguments, **kwargs):
            if arguments[:3] == ["azd", "env", "get-values"]:
                return {"CORE_DEPLOYMENT_NAME": "fixture-completed-core"}
            if arguments[:4] == ["az", "deployment", "sub", "show"]:
                return {"properties": {"outputs": {key: {"value": value} for key, value in outputs.items()}}}
            raise AssertionError("Unexpected CLI command")

        with mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
             mock.patch.object(lifecycle, "run", side_effect=foundation_cli), \
             mock.patch.object(lifecycle, "ArmTransport"), \
             mock.patch.object(lifecycle, "configure_grpc_endpoint", return_value={"enabled": False}), \
             mock.patch.object(lifecycle, "configure_public_dns", return_value={"status": "not-required"}), \
             mock.patch.object(applications.subprocess, "run", side_effect=AssertionError("Application processes must wait for postup")), \
             contextlib.redirect_stdout(io.StringIO()):
            args = hook_arguments("postprovision", "lifecycle.py")
            self.assertEqual(lifecycle.main([*args, "--config", str(selected)]), 0)
        saved = json.loads(lifecycle.STATE.read_text())
        self.assertEqual(saved["outputs"], outputs)
        self.assertEqual(saved["resourceStem"], self.config["resourceStem"])

        def application_cli(command, **kwargs):
            self.assertEqual(json.loads(lifecycle.STATE.read_text()), saved)
            self.assertEqual(kwargs["cwd"], self.root)
            self.assertFalse(kwargs["shell"])
            self.assertEqual(Path(command[command.index("--config") + 1]), selected.resolve())
            self.assertEqual(Path(command[command.index("--state-file") + 1]), lifecycle.STATE.resolve())
            self.assertIn("--execute", command)
            children.append(Path(command[1]).name)
            return mock.Mock(returncode=0)

        with mock.patch.object(applications, "REPOSITORY", self.root), \
             mock.patch.object(applications, "load_configuration", return_value=self.config), \
             mock.patch.object(applications.subprocess, "run", side_effect=application_cli), \
             mock.patch.dict(os.environ, {"WORKFORCE_CONFIG": str(selected)}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(applications.main(hook_arguments("postup", "deploy_applications.py")), 0)
        self.assertEqual(children, ["deploy_gateway.py", "deploy_portal.py"])
        self.assertEqual(json.loads(lifecycle.STATE.read_text()), saved)
        self.assertEqual(selected.read_text(), "{}")


if __name__ == "__main__":
    unittest.main()

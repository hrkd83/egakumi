"""Validate the shared Core workflow and its handoff without starting Azure work."""
import contextlib
import io
import json
from pathlib import Path
import re
import shlex
import unittest
from unittest import mock

from lifecycle_fixtures import LifecycleFixture, lifecycle


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
    def test_up_uses_only_the_shared_core_provision_workflow(self):
        project = (ROOT / "azure.yaml").read_text(encoding="utf-8")
        workflows = project.split("workflows:\n", 1)[1].split("hooks:\n", 1)[0]
        self.assertEqual(workflows.split(), ["up:", "-", "azd:", "provision"])
        self.assertEqual(set(hooks()), {"preprovision", "postprovision", "predown", "postdown"})
        self.assertEqual(hooks()["preprovision"]["interactive"], "true")
        self.assertEqual(hook_arguments("preprovision", "lifecycle.py"), ["preprovision"])
        self.assertEqual(hook_arguments("postprovision", "lifecycle.py"), ["postprovision"])

    def test_core_completion_saves_state_for_individual_deployments(self):
        selected = self.paths.root / "company inputs" / "selected.local.json"
        lifecycle.private_parent(selected, self.root)
        selected.write_text("{}", encoding="utf-8")
        outputs = self.outputs()
        outputs["endpoints"] = {"public": "https://gateway.example.invalid"}

        response = self.core_completion_response(outputs)

        def foundation_cli(arguments, **kwargs):
            if arguments[:3] == ["azd", "env", "get-values"]:
                return {"CORE_DEPLOYMENT_NAME": "fixture-completed-core"}
            if arguments[:4] == ["az", "deployment", "sub", "show"]:
                return response
            raise AssertionError("Unexpected CLI command")

        with mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
             mock.patch.object(lifecycle, "run", side_effect=foundation_cli), \
             mock.patch.object(lifecycle, "ArmTransport"), \
             mock.patch.object(lifecycle, "verify_apim_endpoint", return_value={"enabled": True}) as verified, \
             mock.patch.object(lifecycle, "configure_public_dns", return_value={"status": "not-required"}), \
             contextlib.redirect_stdout(io.StringIO()) as result:
            args = hook_arguments("postprovision", "lifecycle.py")
            self.assertEqual(lifecycle.main([*args, "--config", str(selected)]), 0)
        verified.assert_called_once()
        saved = json.loads((self.paths.state / "latest.json").read_text())
        self.assertEqual(saved["outputs"], outputs)
        self.assertEqual(saved["resourceStem"], self.config["resourceStem"])

        self.assertEqual(json.loads(result.getvalue().splitlines()[-1])["apiPublicationStatus"], "managed-separately")
        self.assertEqual(selected.read_text(), "{}")


if __name__ == "__main__":
    unittest.main()

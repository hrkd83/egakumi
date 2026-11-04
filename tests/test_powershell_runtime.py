"""公開手順のPowerShellだけを選び、旧repo内ツールを自動流用しない。"""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import powershell_runtime as runtime


class PowerShellRuntimeTests(unittest.TestCase):
    def test_missing_installed_runtime_never_searches_old_repo_tools(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(runtime, "_executable", return_value=None) as resolve:
            with self.assertRaises(RuntimeError):
                runtime.locate_powershell()
        self.assertEqual(resolve.call_args_list, [mock.call("pwsh")])

    def test_explicit_executable_has_priority_and_is_not_a_shell_command(self):
        with mock.patch.dict(os.environ, {"WORKFORCE_PWSH": "/fixture/pwsh"}, clear=True), \
             mock.patch.object(runtime, "_executable", return_value="/fixture/pwsh") as resolve:
            self.assertEqual(runtime.locate_powershell(), "/fixture/pwsh")
        resolve.assert_called_once_with("/fixture/pwsh")

    def test_standard_module_search_is_left_to_powershell_when_not_overridden(self):
        with mock.patch.dict(os.environ, {"SYNTHETIC_SETTING": "value"}, clear=True):
            environment = runtime.powershell_environment("/fixture/pwsh")
            self.assertNotIn("PSModulePath", environment)
            self.assertEqual(environment, dict(os.environ))

    def test_explicit_module_path_removes_relative_entries_without_mutating_caller(self):
        value = os.pathsep.join(("/fixture/modules", "", "relative", "/fixture/modules"))
        with mock.patch.dict(os.environ, {"PSModulePath": value}, clear=True):
            environment = runtime.powershell_environment("/fixture/pwsh")
            self.assertEqual(os.environ["PSModulePath"], value)
        self.assertEqual(environment["PSModulePath"].split(os.pathsep), ["/fixture/Modules", "/fixture/modules"])


if __name__ == "__main__":
    unittest.main()

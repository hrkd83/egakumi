"""Compile local infrastructure once per source and isolate each test's template."""
from copy import deepcopy
from functools import cache
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def bicep_executable():
    configured = os.environ.get("BICEP_CLI")
    if configured:
        executable = shutil.which(configured)
        if not executable:
            raise AssertionError("BICEP_CLI does not identify an executable compiler")
        return executable
    executable = shutil.which("bicep")
    if not executable:
        candidate = Path.home() / ".azure/bin/bicep"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            executable = str(candidate)
    if not executable:
        raise unittest.SkipTest("A local Bicep compiler is required")
    return executable


@cache
def _compiled_template(source, executable):
    # All modules are local. --no-restore keeps these tests offline.
    with tempfile.TemporaryDirectory(prefix="core-bicep-test-") as directory:
        result = subprocess.run(
            [executable, "build", str(source), "--stdout", "--no-restore"],
            env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": directory},
            capture_output=True, text=True, timeout=60, check=False,
        )
    if result.returncode:
        raise AssertionError(f"Bicep compilation failed for {source}:\n{result.stderr}")
    return json.loads(result.stdout)


def compile_bicep(relative):
    """Return an independently mutable template; cache only within this test run."""
    return deepcopy(_compiled_template((ROOT / relative).resolve(), bicep_executable()))

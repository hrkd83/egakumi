"""Resolve the PowerShell executable used for offline fixtures."""

import os

from scripts.deployment.helpers.powershell_runtime import locate_powershell


try:
    PWSH = locate_powershell()
except RuntimeError:
    if "WORKFORCE_PWSH" in os.environ:
        raise
    PWSH = None

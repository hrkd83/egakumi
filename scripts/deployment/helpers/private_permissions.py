"""Owner-only runtime storage checks for POSIX modes and Windows native DACLs."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import stat
import subprocess

from .powershell_runtime import locate_powershell, powershell_environment


class PrivatePermissionsError(ValueError):
    """A fixed storage error without external paths, identities or content."""


_REPARSE_POINT = 0x400

# The child process receives only a path and an ACL operation. Passwords and
# credential files are never read by PowerShell or passed in its arguments.
_WINDOWS_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    $target = $env:EGAKUMI_SERIAL_ACL_TARGET
    $item = Get-Item -LiteralPath $target -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { exit 2 }
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    if ($env:EGAKUMI_SERIAL_ACL_OPERATION -eq 'protect-new') {
        if ($item.PSIsContainer) {
            $acl = Get-Acl -LiteralPath $target
            $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
        } else {
            $acl = Get-Acl -LiteralPath $target
            $inherit = [Security.AccessControl.InheritanceFlags]::None
        }
        # Preserve Owner. Generated SSH files may lack WRITE_OWNER even when
        # the current user already owns them. Change only the Access section.
        if (-not $acl.GetOwner([Security.Principal.SecurityIdentifier]).Equals($user)) { exit 3 }
        foreach ($old in $acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier])) {
            [void]$acl.RemoveAccessRuleSpecific($old)
        }
        $acl.SetAccessRuleProtection($true, $false)
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $user, [Security.AccessControl.FileSystemRights]::FullControl,
            $inherit, [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
        if ($null -ne ('System.IO.FileSystemAclExtensions' -as [type])) {
            [IO.FileSystemAclExtensions]::SetAccessControl($item, $acl)
        } else {
            $item.SetAccessControl($acl)
        }
    } elseif ($env:EGAKUMI_SERIAL_ACL_OPERATION -ne 'inspect') { exit 2 }
    $actual = Get-Acl -LiteralPath $target
    $rules = $actual.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])
    $safe = $actual.GetOwner([Security.Principal.SecurityIdentifier]).Equals($user)
    if ($item.PSIsContainer -and $env:EGAKUMI_SERIAL_ACL_OPERATION -eq 'inspect') {
        # Existing local directories may retain operating-system management
        # principals. No other user's Allow entry is accepted or modified.
        $approved = @($user.Value, 'S-1-5-18', 'S-1-5-32-544')
        $ownerHasControl = $false
        foreach ($rule in $rules) {
            if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
                $approved -notcontains $rule.IdentityReference.Value) { $safe = $false }
            if ($rule.IdentityReference.Equals($user) -and
                ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq
                    [Security.AccessControl.FileSystemRights]::FullControl) { $ownerHasControl = $true }
        }
        $safe = $safe -and $ownerHasControl
    } else {
        $safe = $safe -and $actual.AreAccessRulesProtected -and $rules.Count -eq 1
    }
    if ($safe -and -not ($item.PSIsContainer -and $env:EGAKUMI_SERIAL_ACL_OPERATION -eq 'inspect')) {
        $rule = $rules[0]
        $safe = $rule.IdentityReference.Equals($user) -and -not $rule.IsInherited -and
            $rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq
                [Security.AccessControl.FileSystemRights]::FullControl
    }
    if (-not $safe) { exit 3 }
    '{"ownerOnly":true}'
} catch { exit 2 }
"""


def _windows_owner_acl(path: Path, *, protect_new: bool = False) -> None:
    """Windowsの実DACLを検査し、新規の空ファイル/フォルダーだけを保護する。"""
    try:
        executable = locate_powershell()
        environment = powershell_environment(executable)
        environment.update({
            "EGAKUMI_SERIAL_ACL_TARGET": str(path),
            "EGAKUMI_SERIAL_ACL_OPERATION": "protect-new" if protect_new else "inspect",
            "AZURE_LOGGING_ENABLE_LOG_FILE": "false",
        })
        encoded = base64.b64encode(_WINDOWS_ACL_SCRIPT.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=20, check=False, env=environment,
        )
        if result.returncode == 0 and json.loads(result.stdout) == {"ownerOnly": True}:
            return
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError):
        pass
    raise PrivatePermissionsError("Private runtime storage requires owner-only Windows ACLs") from None


def regular_path(path: Path, *, directory: bool = False) -> os.stat_result:
    path = Path(path)
    try:
        info = path.lstat()
    except OSError:
        raise PrivatePermissionsError("Private runtime storage could not be inspected") from None
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT):
        raise PrivatePermissionsError("Private runtime storage must not use links or reparse points")
    return info


def check_private_permissions(path: Path, *, directory: bool = False) -> None:
    info = regular_path(path, directory=directory)
    if os.name == "nt":
        _windows_owner_acl(path)
    elif info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise PrivatePermissionsError("Private runtime storage requires owner-only permissions")


def protect_new_path(path: Path, *, directory: bool = False) -> None:
    """Protect a newly created empty node before storing any runtime data in it."""
    path = Path(path)
    info = regular_path(path, directory=directory)
    if (directory and next(path.iterdir(), None) is not None) or (not directory and info.st_size != 0):
        raise PrivatePermissionsError("Only newly created empty runtime paths may be protected")
    if os.name == "nt":
        _windows_owner_acl(path, protect_new=True)
    else:
        os.chmod(path, 0o700 if directory else 0o600, follow_symlinks=False)
    check_private_permissions(path, directory=directory)

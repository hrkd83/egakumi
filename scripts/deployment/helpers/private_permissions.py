"""Owner-only runtime storage checks for POSIX modes and Windows native DACLs."""
from __future__ import annotations

import atexit
import base64
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import threading
import time

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


_WINDOWS_ACL_WORKER_SCRIPT = "\n$checkPrivateAcl = {\n" + _WINDOWS_ACL_SCRIPT + r"""
}
$ErrorActionPreference = 'Stop'
$previousRequestId = 0
while (($line = [Console]::In.ReadLine()) -ne $null) {
    try {
        $request = ConvertFrom-Json -InputObject $line -AsHashtable
        if ($request -isnot [System.Collections.IDictionary] -or $request.Count -ne 3) { exit 2 }
        foreach ($key in $request.Keys) {
            if ($key -notin @('requestId', 'path', 'operation')) { exit 2 }
        }
        if (($request.requestId -isnot [long] -and $request.requestId -isnot [int]) -or
            $request.requestId -le $previousRequestId -or
            $request.path -isnot [string] -or [string]::IsNullOrEmpty($request.path) -or
            $request.operation -notin @('inspect', 'protect-new')) { exit 2 }
        $previousRequestId = $request.requestId
        $env:EGAKUMI_SERIAL_ACL_TARGET = $request.path
        $env:EGAKUMI_SERIAL_ACL_OPERATION = $request.operation
        # Execute the same native checker on every request; no result is cached.
        $nativeReply = @(& $checkPrivateAcl)
        if ($nativeReply.Count -ne 1 -or $nativeReply[0] -cne '{"ownerOnly":true}') { exit 2 }
        $reply = @{ requestId = $request.requestId; ownerOnly = $true } | ConvertTo-Json -Compress
        [Console]::Out.WriteLine($reply)
        [Console]::Out.Flush()
        Remove-Item -LiteralPath Env:EGAKUMI_SERIAL_ACL_TARGET, Env:EGAKUMI_SERIAL_ACL_OPERATION
    } catch { exit 2 }
}
"""

_ACL_TIMEOUT = 20
_ACL_RESPONSE_LIMIT = 256


def _stop_acl_process(process) -> bool:
    """Stop a native worker with bounded waits before permitting a replacement."""
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        return process.poll() is not None
    except (OSError, subprocess.SubprocessError):
        return False


class _WindowsACLWorker:
    """One serialized native checker; each call still reads the actual ACL."""

    def __init__(self):
        self.pid = os.getpid()
        self._lock = threading.Lock()
        self._process = None
        self._context = None
        self._requests = None
        self._responses = None
        self._io_thread = None
        self._unstopped = None
        self._pending_launch = None
        self._request_id = 0

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Native ACL check timed out")
        return remaining

    def _launch(self, executable, environment, deadline):
        """Bound even startup; a late-created cancelled child is terminated."""
        ready = queue.Queue(maxsize=1)
        guard = threading.Lock()
        launch = {"cancelled": False, "process": None, "complete": threading.Event()}
        self._pending_launch = launch
        encoded = base64.b64encode(_WINDOWS_ACL_WORKER_SCRIPT.encode("utf-16-le")).decode("ascii")

        def start():
            try:
                process = subprocess.Popen(
                    [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="strict", bufsize=1, env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except Exception:
                ready.put(None)
                launch["complete"].set()
                return
            with guard:
                launch["process"] = process
                cancelled = launch["cancelled"]
                if not cancelled:
                    ready.put(process)
            if cancelled:
                _stop_acl_process(process)
            launch["complete"].set()

        threading.Thread(target=start, daemon=True, name="private-acl-start").start()
        try:
            process = ready.get(timeout=_WindowsACLWorker._remaining(deadline))
            if process is None:
                raise OSError("Native ACL worker could not be started")
            self._process = process
            self._pending_launch = None
            return process
        except BaseException:
            with guard:
                launch["cancelled"] = True
                process = launch["process"]
            if process is not None:
                _stop_acl_process(process)
            raise

    def _start(self, executable, environment, deadline):
        process = self._launch(executable, environment, deadline)
        self._process = process
        self._requests = queue.Queue(maxsize=1)
        self._responses = queue.Queue(maxsize=1)
        requests, responses = self._requests, self._responses

        def exchange():
            while True:
                packet = requests.get()
                if packet is None:
                    return
                try:
                    process.stdin.write(packet + "\n")
                    process.stdin.flush()
                    reply = process.stdout.readline(_ACL_RESPONSE_LIMIT + 1)
                    if not reply or len(reply) > _ACL_RESPONSE_LIMIT or not reply.endswith("\n"):
                        raise ValueError("Invalid native ACL reply")
                    responses.put_nowait(reply)
                except Exception:
                    try:
                        responses.put_nowait(None)
                    except queue.Full:
                        pass
                    return

        self._io_thread = threading.Thread(target=exchange, daemon=True, name="private-acl-io")
        self._io_thread.start()

    def _discard(self):
        process, io_thread, requests = self._process, self._io_thread, self._requests
        self._process = self._context = self._requests = self._responses = self._io_thread = None
        if process is None:
            return
        if not _stop_acl_process(process):
            self._unstopped = process
            return
        if requests is not None:
            try:
                requests.put_nowait(None)
            except queue.Full:
                pass
        if io_thread is not None and io_thread.ident is not None:
            io_thread.join(timeout=1)
        # Closing buffered streams while an I/O thread is blocked can itself
        # block. Close only after the child and that thread have both stopped.
        if io_thread is None or not io_thread.is_alive():
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def check(self, path: Path, *, protect_new: bool = False):
        deadline = time.monotonic() + _ACL_TIMEOUT
        if self.pid != os.getpid() or not self._lock.acquire(timeout=self._remaining(deadline)):
            raise TimeoutError("Native ACL worker is unavailable")
        try:
            executable = locate_powershell()
            environment = powershell_environment(executable)
            environment.pop("EGAKUMI_SERIAL_ACL_TARGET", None)
            environment.pop("EGAKUMI_SERIAL_ACL_OPERATION", None)
            environment["AZURE_LOGGING_ENABLE_LOG_FILE"] = "false"
            context = (os.getpid(), executable, environment)
            if self._pending_launch is not None:
                pending = self._pending_launch
                if not pending["complete"].is_set():
                    raise OSError("Previous native ACL startup has not completed")
                if pending["process"] is not None and not _stop_acl_process(pending["process"]):
                    raise OSError("Previous native ACL worker has not stopped")
                self._pending_launch = None
            if self._unstopped is not None:
                if not _stop_acl_process(self._unstopped):
                    raise OSError("Previous native ACL worker has not stopped")
                self._unstopped = None
            if self._process is not None and (self._context != context or self._process.poll() is not None):
                self._discard()
                if self._unstopped is not None:
                    raise OSError("Previous native ACL worker has not stopped")
            if self._process is None:
                self._start(executable, environment, deadline)
                self._context = context
            self._request_id += 1
            request = {"requestId": self._request_id, "path": str(path),
                       "operation": "protect-new" if protect_new else "inspect"}
            self._requests.put_nowait(json.dumps(request, ensure_ascii=True))
            reply = self._responses.get(timeout=self._remaining(deadline))
            if reply is None:
                raise ValueError("Native ACL worker stopped")
            actual = json.loads(reply)
            if (not isinstance(actual, dict) or set(actual) != {"requestId", "ownerOnly"}
                    or type(actual["requestId"]) is not int or actual["requestId"] != self._request_id
                    or actual["ownerOnly"] is not True):
                raise ValueError("Invalid native ACL reply")
            self._remaining(deadline)
        except BaseException:
            self._discard()
            raise
        finally:
            self._lock.release()

    def close(self):
        if self.pid != os.getpid():
            return
        if self._lock.acquire(timeout=1):
            try:
                self._discard()
                if self._unstopped is not None:
                    _stop_acl_process(self._unstopped)
                if self._pending_launch is not None:
                    self._pending_launch["cancelled"] = True
                    if self._pending_launch["process"] is not None:
                        _stop_acl_process(self._pending_launch["process"])
            finally:
                self._lock.release()
        elif self._process is not None:
            _stop_acl_process(self._process)


_WINDOWS_ACL_WORKER = _WindowsACLWorker()


def _close_windows_acl_worker():
    _WINDOWS_ACL_WORKER.close()


atexit.register(_close_windows_acl_worker)


def _windows_owner_acl(path: Path, *, protect_new: bool = False) -> None:
    """Windowsの実DACLを毎回検査し、native processだけを再利用する。"""
    global _WINDOWS_ACL_WORKER
    try:
        if _WINDOWS_ACL_WORKER.pid != os.getpid():
            _WINDOWS_ACL_WORKER = _WindowsACLWorker()
        _WINDOWS_ACL_WORKER.check(path, protect_new=protect_new)
        return
    except Exception:
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

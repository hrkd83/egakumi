"""Synthetic ACL worker protocol, bounded failures and fresh native inspection."""
from concurrent.futures import ThreadPoolExecutor
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from deployment.helpers import private_permissions as permissions


class FakeACLProcess:
    """A bounded-protocol child fixture without reading any real ACL or file."""

    def __init__(self, respond=None):
        self.returncode = None
        self.terminated = threading.Event()
        self.replies = queue.Queue()
        self.packets = []
        self.respond = respond
        process = self

        class Input:
            def __init__(self):
                self.pending = ""
                self.closed = False

            def write(self, value):
                self.pending += value

            def flush(self):
                request = json.loads(self.pending)
                self.pending = ""
                process.packets.append(request)
                if process.respond is None:
                    reply = json.dumps({"requestId": request["requestId"], "ownerOnly": True}) + "\n"
                else:
                    reply = process.respond(request, process)
                if reply is not None:
                    process.replies.put(reply)

            def close(self):
                self.closed = True

        class Output:
            def __init__(self):
                self.closed = False

            def readline(self, limit):
                return process.replies.get()[:limit]

            def close(self):
                self.closed = True

        self.stdin, self.stdout = Input(), Output()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -1
        self.terminated.set()
        self.replies.put("")

    kill = terminate

    def wait(self, timeout):
        if not self.terminated.wait(timeout):
            raise subprocess.TimeoutExpired("synthetic-native-acl", timeout)
        return self.returncode


class ACLWorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = permissions._WindowsACLWorker()
        self.processes = []
        self.enterContext(mock.patch.object(permissions, "_WINDOWS_ACL_WORKER", self.worker))
        self.locate = self.enterContext(mock.patch.object(permissions, "locate_powershell", return_value="pwsh"))
        self.environment = self.enterContext(mock.patch.object(permissions, "powershell_environment", return_value={}))
        self.launch = self.enterContext(mock.patch.object(permissions.subprocess, "Popen", side_effect=self.create_process))
        self.addCleanup(self.worker.close)

    def create_process(self, *args, **kwargs):
        process = FakeACLProcess()
        self.processes.append(process)
        return process

    def check(self, path="synthetic-file", **kwargs):
        permissions._windows_owner_acl(Path(path), **kwargs)

    def test_reuses_only_process_and_performs_every_requested_check(self):
        self.check(protect_new=True)
        self.check()
        self.check("synthetic-other-file")
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual([p["requestId"] for p in self.processes[0].packets], [1, 2, 3])
        self.assertEqual([p["operation"] for p in self.processes[0].packets], ["protect-new", "inspect", "inspect"])

    def test_paths_are_json_stdin_data_and_checker_is_unchanged(self):
        path = 'synthetic-quote-"-日本語'
        self.check(path, protect_new=True)
        argv = self.launch.call_args.args[0]
        options = self.launch.call_args.kwargs
        self.assertNotIn(path, argv)
        self.assertNotIn("EGAKUMI_SERIAL_ACL_TARGET", options["env"])
        self.assertNotIn("EGAKUMI_SERIAL_ACL_OPERATION", options["env"])
        self.assertEqual(self.processes[0].packets, [{"requestId": 1, "path": path, "operation": "protect-new"}])
        script = base64.b64decode(argv[-1]).decode("utf-16-le")
        self.assertEqual(script, permissions._WINDOWS_ACL_WORKER_SCRIPT)
        self.assertIn(permissions._WINDOWS_ACL_SCRIPT, script)
        self.assertNotIn("Invoke-Expression", script)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def test_concurrent_callers_receive_their_own_native_result(self):
        def respond(request, process):
            time.sleep(0.002)
            return json.dumps({"requestId": request["requestId"], "ownerOnly": True}) + "\n"
        process = FakeACLProcess(respond)
        self.launch.side_effect = None
        self.launch.return_value = process
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda n: self.check("synthetic-" + str(n)), range(16)))
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual([p["requestId"] for p in process.packets], list(range(1, 17)))
        self.assertEqual(len({p["path"] for p in process.packets}), 16)

    def test_malformed_stale_oversized_and_failed_replies_fail_without_replay(self):
        replies = ["synthetic-invalid-json\n", '{"requestId":2,"ownerOnly":true}\n',
                   '{"requestId":true,"ownerOnly":true}\n', '{"requestId":1,"ownerOnly":false}\n',
                   '{"requestId":1,"ownerOnly":true,"extra":"synthetic-private-output"}\n',
                   '{"requestId":1,"ownerOnly":true}', "x" * 257 + "\n", ""]
        for reply in replies:
            with self.subTest(reply_kind=replies.index(reply)):
                worker = permissions._WindowsACLWorker()
                process = FakeACLProcess(lambda request, child, value=reply: value)
                with mock.patch.object(permissions, "_WINDOWS_ACL_WORKER", worker), \
                     mock.patch.object(permissions.subprocess, "Popen", return_value=process) as launch:
                    with self.assertRaises(permissions.PrivatePermissionsError) as failure:
                        self.check()
                    self.assertEqual(str(failure.exception), "Private runtime storage requires owner-only Windows ACLs")
                    self.assertEqual(launch.call_count, 1)
                    self.assertEqual(len(process.packets), 1)
                    self.assertTrue(process.terminated.is_set())
                    worker.close()

    def test_new_call_can_start_fresh_but_failed_call_is_never_replayed(self):
        failed = FakeACLProcess(lambda request, process: '{"ownerOnly":false}\n')
        succeeded = FakeACLProcess()
        self.launch.side_effect = [failed, succeeded]
        with self.assertRaises(permissions.PrivatePermissionsError):
            self.check()
        self.assertEqual(self.launch.call_count, 1)
        self.check()
        self.assertEqual(self.launch.call_count, 2)
        self.assertEqual(len(failed.packets), 1)
        self.assertEqual(len(succeeded.packets), 1)
        self.assertEqual(succeeded.packets[0]["requestId"], 2)

    def test_lock_wait_has_the_same_bounded_call_budget(self):
        self.worker._lock.acquire()
        try:
            with mock.patch.object(permissions, "_ACL_TIMEOUT", 0.03):
                start = time.monotonic()
                with self.assertRaises(permissions.PrivatePermissionsError):
                    self.check()
            self.assertLess(time.monotonic() - start, 1)
            self.launch.assert_not_called()
        finally:
            self.worker._lock.release()

    def test_read_and_write_stalls_are_bounded_and_child_is_stopped(self):
        for stall in ("read", "write"):
            with self.subTest(stall=stall):
                worker = permissions._WindowsACLWorker()
                def respond(request, process):
                    if stall == "write":
                        process.terminated.wait()
                    return None
                process = FakeACLProcess(respond)
                with mock.patch.object(permissions, "_WINDOWS_ACL_WORKER", worker), \
                     mock.patch.object(permissions.subprocess, "Popen", return_value=process) as launch, \
                     mock.patch.object(permissions, "_ACL_TIMEOUT", 0.05):
                    start = time.monotonic()
                    with self.assertRaises(permissions.PrivatePermissionsError):
                        self.check()
                    self.assertLess(time.monotonic() - start, 1)
                    self.assertTrue(process.terminated.is_set())
                    self.assertEqual(launch.call_count, 1)
                    worker.close()

    def test_late_startup_is_cancelled_and_not_replaced_until_finished(self):
        release = threading.Event()
        late, fresh = FakeACLProcess(), FakeACLProcess()
        def delayed(*args, **kwargs):
            release.wait()
            return late
        self.launch.side_effect = delayed
        try:
            with mock.patch.object(permissions, "_ACL_TIMEOUT", 0.03):
                with self.assertRaises(permissions.PrivatePermissionsError):
                    self.check()
                with self.assertRaises(permissions.PrivatePermissionsError):
                    self.check()
            self.assertEqual(self.launch.call_count, 1)
            release.set()
            self.assertTrue(late.terminated.wait(1))
            self.worker._pending_launch["complete"].wait(1)
            self.launch.side_effect = None
            self.launch.return_value = fresh
            self.check()
            self.assertEqual(self.launch.call_count, 2)
            self.assertEqual(late.packets, [])
        finally:
            release.set()

    def test_startup_failure_has_fixed_error_without_external_detail(self):
        self.launch.side_effect = OSError("synthetic-private-launch-error")
        with self.assertRaises(permissions.PrivatePermissionsError) as failure:
            self.check()
        self.assertNotIn("synthetic-private", str(failure.exception))
        self.assertEqual(self.launch.call_count, 1)

    def test_executable_or_environment_change_replaces_the_idle_worker(self):
        self.check()
        self.locate.return_value = "synthetic-other-pwsh"
        self.check()
        self.environment.return_value = {"PSModulePath": "<SYNTHETIC_MODULE_PATH>"}
        self.check()
        self.assertEqual(self.launch.call_count, 3)
        self.assertTrue(self.processes[0].terminated.is_set())
        self.assertTrue(self.processes[1].terminated.is_set())
        self.assertEqual(len(self.processes[2].packets), 1)

    def test_worker_cannot_be_used_from_a_different_process(self):
        with mock.patch.object(permissions.os, "getpid", return_value=self.worker.pid + 1):
            with self.assertRaises(TimeoutError):
                self.worker.check(Path("synthetic-file"))
        self.launch.assert_not_called()

    def test_unstopped_child_prevents_another_native_worker(self):
        process = FakeACLProcess(lambda request, child: '{"ownerOnly":false}\n')
        process.terminate = mock.Mock(side_effect=OSError("synthetic-termination-error"))
        self.launch.side_effect = None
        self.launch.return_value = process
        with self.assertRaises(permissions.PrivatePermissionsError):
            self.check()
        with self.assertRaises(permissions.PrivatePermissionsError):
            self.check()
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(len(process.packets), 1)
        FakeACLProcess.terminate(process)

    def test_close_stops_idle_child_and_closes_streams(self):
        self.check()
        process = self.processes[0]
        self.worker.close()
        self.assertTrue(process.terminated.is_set())
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)

    def test_protect_new_path_keeps_both_native_inspections(self):
        target = Path("synthetic-empty-file")
        portable_os = SimpleNamespace(name="nt", getpid=os.getpid)
        with mock.patch.object(permissions, "os", portable_os), \
             mock.patch.object(permissions, "regular_path", return_value=SimpleNamespace(st_size=0)), \
             mock.patch.object(permissions.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True):
            permissions.protect_new_path(target)
        self.assertEqual([p["operation"] for p in self.processes[0].packets], ["protect-new", "inspect"])
        self.assertEqual(self.launch.call_count, 1)


@unittest.skipUnless(os.name == "nt", "Uses Windows native DACLs")
class NativeACLWorkerTests(unittest.TestCase):
    def test_reused_worker_detects_acl_change_without_cached_success(self):
        worker = permissions._WindowsACLWorker()
        self.addCleanup(worker.close)
        with mock.patch.object(permissions, "_WINDOWS_ACL_WORKER", worker), tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "synthetic-empty-file"
            target.touch()
            permissions._windows_owner_acl(target, protect_new=True)
            process = worker._process
            permissions._windows_owner_acl(target)
            self.assertIs(worker._process, process)
            executable = permissions.locate_powershell()
            environment = permissions.powershell_environment(executable)
            environment["EGAKUMI_TEST_ACL_FILE"] = str(target)
            script = r"""
$ErrorActionPreference = 'Stop'
try {
    $target = $env:EGAKUMI_TEST_ACL_FILE
    $acl = Get-Acl -LiteralPath $target
    $sid = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        $sid, [Security.AccessControl.FileSystemRights]::Read,
        [Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
    $item = Get-Item -LiteralPath $target
    if ($null -ne ('System.IO.FileSystemAclExtensions' -as [type])) {
        [IO.FileSystemAclExtensions]::SetAccessControl($item, $acl)
    } else {
        $item.SetAccessControl($acl)
    }
} catch { exit 2 }
"""
            result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                                     base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                                    env=environment, capture_output=True, timeout=20, check=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, "Synthetic ACL mutation failed")
            with self.assertRaises(permissions.PrivatePermissionsError):
                permissions._windows_owner_acl(target)
            self.assertIsNone(worker._process)
            self.assertIsNotNone(process.poll())


if __name__ == "__main__":
    unittest.main()

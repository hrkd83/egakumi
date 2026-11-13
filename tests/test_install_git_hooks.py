"""Gitフックの検査範囲・標準入力・失敗停止を合成入力で確認する。"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


INSTALLER = Path(__file__).resolve().parents[1] / "scripts/install_git_hooks.py"
SPEC = importlib.util.spec_from_file_location("install_git_hooks_test_subject", INSTALLER)
assert SPEC is not None and SPEC.loader is not None
hooks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hooks)

# Gitやこのクローンの設定へ触れず、実CLI・実stdin・実scanner子プロセスを通す。
DRIVER = """
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location("synthetic_hook_subject", sys.argv[1])
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)
root = Path(sys.argv[2]).resolve()
subject.repository_root = lambda: root
if sys.argv[3] == "bounded":
    subject.MAX_PUSH_INPUT_BYTES = 128
raise SystemExit(subject.main(sys.argv[4:]))
"""

SCANNER = """
import json
import os
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
with (root / "calls.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({
        "arguments": sys.argv[1:],
        "stdin": list(sys.stdin.buffer.read()),
        "no_bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE"),
        "isolated": sys.flags.isolated,
        "no_site": sys.flags.no_site,
    }) + "\\n")
print("SYNTHETIC_SCANNER_OUTPUT")
print("SYNTHETIC_SCANNER_OUTPUT", file=sys.stderr)
raise SystemExit(int((root / "scanner-status").read_text(encoding="ascii")))
"""


class InstallGitHookTests(unittest.TestCase):
    """検査処理は合成scannerとし、フックのプロセス境界を検証する。"""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-hook-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "scripts").mkdir()
        (self.root / "scripts/check_public_data.py").write_text(
            SCANNER, encoding="utf-8",
        )
        (self.root / "scripts/public-data-policy.json").write_text(
            "{}\n", encoding="utf-8",
        )
        (self.root / "scanner-status").write_text("0", encoding="ascii")

    def invoke(
        self, hook: str, *arguments: str, data: bytes = b"", bounded: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-I", "-B", "-S", "-c", DRIVER,
             str(INSTALLER), str(self.root), "bounded" if bounded else "normal",
             "--run-hook", hook, "--", *arguments],
            input=data, capture_output=True, env=environment,
            timeout=15, check=False,
        )

    def calls(self) -> list[dict]:
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def assert_hidden_output(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertNotIn(b"SYNTHETIC_SCANNER_OUTPUT", result.stdout + result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_pre_push_checks_history_and_refs_once_with_exact_standard_input(self) -> None:
        # branch・tag・削除通知を同時に渡す。OIDは実履歴と無関係の合成値。
        updates = (
            b"refs/heads/main " + b"1" * 40 + b" refs/heads/main " + b"2" * 40 + b"\r\n"
            b"refs/tags/release " + b"3" * 40 + b" refs/tags/release " + b"0" * 40 + b"\n"
            b"(delete) " + b"0" * 40 + b" refs/heads/retired " + b"4" * 40 + b"\n"
        )
        result = self.invoke("pre-push", "origin", "https://example.com/repository", data=updates)
        self.assertEqual(result.returncode, 0, "pre-push must accept a successful check")
        calls = self.calls()
        self.assertEqual(len(calls), 1, "all scopes must share one scanner process")
        self.assertEqual(calls[0]["arguments"], ["--all-history", "--pre-push-refs"])
        self.assertTrue(bytes(calls[0]["stdin"]) == updates, "push stdin must reach the scanner unchanged")
        self.assertEqual((calls[0]["isolated"], calls[0]["no_site"], calls[0]["no_bytecode"]), (1, 1, "1"))
        self.assert_hidden_output(result)

    def test_empty_pre_push_input_still_runs_both_checks_once(self) -> None:
        result = self.invoke("pre-push", "origin", "https://example.com/repository")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0]["arguments"], ["--all-history", "--pre-push-refs"])
        self.assertEqual(self.calls()[0]["stdin"], [])
        self.assert_hidden_output(result)

    def test_scanner_failure_stops_push_without_retry_or_raw_output(self) -> None:
        (self.root / "scanner-status").write_text("7", encoding="ascii")
        result = self.invoke("pre-push", "origin", "https://example.com/repository", data=b"synthetic input\n")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls()), 1, "a failed check must not be retried")
        self.assertIn(b"check did not pass.", result.stderr)
        self.assert_hidden_output(result)

    def test_oversized_push_input_stops_before_scanner_startup(self) -> None:
        result = self.invoke("pre-push", "origin", "https://example.com/repository", data=b"x" * 129, bounded=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])
        self.assertIn(b"push input exceeds the limit.", result.stderr)

    def test_invalid_push_arguments_stop_without_echoing_them(self) -> None:
        result = self.invoke("pre-push", "SYNTHETIC_ARGUMENT")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])
        self.assertNotIn(b"SYNTHETIC_ARGUMENT", result.stdout + result.stderr)

    def test_missing_guard_policy_stops_before_scanner_startup(self) -> None:
        (self.root / "scripts/public-data-policy.json").unlink()
        result = self.invoke("pre-push", "origin", "https://example.com/repository")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])
        self.assertIn(b"required guard files are unavailable.", result.stderr)

    def test_pre_commit_keeps_staged_and_worktree_checks(self) -> None:
        result = self.invoke("pre-commit")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0]["arguments"], ["--staged", "--worktree"])
        self.assert_hidden_output(result)

    def test_commit_message_check_is_not_removed(self) -> None:
        message = self.root / "message.txt"
        message.write_text("Synthetic commit message.\n", encoding="utf-8")
        result = self.invoke("commit-msg", str(message))
        self.assertEqual(result.returncode, 0)
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["arguments"][0], "--commit-message")
        self.assertTrue(calls[0]["arguments"][1] == str(message), "the message file must reach the scanner")
        self.assert_hidden_output(result)

    def test_timeout_and_startup_failure_have_distinct_safe_messages(self) -> None:
        cases = (
            (subprocess.TimeoutExpired("SYNTHETIC_EXCEPTION_VALUE", 300,
                                       output=b"SYNTHETIC_EXCEPTION_VALUE"),
             "Public-data guard: scanner exceeded the time limit."),
            (OSError("SYNTHETIC_EXCEPTION_VALUE"),
             "Public-data guard: scanner startup failed."),
        )
        for failure, expected in cases:
            output = io.StringIO()
            with self.subTest(failure=type(failure).__name__), \
                 mock.patch.object(hooks, "repository_root", return_value=self.root), \
                 mock.patch.object(hooks.subprocess, "run", side_effect=failure) as run, \
                 mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"synthetic input\n"))), \
                 contextlib.redirect_stderr(output):
                self.assertEqual(hooks.main(["--run-hook", "pre-push", "--", "origin", "https://example.com/repository"]), 1)
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs["input"], b"synthetic input\n")
                self.assertEqual(output.getvalue().strip(), expected)
                self.assertNotIn("SYNTHETIC_EXCEPTION_VALUE", output.getvalue())


if __name__ == "__main__":
    unittest.main()

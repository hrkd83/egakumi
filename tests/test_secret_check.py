"""The CI secret check inspects changed blobs without printing their contents."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_secrets.py"


class SecretCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "Example contributor")
        self.git("config", "user.email", "developer@example.com")
        self.write("README.md", "Independent synthetic source fixture.\n")
        self.base = self.save()

    def git(self, *arguments):
        return subprocess.check_output(["git", *arguments], cwd=self.root, stderr=subprocess.DEVNULL).strip().decode()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())

    def save(self):
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "Synthetic source fixture")
        return self.git("rev-parse", "HEAD")

    def run_check(self, *, base=None, head="HEAD"):
        arguments = [sys.executable, "-I", "-B", str(SCRIPT), "--head", head]
        if base is not None:
            arguments += ["--base", base]
        return subprocess.run(arguments, cwd=self.root, capture_output=True, text=True, check=False)

    def test_known_token_families_are_rejected_without_echoing_values_or_paths(self):
        for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_", "sk-", "sk-proj-",
                       "sk-svcacct-", "xoxb-", "xoxp-", "xoxa-", "xoxr-", "xoxs-"):
            with self.subTest(prefix=prefix):
                token = prefix + "Q" * 48
                self.write("do-not-print-this-name.txt", "first line\n" + token + "\n")
                self.save()
                result = self.run_check(base=self.base)
                self.assertEqual(result.returncode, 1)
                self.assertIn("line=2", result.stdout)
                self.assertNotIn(token, result.stdout + result.stderr)
                self.assertNotIn("do-not-print-this-name", result.stdout + result.stderr)

    def test_private_key_headers_are_rejected(self):
        for kind in ("", "RSA ", "EC ", "DSA ", "OPENSSH ", "ENCRYPTED "):
            with self.subTest(kind=kind):
                header = "-----BEGIN " + kind + "PRIVATE KEY-----"
                self.write("private.txt", header + "\n" + "M" * 64 + "\n")
                self.save()
                result = self.run_check(base=self.base)
                self.assertEqual(result.returncode, 1)
                self.assertIn("rule=private-key", result.stdout)
                self.assertNotIn(header, result.stdout + result.stderr)

    def test_crlf_and_json_escaped_private_key_material_are_rejected(self):
        header = "-----BEGIN " + "PRIVATE KEY-----"
        for content in (header + "\r\n" + "M" * 64,
                        json.dumps({"value": header + "\n" + "M" * 64}),
                        json.dumps({"value": header + "\r\n" + "M" * 64})):
            with self.subTest(content=content):
                self.write("private.txt", content)
                self.save()
                self.assertEqual(self.run_check(base=self.base).returncode, 1)

    def test_bare_headers_and_explanatory_placeholders_are_not_key_material(self):
        header = "-----BEGIN " + "PRIVATE KEY-----"
        for content in (header, header + "\nThis is an explanation, not encoded key material.",
                        json.dumps({"invalidInput": header})):
            with self.subTest(content=content):
                self.write("placeholder.txt", content)
                self.save()
                self.assertEqual(self.run_check(base=self.base).returncode, 0)

    def test_domains_names_ids_ips_and_public_keys_are_not_secrets(self):
        public_header = "-----BEGIN " + "PUBLIC KEY-----"
        self.write("public.json", json.dumps({
            "company": "Example Company", "domain": "example.com", "email": "developer@example.com",
            "tenantId": "11111111-1111-1111-1111-111111111111", "ip": "192.0.2.10",
            "password": "example", "key": public_header,
        }))
        self.save()
        self.assertEqual(self.run_check(base=self.base).returncode, 0)

    def test_tokens_in_plain_json_strings_are_rejected(self):
        token = "ghp_" + "J" * 40
        self.write("data.json", json.dumps({"value": token}))
        self.save()
        self.assertEqual(self.run_check(base=self.base).returncode, 1)

    def test_only_new_changes_are_inspected_when_base_is_given(self):
        self.write("old.txt", "ghp_" + "K" * 40)
        existing = self.save()
        self.write("new.txt", "A normal public file.\n")
        self.save()
        self.assertEqual(self.run_check(base=existing).returncode, 0)
        self.assertEqual(self.run_check().returncode, 1)

    def test_deleted_files_and_historical_secrets_are_not_inspected(self):
        self.write("removed.txt", "ghp_" + "R" * 40)
        existing = self.save()
        (self.root / "removed.txt").unlink()
        self.save()
        self.assertEqual(self.run_check(base=existing).returncode, 0)
        self.assertEqual(self.run_check().returncode, 0)

    def test_rename_destination_and_type_changes_are_inspected(self):
        self.write("first.txt", "ghp_" + "S" * 40)
        existing = self.save()
        (self.root / "first.txt").rename(self.root / "second.txt")
        self.save()
        self.assertEqual(self.run_check(base=existing).returncode, 1)

    def test_untracked_worktree_and_private_caches_are_not_read(self):
        self.write(".local/not-tracked.txt", "ghp_" + "U" * 40)
        self.assertEqual(self.run_check(base=self.base).returncode, 0)
        self.assertEqual(self.run_check().returncode, 0)

    def test_nul_binary_and_oversized_blobs_are_skipped(self):
        token = ("ghp_" + "B" * 40).encode()
        self.write("binary.dat", b"\0" + token)
        self.write("large.txt", token + b" " * (2 * 1024 * 1024))
        self.save()
        result = self.run_check(base=self.base)
        self.assertEqual(result.returncode, 0)
        self.assertIn("skipped=2", result.stdout)

    def test_git_errors_and_invalid_arguments_use_fixed_safe_errors(self):
        for arguments in (("--base", "do-not-print-this-value"), ("--head", "missing-private-name"),
                          ("--invalid-private-argument",)):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), *arguments],
                                        cwd=self.root, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "secret-check: ERROR inspection failed\n")
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()

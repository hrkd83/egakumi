"""SSH鍵の初回生成・再利用・失敗時の保全を、一時領域だけで確認する。

実ssh-keygenでRSA鍵を1組生成する。失敗と競合は局所的に再現し、既存の
ユーザー鍵・Azure・ネットワークへ触れない。秘密の値を試験出力へ書かない。
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import ssh_keys
from deployment.helpers.configuration import load_ssh_public_key


PRIVATE_MARKER = "SYNTHETIC-PRIVATE-MUST-NOT-APPEAR"


def public_fixture() -> str:
    """秘密鍵を必要としない、形式検査用の合成Ed25519公開鍵。"""
    def field(value):
        return len(value).to_bytes(4, "big") + value
    return "ssh-ed25519 " + base64.b64encode(field(b"ssh-ed25519") + field(b"x" * 32)).decode() + " fixture"


class ManagementSshKeyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="core-ssh-keys-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.public = self.root / "secrets/management-ssh.pub"
        self.private = self.public.with_suffix("")
        self.config = {"management": {"sshPublicKeyFile": str(self.public)}}

    def fake_generate(self, arguments, **kwargs):
        """生成成功後のファイルを合成し、異常時の公開処理だけを独立して調べる。"""
        private = Path(arguments[arguments.index("-f") + 1])
        private.write_text(PRIVATE_MARKER)
        private.with_suffix(".pub").write_text(public_fixture())
        return subprocess.CompletedProcess(arguments, 0, PRIVATE_MARKER, PRIVATE_MARKER)

    def assert_no_staging(self):
        self.assertEqual(list(self.root.rglob(".management-ssh-*")), [])

    def test_actual_rsa4096_generation_is_private_and_second_call_reuses_it(self):
        """本物のローカル鍵生成と2回目の内容・inode・mtime保持を確認する。"""
        output, errors = io.StringIO(), io.StringIO()
        real_run = subprocess.run
        with mock.patch.object(ssh_keys.subprocess, "run", wraps=real_run) as run, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = ssh_keys.ensure_management_ssh_key(self.config, self.root)
            before = [(hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_ino, path.stat().st_mtime_ns)
                      for path in (self.private, self.public)]
            repeated = ssh_keys.ensure_management_ssh_key(self.config, self.root)
        self.assertEqual(result, {"created": True, "publicKeyFile": str(self.public), "privateKeyFile": str(self.private)})
        self.assertEqual(repeated, {"created": False, "publicKeyFile": str(self.public)})
        self.assertEqual(before, [(hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_ino, path.stat().st_mtime_ns)
                                  for path in (self.private, self.public)])
        self.assertTrue(load_ssh_public_key(str(self.public)).startswith("ssh-rsa "))
        # 公開鍵のRSA modulusを読み、コマンド引数だけでなく4096bitの実生成も確認。
        blob = base64.b64decode(self.public.read_text().split()[1])
        fields = []
        while blob:
            length = int.from_bytes(blob[:4], "big")
            fields.append(blob[4:4 + length]); blob = blob[4 + length:]
        self.assertEqual(int.from_bytes(fields[2], "big").bit_length(), 4096)
        run.assert_called_once()
        arguments, kwargs = run.call_args
        self.assertEqual(arguments[0][1:9], ["-q", "-t", "rsa", "-b", "4096", "-N", "", "-C"])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], 60)
        for path in (self.private, self.public):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.public.parent.stat().st_mode), 0o700)
        self.assertEqual(output.getvalue() + errors.getvalue(), "")
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", json.dumps(result))
        self.assert_no_staging()

    def test_existing_imported_public_only_file_is_validated_without_generation(self):
        """secrets外/.pub以外/秘密鍵なしの既存参照も、従来どおり再利用する。"""
        imported = self.root / "imported-public-key"
        imported.write_text(public_fixture()); imported.chmod(0o644)
        config = {"management": {"sshPublicKeyFile": str(imported)}}
        with mock.patch.object(ssh_keys.subprocess, "run") as run:
            self.assertEqual(ssh_keys.ensure_management_ssh_key(config, self.root),
                             {"created": False, "publicKeyFile": str(imported)})
        run.assert_not_called()
        self.assertFalse(self.public.parent.exists())
        self.assertEqual(stat.S_IMODE(imported.stat().st_mode), 0o644)

    def test_existing_public_only_file_in_secrets_is_reused(self):
        """自動生成用の標準パスでも、既存の公開鍵だけで再利用できる。"""
        self.public.parent.mkdir(mode=0o700)
        self.public.write_text(public_fixture()); self.public.chmod(0o644)
        with mock.patch.object(ssh_keys.subprocess, "run") as run:
            self.assertEqual(ssh_keys.ensure_management_ssh_key(self.config, self.root),
                             {"created": False, "publicKeyFile": str(self.public)})
        self.assertFalse(self.private.exists())
        run.assert_not_called()

    def test_existing_public_pair_does_not_read_or_modify_private_file(self):
        self.public.parent.mkdir(mode=0o700)
        self.public.write_text(public_fixture())
        self.private.write_text(PRIVATE_MARKER)
        original = Path.read_text
        def read_public_only(path, *args, **kwargs):
            if path == self.private:
                raise AssertionError("Existing private keys must never be read")
            return original(path, *args, **kwargs)
        before = self.private.stat()
        with mock.patch.object(Path, "read_text", read_public_only), mock.patch.object(ssh_keys.subprocess, "run") as run:
            result = ssh_keys.ensure_management_ssh_key(self.config, self.root)
        self.assertFalse(result["created"])
        self.assertNotIn("privateKeyFile", result)
        self.assertEqual(self.private.stat(), before)
        run.assert_not_called()

    def test_invalid_existing_public_key_is_not_replaced(self):
        self.public.parent.mkdir(mode=0o700)
        self.public.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n" + PRIVATE_MARKER)
        with mock.patch.object(ssh_keys.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "public key") as error:
                ssh_keys.ensure_management_ssh_key(self.config, self.root)
        self.assertNotIn(PRIVATE_MARKER, str(error.exception))
        self.assertIn(PRIVATE_MARKER, self.public.read_text())
        run.assert_not_called()

    def test_missing_public_key_preserves_an_existing_private_companion(self):
        self.private.parent.mkdir(mode=0o700)
        self.private.write_text(PRIVATE_MARKER); self.private.chmod(0o600)
        before = self.private.stat()
        with mock.patch.object(ssh_keys.subprocess, "run") as run:
            with self.assertRaisesRegex(ssh_keys.SshKeyError, "private companion"):
                ssh_keys.ensure_management_ssh_key(self.config, self.root)
        self.assertEqual(self.private.stat(), before)
        self.assertEqual(self.private.read_text(), PRIVATE_MARKER)
        self.assertFalse(self.public.exists())
        run.assert_not_called()

    def test_missing_file_requires_secrets_subdirectory_pub_suffix_and_absolute_path(self):
        paths = [self.root / "tracked.pub", self.root / "secrets/key", self.root / "secrets/../escape.pub", Path("secrets/key.pub")]
        for path in paths:
            with self.subTest(path=path), mock.patch.object(ssh_keys.subprocess, "run") as run:
                with self.assertRaises(ssh_keys.SshKeyError):
                    ssh_keys.ensure_management_ssh_key({"management": {"sshPublicKeyFile": str(path)}}, self.root)
                run.assert_not_called()
        self.assertFalse(self.public.parent.exists())

    def test_symlinks_non_directories_and_shared_generation_directory_are_rejected(self):
        for kind in ("directory-link", "public-link", "private-link", "shared-directory", "parent-file"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir="/tmp") as directory:
                root = Path(directory).resolve(); secrets = root / "secrets"; public = secrets / "key.pub"
                if kind == "directory-link":
                    target = root / "elsewhere"; target.mkdir(mode=0o700); secrets.symlink_to(target, target_is_directory=True)
                elif kind == "parent-file":
                    secrets.write_text("existing")
                else:
                    secrets.mkdir(mode=0o700)
                    if kind == "shared-directory": secrets.chmod(0o755)
                    if kind == "public-link": public.symlink_to(root / "missing-public")
                    if kind == "private-link": public.with_suffix("").symlink_to(root / "missing-private")
                with mock.patch.object(ssh_keys.subprocess, "run") as run:
                    with self.assertRaises(ssh_keys.SshKeyError):
                        ssh_keys.ensure_management_ssh_key({"management": {"sshPublicKeyFile": str(public)}}, root)
                run.assert_not_called()
                self.assertFalse((root / "elsewhere/key").exists())

    def test_missing_keygen_fails_without_creating_keys(self):
        with mock.patch.object(ssh_keys.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ssh_keys.SshKeyError, "install ssh-keygen"):
                ssh_keys.ensure_management_ssh_key(self.config, self.root)
        self.assertFalse(self.public.parent.exists())

    def test_command_failures_timeouts_and_incomplete_output_are_safe_and_cleaned(self):
        for kind in ("failed", "timeout", "os-error", "incomplete", "invalid-public"):
            with self.subTest(kind=kind):
                def failing(arguments, **kwargs):
                    if kind == "timeout":
                        raise subprocess.TimeoutExpired(arguments, 60, output=PRIVATE_MARKER, stderr=PRIVATE_MARKER)
                    if kind == "os-error": raise OSError(PRIVATE_MARKER)
                    if kind == "failed": return subprocess.CompletedProcess(arguments, 1, PRIVATE_MARKER, PRIVATE_MARKER)
                    private = Path(arguments[-1]); private.write_text(PRIVATE_MARKER)
                    if kind == "invalid-public": private.with_suffix(".pub").write_text(PRIVATE_MARKER)
                    return subprocess.CompletedProcess(arguments, 0, PRIVATE_MARKER, PRIVATE_MARKER)
                output, errors = io.StringIO(), io.StringIO()
                with mock.patch.object(ssh_keys.subprocess, "run", side_effect=failing), \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    with self.assertRaises(ValueError) as caught:
                        ssh_keys.ensure_management_ssh_key(self.config, self.root)
                text = "".join(traceback.format_exception(caught.exception)) + output.getvalue() + errors.getvalue()
                self.assertNotIn(PRIVATE_MARKER, text)
                self.assertFalse(self.private.exists()); self.assertFalse(self.public.exists())
                self.assert_no_staging()

    def test_exclusive_publication_preserves_a_competing_private_file(self):
        link = os.link
        def compete(source, destination, **kwargs):
            self.private.write_text("COMPETING-PRIVATE")
            return link(source, destination, **kwargs)
        with mock.patch.object(ssh_keys.subprocess, "run", side_effect=self.fake_generate), \
                mock.patch.object(ssh_keys.os, "link", side_effect=compete):
            with self.assertRaisesRegex(ssh_keys.SshKeyError, "publication"):
                ssh_keys.ensure_management_ssh_key(self.config, self.root)
        self.assertEqual(self.private.read_text(), "COMPETING-PRIVATE")
        self.assertFalse(self.public.exists()); self.assert_no_staging()

    def test_second_publication_failure_removes_only_our_private_file(self):
        link = os.link
        def compete(source, destination, **kwargs):
            if destination == self.public:
                self.public.write_text(public_fixture())
            return link(source, destination, **kwargs)
        with mock.patch.object(ssh_keys.subprocess, "run", side_effect=self.fake_generate), \
                mock.patch.object(ssh_keys.os, "link", side_effect=compete):
            with self.assertRaisesRegex(ssh_keys.SshKeyError, "publication"):
                ssh_keys.ensure_management_ssh_key(self.config, self.root)
        self.assertFalse(self.private.exists())
        self.assertEqual(self.public.read_text(), public_fixture()); self.assert_no_staging()

    def test_rollback_keeps_a_private_file_replaced_by_another_process(self):
        link = os.link
        def compete(source, destination, **kwargs):
            if destination == self.public:
                self.private.unlink(); self.private.write_text("REPLACEMENT-PRIVATE")
                raise FileExistsError("collision")
            return link(source, destination, **kwargs)
        with mock.patch.object(ssh_keys.subprocess, "run", side_effect=self.fake_generate), \
                mock.patch.object(ssh_keys.os, "link", side_effect=compete):
            with self.assertRaises(ssh_keys.SshKeyError):
                ssh_keys.ensure_management_ssh_key(self.config, self.root)
        self.assertEqual(self.private.read_text(), "REPLACEMENT-PRIVATE")
        self.assertFalse(self.public.exists()); self.assert_no_staging()


if __name__ == "__main__":
    unittest.main()

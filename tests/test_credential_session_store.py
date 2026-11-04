"""単一ホスト診断用保存先の原子的置換・版数・所有者限定アクセスを確認する。"""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from credential_session_store import FileSessionStore, MemorySessionStore, SessionStateConflict


class PrivateSessionStoreTests(unittest.TestCase):
    def test_memory_does_not_return_mutable_internal_state(self):
        store = MemorySessionStore({"revision": 0, "nested": {"generation": 1}})
        result = store.read()
        result["nested"]["generation"] = 99
        self.assertEqual(store.read()["nested"]["generation"], 1)

    def test_stale_revision_is_rejected_in_both_stores(self):
        with tempfile.TemporaryDirectory() as folder:
            stores = [MemorySessionStore()]
            if os.name != "nt": stores.append(FileSessionStore(Path(folder) / "session.json"))
            for store in stores:
                with self.subTest(store=type(store).__name__):
                    self.assertEqual(store.write({"generation": 1}, expected_revision=0)["revision"], 1)
                    with self.assertRaises(SessionStateConflict): store.write({"generation": 0}, expected_revision=0)
                    self.assertEqual(store.read()["generation"], 1)

    @unittest.skipIf(os.name == "nt", "診断CLIの所有者限定ファイルはWSL/POSIX専用")
    def test_file_is_atomic_persistent_and_owner_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.json"
            first = FileSessionStore(path)
            with first.locked():
                saved = first.write({"generation": 1, "pendingRotation": {"private": "candidate"}}, expected_revision=0)
            self.assertEqual(FileSessionStore(path).read(), saved)
            self.assertEqual({item.name for item in path.parent.iterdir()}, {"session.json.lock", "session.json"})
            if os.name != "nt": self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX所有者・リンク制約をWSLで確認する")
    def test_unprotected_or_symbolic_session_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.json"
            path.write_text('{"revision": 0}')
            path.chmod(0o644)
            with self.assertRaises(ValueError): FileSessionStore(path).read()
            path.chmod(0o600)
            linked = path.with_name("link.json")
            linked.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "symbolic links"): FileSessionStore(linked).read()

    @unittest.skipIf(os.name == "nt", "診断CLIの所有者限定ファイルはWSL/POSIX専用")
    def test_nested_lock_and_reopened_store_preserve_pending_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.json"
            store = FileSessionStore(path)
            with store.locked():
                with store.locked(): store.write({"generation": 1, "pendingRotation": {"id": "pending"}}, expected_revision=0)
            self.assertEqual(FileSessionStore(path).read()["pendingRotation"], {"id": "pending"})

    @unittest.skipIf(os.name == "nt", "POSIXファイルと親ディレクトリのfsyncをWSLで確認する")
    def test_candidate_save_flushes_file_then_parent_directory_before_return(self):
        events = []
        real_sync, real_replace = os.fsync, os.replace
        def sync(descriptor):
            events.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
            return real_sync(descriptor)
        def replace(source, target):
            events.append("replace")
            return real_replace(source, target)
        with tempfile.TemporaryDirectory() as folder, patch("credential_session_store.os.fsync", side_effect=sync), \
                patch("credential_session_store.os.replace", side_effect=replace):
            FileSessionStore(Path(folder) / "session.json").write({"pendingRotation": {"private": "candidate"}}, expected_revision=0)
        self.assertEqual(events, ["file", "replace", "directory"])

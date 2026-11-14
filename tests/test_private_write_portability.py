"""Synthetic protected evidence writing works with and without POSIX flags."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import integration_client


class PrivateWritePortabilityTests(unittest.TestCase):
    def test_missing_nofollow_and_fchmod_do_not_break_native_windows_writer(self):
        portable_os = SimpleNamespace(
            O_WRONLY=os.O_WRONLY, O_CREAT=os.O_CREAT, O_TRUNC=os.O_TRUNC,
            open=os.open, chmod=os.chmod, close=os.close, fdopen=os.fdopen)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "synthetic-evidence.json"
            with patch.object(integration_client, "os", portable_os):
                integration_client.private_write(output, '{"synthetic": true}\n')
            self.assertEqual(output.read_text(encoding="utf-8"), '{"synthetic": true}\n')

    def test_nofollow_is_preserved_when_available(self):
        sentinel = 1 << 29
        observed = []
        def open_checked(path, flags, mode):
            observed.append(flags)
            return os.open(path, flags & ~sentinel, mode)
        portable_os = SimpleNamespace(
            O_WRONLY=os.O_WRONLY, O_CREAT=os.O_CREAT, O_TRUNC=os.O_TRUNC,
            O_NOFOLLOW=sentinel, open=open_checked, chmod=os.chmod, close=os.close, fdopen=os.fdopen)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(integration_client, "os", portable_os):
                integration_client.private_write(Path(directory) / "synthetic.json", "{}")
        self.assertTrue(observed[0] & sentinel)

    def test_parent_validation_happens_before_file_open(self):
        with patch.object(integration_client, "private_parent", side_effect=ValueError("link rejected")), \
             patch.object(integration_client.os, "open") as opened:
            with self.assertRaisesRegex(ValueError, "link rejected"):
                integration_client.private_write(Path("synthetic-link.json"), "{}")
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()

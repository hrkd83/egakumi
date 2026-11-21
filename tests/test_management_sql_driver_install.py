"""固定ODBC packageをTLS・hash・所有者境界を越えずに導入する合成検査。"""
import ast
import hashlib
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'infra/guest/prepare-management-client.sh'


def runtime():
    block = SCRIPT.read_text(encoding='utf-8').split("<<'PY_PREPARE'\n", 1)[1].split('\nPY_PREPARE', 1)[0]
    return next(ast.literal_eval(node.value) for node in ast.parse(block).body
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                    and target.id == 'runtime_odbc' for target in node.targets))


class ManagementSqlDriverInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='synthetic-driver-install-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'synthetic.deb'
        self.path.write_bytes(b'synthetic-existing-private-package')
        self.path.chmod(0o600)
        self.package = b'synthetic-independent-package-body'
        self.network = mock.Mock()
        self.network.open.return_value = io.BytesIO(self.package)
        self.script = runtime().split("<<'PY_ODBC'\n", 1)[1].split('\nPY_ODBC', 1)[0]
        # Use an independent fixture digest to exercise integrity handling;
        # actual driver bytes are not downloaded or installed in these tests.
        self.script = self.script.replace('f506cb3de8b34cd94e9014d247b39e9dfd7effad79edad3ab67096dc3c1b6b0e', hashlib.sha256(self.package).hexdigest())

    def run_probe(self, *, mode=0o600, uid=0):
        actual = self.path.lstat()
        info = SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=uid, st_dev=actual.st_dev, st_ino=actual.st_ino)
        with mock.patch('urllib.request.build_opener', return_value=self.network), mock.patch.object(sys, 'argv', ['synthetic-probe', str(self.path)]), \
             mock.patch.object(os, 'lstat', return_value=info), mock.patch.object(os, 'O_NOFOLLOW', getattr(os, 'O_NOFOLLOW', 0), create=True):
            exec(compile(self.script, 'synthetic-driver-probe', 'exec'), {'__name__': 'synthetic_probe'})

    def test_verified_body_replaces_only_the_private_package_file(self):
        self.run_probe()
        self.assertEqual(self.path.read_bytes(), self.package)
        request = self.network.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://packages.microsoft.com/ubuntu/24.04/prod/pool/main/m/msodbcsql18/msodbcsql18_18.7.1.1-1_amd64.deb')
        self.assertEqual(self.network.open.call_args.kwargs['timeout'], 60)

    def test_hash_or_size_failure_preserves_existing_private_package(self):
        before = self.path.read_bytes()
        for body in (b'synthetic-tampered-body', b'x' * (8 * 1024 * 1024 + 1)):
            self.network.open.return_value = io.BytesIO(body)
            with self.subTest(size=len(body)), self.assertRaisesRegex(SystemExit, 'hash did not match'):
                self.run_probe()
            self.assertEqual(self.path.read_bytes(), before)

    def test_wrong_owner_or_public_mode_stops_before_downloading(self):
        for mode, uid in ((0o644, 0), (0o600, 1)):
            self.network.reset_mock()
            with self.subTest(mode=mode, uid=uid), self.assertRaisesRegex(SystemExit, 'private regular file'):
                self.run_probe(mode=mode, uid=uid)
            self.network.open.assert_not_called()

    def test_official_version_os_and_dependency_install_contract_is_fixed(self):
        shell = runtime()
        self.assertIn('${ID} == ubuntu && ${VERSION_ID} == 24.04', shell)
        self.assertIn('$(dpkg --print-architecture) == amd64', shell)
        self.assertIn('18.7.1.1-1', shell)
        self.assertIn('ACCEPT_EULA=Y DEBIAN_FRONTEND=noninteractive apt-get', shell)
        self.assertIn('ProxyHandler({}),NoRedirect()', shell)
        self.assertIn('os.O_NOFOLLOW', shell)
        self.assertNotIn('curl ', shell)
        self.assertNotIn('CERT_NONE', shell)
        self.assertNotIn('check_hostname=False', shell)


if __name__ == '__main__': unittest.main()

"""Run the guest installer storage stage with synthetic files and mocked IMDS."""
import ast
import base64
import copy
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import test_management_client_assets_bicep as fixtures


ROOT = fixtures.ROOT
SCRIPT = ROOT / 'infra/guest/prepare-management-client.sh'


class ManagementClientInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='synthetic-client-install-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'maintenance'
        self.inventory = fixtures.ManagementClientBicepTests().fixture()[2]
        self.source = (ROOT / 'scripts/management_client.py').read_bytes()
        self.requirements = (ROOT / 'scripts/maintenance-requirements.txt').read_bytes()
        self.unsafe = set()
        self.bad_owners = set()
        self.bad_modes = {}
        self.network = mock.Mock()
        self.runtime = mock.Mock()
        self.runtime_links = set()
        self.runtime_pip_ready = set()
        self.runtime_builds = []
        self.locked = False
        self.lock_events = []

    def prepare(self, inventory, *, observed_vm=None, fail_at=None, runtime_callback=None):
        block = SCRIPT.read_text(encoding='utf-8').split("<<'PY_PREPARE'\n", 1)[1].split('\nPY_PREPARE', 1)[0]
        root = self.root
        self_fixture = self
        unsafe, bad_owners, bad_modes = self.unsafe, self.bad_owners, self.bad_modes
        platform_path = type(Path())
        class InstallerPath(platform_path):
            def __new__(cls, *parts):
                if parts == ('/opt/core/maintenance',): parts = (str(root),)
                # Path.__new__ checks its module's global Path name. Replacing
                # that factory must not make it return an uninitialized base
                # PosixPath on Python 3.12 instead of this synthetic subclass.
                if platform_path.__init__ is object.__init__:
                    return cls._from_parts(parts)
                return object.__new__(cls)

            def __init__(self, *parts):
                if parts == ('/opt/core/maintenance',): parts = (str(root),)
                if platform_path.__init__ is object.__init__:
                    super().__init__()
                else:
                    super().__init__(*parts)

            def stat(self, *args, **kwargs):
                actual = super().stat(*args, **kwargs)
                # Simulate the management VM's root owner. Native permission
                # enforcement belongs to the Linux client integration tests.
                mode = actual.st_mode
                if os.name == 'nt': mode = stat.S_IFMT(mode) | (0o700 if stat.S_ISDIR(mode) else 0o600)
                # Synthetic temp ancestors stand in for the protected /opt
                # hierarchy, including when the host temp directory is /tmp.
                if self != root and self in root.parents:
                    mode &= ~0o022
                if str(self) in bad_modes: mode = stat.S_IFMT(mode) | bad_modes[str(self)]
                return SimpleNamespace(st_uid=1 if str(self) in bad_owners else 0, st_mode=mode)

            def is_symlink(self):
                return str(self) in unsafe or str(self) in self_fixture.runtime_links or super().is_symlink()

            def resolve(self, *args, **kwargs):
                if str(self) in self_fixture.runtime_links:
                    return InstallerPath(sys.executable).resolve(*args, **kwargs)
                return super().resolve(*args, **kwargs)

        environment = {
            'CORE_MAINTENANCE_SOURCE_B64': base64.b64encode(self.source).decode(),
            'CORE_MAINTENANCE_REQUIREMENTS_B64': base64.b64encode(self.requirements).decode(),
            'CORE_MAINTENANCE_INVENTORY_B64': base64.b64encode(json.dumps(inventory).encode()).decode()}
        observed_vm = inventory['managementVmId'] if observed_vm is None else observed_vm
        self.network.open.return_value = io.BytesIO(json.dumps({'resourceId': observed_vm}).encode())
        original_open, original_fstat, original_replace, original_close = os.open, os.fstat, os.replace, os.close
        opened = {}
        locked_descriptors = set()
        fcntl = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8)

        def flock(fd, operation):
            if operation == fcntl.LOCK_UN:
                self.locked = False
                self.lock_events.append('released')
            else:
                if self.locked: raise BlockingIOError('Synthetic active preparation')
                self.locked = True
                locked_descriptors.add(fd)
                self.lock_events.append('acquired')

        fcntl.flock = flock

        def open_file(path, flags, mode=0o777, *, dir_fd=None):
            if (fail_at == ('staging', Path(path).name)
                    and Path(path).parent.name.startswith('.prepare-stage-') and flags & os.O_CREAT):
                raise OSError('Synthetic interrupted staging write')
            fd = original_open(path, flags, mode, dir_fd=dir_fd)
            opened[fd] = str(path)
            return fd

        def file_stat(fd):
            actual = original_fstat(fd)
            mode = actual.st_mode
            if os.name == 'nt': mode = stat.S_IFMT(mode) | 0o600
            if opened.get(fd) in bad_modes: mode = stat.S_IFMT(mode) | bad_modes[opened[fd]]
            return SimpleNamespace(st_uid=1 if opened.get(fd) in bad_owners else 0, st_mode=mode)

        def close_file(fd):
            original_close(fd)
            if fd in locked_descriptors:
                locked_descriptors.remove(fd)
                self.locked = False
                self.lock_events.append('released')

        def replace_file(source, target):
            original_replace(source, target)
            if fail_at == ('replace', Path(target).name) and Path(target).parent == root:
                raise OSError('Synthetic interruption immediately after atomic replacement')

        def run_runtime(*args, **kwargs):
            self.assertTrue(self.locked, 'Guest lock must remain held through package preparation')
            if fail_at == ('runtime', ''):
                raise subprocess.CalledProcessError(1, ['/bin/bash'])
            if runtime_callback is not None: runtime_callback()
            if 'python3 -m venv' in args[0][2]:
                venv = root / 'venv'
                executable = venv / 'bin/python'
                if not executable.exists() or not (venv / 'pyvenv.cfg').exists() or str(root) not in self.runtime_pip_ready:
                    for name in ('bin', 'lib', 'include'):
                        (venv / name).mkdir(mode=0o700, exist_ok=True)
                    executable.write_bytes(b'Synthetic system Python link')
                    executable.chmod(0o700)
                    self.runtime_links.add(str(executable))
                    configuration = venv / 'pyvenv.cfg'
                    configuration.write_bytes(b'Synthetic private runtime configuration')
                    configuration.chmod(0o600)
                    self.runtime_pip_ready.add(str(root))
                    self.runtime_builds.append(str(root))
            return self.runtime(*args, **kwargs)

        with mock.patch('pathlib.Path', InstallerPath), mock.patch('urllib.request.build_opener', return_value=self.network), \
             mock.patch.object(os, 'environ', environment), mock.patch.object(os, 'O_NOFOLLOW', getattr(os, 'O_NOFOLLOW', 0), create=True), \
             mock.patch.dict(sys.modules, {'fcntl': fcntl}), mock.patch.object(os, 'open', side_effect=open_file), \
             mock.patch.object(os, 'fstat', side_effect=file_stat), mock.patch.object(os, 'replace', side_effect=replace_file), \
             mock.patch.object(os, 'close', side_effect=close_file), \
             mock.patch.object(subprocess, 'run', side_effect=run_runtime), mock.patch('sys.stdout', new=io.StringIO()):
            exec(compile(block, 'synthetic-guest-install', 'exec'), {'__name__': 'synthetic_install'})

    def test_storage_stage_installs_the_exact_source_and_inventory_without_tokens(self):
        self.prepare(self.inventory)
        self.assertEqual((self.root / 'management_client.py').read_bytes(), self.source)
        self.assertEqual((self.root / 'requirements.txt').read_bytes(), self.requirements)
        self.assertEqual(json.loads((self.root / 'inventory.json').read_text(encoding='utf-8')), self.inventory)
        self.network.open.assert_called_once()
        request = self.network.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01')
        self.assertEqual(request.get_header('Metadata'), 'true')
        self.assertIsNone(request.get_header('Authorization'))
        self.assertFalse(any(path.suffix == '.pyc' for path in self.root.rglob('*')))
        self.assertEqual(len(self.runtime.call_args.kwargs['pass_fds']), 1)

    def test_repeated_core_and_app_stages_only_add_constructed_resource_ids(self):
        self.prepare(self.inventory)
        added = copy.deepcopy(self.inventory)
        base = added['environments'][0].rsplit('/managedEnvironments/', 1)[0]
        added['applications'].append(base + '/containerApps/ca-test-fixture-credentials')
        added['jobs'] = [base + '/jobs/caj-test-fixture-schema', base + '/jobs/caj-test-fixture-recon']
        self.prepare(added)
        self.prepare(self.inventory)
        saved = json.loads((self.root / 'inventory.json').read_text(encoding='utf-8'))
        for key in ('applications', 'jobs', 'environments'):
            self.assertEqual(saved[key], sorted(set(added[key])))

    def test_interrupted_first_commit_retries_with_the_same_vm_identity_and_targets(self):
        with self.assertRaises(OSError):
            self.prepare(self.inventory, fail_at=('replace', 'management_client.py'))
        self.assertTrue((self.root / '.owner.json').is_file())
        self.assertTrue((self.root / 'management_client.py').is_file())
        self.assertFalse((self.root / 'inventory.json').exists())
        self.assertFalse(self.locked)
        self.prepare(self.inventory)
        self.assertEqual(json.loads((self.root / 'inventory.json').read_text(encoding='utf-8')), self.inventory)
        self.assertEqual((self.root / 'management_client.py').read_bytes(), self.source)
        self.assertEqual((self.root / 'requirements.txt').read_bytes(), self.requirements)

    def test_staging_and_each_atomic_commit_interruption_retry_without_losing_constructed_app_ids(self):
        for index, failure in enumerate((('staging', 'requirements.txt'), ('replace', 'management_client.py'),
                                        ('replace', 'requirements.txt'), ('replace', 'inventory.json'), ('runtime', ''))):
            self.root = Path(self.directory.name) / ('maintenance-' + str(index))
            self.prepare(self.inventory)
            added = copy.deepcopy(self.inventory)
            base = added['environments'][0].rsplit('/managedEnvironments/', 1)[0]
            added['jobs'] = [base + '/jobs/caj-test-fixture-schema']
            with self.subTest(failure=failure), self.assertRaises((OSError, subprocess.CalledProcessError)):
                self.prepare(added, fail_at=failure)
            self.assertFalse(self.locked)
            self.prepare(added)
            self.prepare(self.inventory)
            saved = json.loads((self.root / 'inventory.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['jobs'], added['jobs'])
            self.assertEqual(saved['applications'], self.inventory['applications'])
            self.assertEqual((self.root / 'management_client.py').read_bytes(), self.source)
            self.assertEqual((self.root / 'requirements.txt').read_bytes(), self.requirements)

    def test_owned_retry_ignores_old_staging_and_fixed_new_names_without_deleting_unknown_files(self):
        self.prepare(self.inventory)
        unknown = self.root / 'unmanaged-note'
        unknown.write_bytes(b'Synthetic data must be preserved')
        stale = self.root / '.management_client.py.new'
        stale.write_bytes(b'Synthetic legacy temporary data')
        old_stage = self.root / '.prepare-stage-11111111-1111-4111-8111-111111111111'
        old_stage.mkdir(mode=0o700)
        (old_stage / 'unknown').write_bytes(b'Synthetic incomplete stage')
        self.unsafe.add(str(old_stage))
        self.prepare(self.inventory)
        self.assertEqual(unknown.read_bytes(), b'Synthetic data must be preserved')
        self.assertEqual(stale.read_bytes(), b'Synthetic legacy temporary data')
        self.assertEqual((old_stage / 'unknown').read_bytes(), b'Synthetic incomplete stage')

    def test_unstamped_nonempty_directory_or_different_vm_identity_is_not_adopted(self):
        self.root.mkdir(mode=0o700)
        unknown = self.root / 'unmanaged-note'
        unknown.write_bytes(b'Synthetic unmanaged data')
        with self.assertRaisesRegex(SystemExit, 'unmanaged'):
            self.prepare(self.inventory)
        self.assertEqual(unknown.read_bytes(), b'Synthetic unmanaged data')
        self.assertFalse((self.root / '.owner.json').exists())
        self.root = Path(self.directory.name) / 'another-maintenance'
        self.prepare(self.inventory)
        altered = copy.deepcopy(self.inventory)
        altered['managementPrincipalId'] = '55555555-5555-4555-8555-555555555555'
        before = (self.root / 'inventory.json').read_bytes()
        with self.assertRaisesRegex(SystemExit, 'ownership'):
            self.prepare(altered)
        self.assertEqual((self.root / 'inventory.json').read_bytes(), before)

    def test_active_preparation_fails_closed_and_keeps_lock_through_runtime(self):
        self.prepare(self.inventory)
        baseline = (self.root / 'inventory.json').read_bytes()
        self.locked = True
        try:
            with self.assertRaisesRegex(SystemExit, 'active'):
                self.prepare(self.inventory)
            self.assertTrue(self.locked, 'A rejected contender cannot release the active owner lock')
            self.assertEqual((self.root / 'inventory.json').read_bytes(), baseline)
        finally:
            self.locked = False

        def contender():
            with self.assertRaisesRegex(SystemExit, 'active'):
                self.prepare(self.inventory)
            self.assertTrue(self.locked)

        self.prepare(self.inventory, runtime_callback=contender)
        self.assertFalse(self.locked)
        self.assertEqual(self.lock_events[-2:], ['acquired', 'released'])

    def test_lock_and_ownership_stamp_must_be_private_owned_regular_files(self):
        self.prepare(self.inventory)
        before = (self.root / 'inventory.json').read_bytes()
        for name in ('.prepare.lock', '.owner.json'):
            target = self.root / name
            for invalid in ('link', 'owner', 'mode'):
                if invalid == 'link': self.unsafe.add(str(target))
                elif invalid == 'owner': self.bad_owners.add(str(target))
                else: self.bad_modes[str(target)] = 0o644
                with self.subTest(name=name, invalid=invalid), self.assertRaises(SystemExit):
                    self.prepare(self.inventory)
                self.unsafe.clear()
                self.bad_owners.clear()
                self.bad_modes.clear()
                self.assertEqual((self.root / 'inventory.json').read_bytes(), before)

    def test_unprotected_directory_ancestor_stops_without_replacing_assets(self):
        self.prepare(self.inventory)
        before = (self.root / 'inventory.json').read_bytes()
        ancestor = self.root.parent
        for invalid in ('link', 'owner', 'mode'):
            if invalid == 'link': self.unsafe.add(str(ancestor))
            elif invalid == 'owner': self.bad_owners.add(str(ancestor))
            else: self.bad_modes[str(ancestor)] = 0o777
            with self.subTest(invalid=invalid), self.assertRaises(SystemExit):
                self.prepare(self.inventory)
            self.unsafe.clear()
            self.bad_owners.clear()
            self.bad_modes.clear()
            self.assertEqual((self.root / 'inventory.json').read_bytes(), before)

    def test_owned_incomplete_runtime_is_repaired_and_unknown_runtime_is_preserved(self):
        self.prepare(self.inventory)
        venv = self.root / 'venv'
        note = venv / 'unmanaged-note'
        note.write_bytes(b'Synthetic runtime note must remain unchanged')
        self.runtime_pip_ready.remove(str(self.root))
        before = len(self.runtime_builds)
        self.prepare(self.inventory)
        self.assertEqual(len(self.runtime_builds), before + 1)
        self.assertIn(str(self.root), self.runtime_pip_ready)
        self.assertEqual(note.read_bytes(), b'Synthetic runtime note must remain unchanged')
        (venv / 'pyvenv.cfg').unlink()
        before = len(self.runtime_builds)
        self.prepare(self.inventory)
        self.assertEqual(len(self.runtime_builds), before + 1)
        self.assertTrue((venv / 'pyvenv.cfg').is_file())
        owner = self.root / '.runtime-owner.json'
        owner.unlink()
        with self.assertRaisesRegex(SystemExit, 'unmanaged maintenance runtime'):
            self.prepare(self.inventory)
        self.assertFalse(owner.exists())
        self.assertEqual(note.read_bytes(), b'Synthetic runtime note must remain unchanged')

    def test_runtime_directory_and_receipt_reject_links_wrong_owner_and_public_modes(self):
        self.prepare(self.inventory)
        for name in ('venv', 'venv/bin', '.runtime-owner.json'):
            target = self.root / name
            for invalid in ('link', 'owner', 'mode'):
                if invalid == 'link': self.unsafe.add(str(target))
                elif invalid == 'owner': self.bad_owners.add(str(target))
                else: self.bad_modes[str(target)] = 0o755 if name != '.runtime-owner.json' else 0o644
                with self.subTest(name=name, invalid=invalid), self.assertRaises(SystemExit):
                    self.prepare(self.inventory)
                self.unsafe.clear()
                self.bad_owners.clear()
                self.bad_modes.clear()

    @unittest.skipUnless(os.name == 'posix', 'The guest venv repair needs the Linux runtime')
    def test_posix_real_venv_without_pip_recovers_using_only_local_system_wheels(self):
        venv = Path(self.directory.name) / 'synthetic-runtime'
        subprocess.run([sys.executable, '-I', '-B', '-m', 'venv', '--without-pip', str(venv)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        executable = venv / 'bin/python'
        before = executable.resolve()
        self.assertNotEqual(subprocess.run([str(executable), '-I', '-B', '-m', 'pip', '--version'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode, 0)
        note = venv / 'unmanaged-note'
        note.write_bytes(b'Synthetic runtime note must be preserved')
        commands = Path(self.directory.name) / 'synthetic-commands'
        commands.mkdir(mode=0o700)
        apt = commands / 'apt-get'
        apt.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        apt.chmod(0o700)
        block = SCRIPT.read_text(encoding='utf-8').split("<<'PY_PREPARE'\n", 1)[1].split('\nPY_PREPARE', 1)[0]
        runtime = next(ast.literal_eval(node.value) for node in ast.parse(block).body
                       if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                          and target.id == 'runtime_prepare' for target in node.targets))
        runtime = runtime.replace('/opt/core/maintenance/venv', shlex.quote(str(venv)))
        subprocess.run(['/bin/bash', '-c', runtime], check=True,
                       env={**os.environ, 'PATH': str(commands) + ':/usr/bin:/bin'},
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([str(executable), '-I', '-B', '-m', 'pip', '--version'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for missing in (True, False):
            if missing: (venv / 'pyvenv.cfg').unlink()
            else: (venv / 'pyvenv.cfg').write_bytes(b'')
            subprocess.run(['/bin/bash', '-c', runtime], check=True,
                           env={**os.environ, 'PATH': str(commands) + ':/usr/bin:/bin'},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([str(executable), '-I', '-B', '-c',
                'import sys; assert sys.prefix != sys.base_prefix'],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([str(executable), '-I', '-B', '-m', 'pip', '--version'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(executable.resolve(), before)
        self.assertEqual(note.read_bytes(), b'Synthetic runtime note must be preserved')

    @unittest.skipUnless(os.name == 'posix', 'The inherited guest flock needs the Linux runtime')
    def test_posix_child_retains_lock_after_parent_reference_closes(self):
        import fcntl
        path = Path(self.directory.name) / 'synthetic-inherited-lock'
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        contender = None
        child = None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            child = subprocess.Popen([sys.executable, '-I', '-B', '-c',
                "import sys; print('ready', flush=True); sys.stdin.readline()"],
                pass_fds=(fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            os.close(fd)
            fd = None
            contender = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            child.stdin.write('\n')
            child.stdin.flush()
            child.wait(timeout=5)
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                child.stdin.close()
                child.stdout.close()
            if contender is not None: os.close(contender)
            if fd is not None: os.close(fd)

    def test_foreign_target_or_host_inventory_is_rejected_before_metadata_or_files(self):
        for value in ('https://example.invalid/another-app', '/subscriptions/99999999-9999-4999-8999-999999999999/resourceGroups/rg-fixture/providers/Microsoft.App/containerApps/ca-fixture'):
            inventory = copy.deepcopy(self.inventory)
            inventory['applications'] = [value]
            self.network.reset_mock()
            with self.subTest(value=value), self.assertRaises((RuntimeError, SystemExit, ValueError)):
                self.prepare(inventory)
            self.network.open.assert_not_called()
            self.assertFalse(any(self.root.iterdir()))

    def test_another_vm_or_changed_workspace_stops_without_replacing_inventory(self):
        self.prepare(self.inventory)
        before = (self.root / 'inventory.json').read_bytes()
        with self.assertRaises((RuntimeError, SystemExit, ValueError)):
            self.prepare(self.inventory, observed_vm='/synthetic/another-vm')
        altered = copy.deepcopy(self.inventory)
        altered['workspace']['customerId'] = '55555555-5555-4555-8555-555555555555'
        with self.assertRaises((RuntimeError, SystemExit, ValueError)): self.prepare(altered)
        self.assertEqual((self.root / 'inventory.json').read_bytes(), before)

    def test_invalid_prior_inventory_is_not_retained_in_a_new_core_or_app_update(self):
        self.prepare(self.inventory)
        previous = copy.deepcopy(self.inventory)
        previous['jobs'] = ['https://example.invalid/unapproved-job']
        target = self.root / 'inventory.json'
        target.write_text(json.dumps(previous), encoding='utf-8')
        target.chmod(0o600)
        before = target.read_bytes()
        with self.assertRaises((RuntimeError, SystemExit, ValueError)): self.prepare(self.inventory)
        self.assertEqual(target.read_bytes(), before)

    def test_links_and_unowned_files_are_never_replaced(self):
        self.prepare(self.inventory)
        target = self.root / 'management_client.py'
        self.unsafe.add(str(target))
        with self.assertRaises((RuntimeError, SystemExit, ValueError)): self.prepare(self.inventory)
        self.unsafe.clear()
        self.bad_owners.add(str(target))
        with self.assertRaises((RuntimeError, SystemExit, ValueError)): self.prepare(self.inventory)
        self.assertEqual(target.read_bytes(), self.source)

    def test_package_install_uses_only_official_pypi_without_environment_overrides(self):
        script = SCRIPT.read_text(encoding='utf-8')
        command = next(line for line in script.splitlines() if '-m pip --isolated install' in line)
        self.assertIn('PIP_CONFIG_FILE=/dev/null', command)
        self.assertIn('-I -B -m pip --isolated install --index-url https://pypi.org/simple', command)
        self.assertIn('--no-deps', command)
        self.assertIn('--no-cache-dir', command)
        self.assertNotIn('curl ', script)
        self.assertNotIn('az login', script)


if __name__ == '__main__': unittest.main()

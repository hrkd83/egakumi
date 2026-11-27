#!/usr/bin/env bash
# 管理VM内で保守担当者本人が使うclientを準備します。認証tokenや本文は取得しません。
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
export CORE_MAINTENANCE_SOURCE_B64='__CLIENT_SOURCE_BASE64__'
export CORE_MAINTENANCE_REQUIREMENTS_B64='__CLIENT_REQUIREMENTS_BASE64__'
export CORE_MAINTENANCE_INVENTORY_B64='__CLIENT_INVENTORY_BASE64__'
[[ $(id -u) == 0 ]]
getent -s files group core-maintenance >/dev/null || groupadd --system core-maintenance || getent -s files group core-maintenance >/dev/null
CORE_MAINTENANCE_GID=$(getent -s files group core-maintenance | cut -d: -f3)
export CORE_MAINTENANCE_GID
python3 -I -B - <<'PY_PREPARE'
import base64, fcntl, json, os, stat, subprocess, sys, uuid
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

runtime_prepare = r'''
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
if [[ ! -f /opt/core/maintenance/venv/pyvenv.cfg ]] || [[ ! -x /opt/core/maintenance/venv/bin/python ]] || ! /opt/core/maintenance/venv/bin/python -I -B -c 'import sys; assert sys.prefix == "/opt/core/maintenance/venv" and sys.prefix != sys.base_prefix' >/dev/null 2>&1 || ! /opt/core/maintenance/venv/bin/python -I -B -m pip --version >/dev/null 2>&1; then
    apt-get -o DPkg::Lock::Timeout=300 update >/dev/null 2>&1
    apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends python3-venv >/dev/null 2>&1
    python3 -m venv /opt/core/maintenance/venv >/dev/null 2>&1
fi
'''

runtime_azure_cli = r'''
set -euo pipefail
umask 077
source /etc/os-release
[[ ${ID} == ubuntu && ${VERSION_ID} == 24.04 && $(dpkg --print-architecture) == amd64 ]]
work=$(mktemp -d /opt/core/maintenance/.azure-cli-XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
azure_cli_expected='2.90.0'
azure_cli_package='2.90.0-1~noble'
azure_cli_current=''
if command -v az >/dev/null 2>&1; then
    # versionはAzureへ接続しません。初期化用の設定も一時領域へ閉じ込め、
    # 人のログイン情報を作成・取得したり、VMのMIでログインしたりしません。
    azure_cli_current=$(AZURE_CONFIG_DIR="$work/azure-cli-inspection" AZURE_CORE_COLLECT_TELEMETRY=0 \
        az version --query '"azure-cli"' --output tsv --only-show-errors 2>/dev/null) || azure_cli_current=''
fi
if [ "$azure_cli_current" != "$azure_cli_expected" ]; then
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 update >/dev/null 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends ca-certificates curl gnupg >/dev/null 2>&1
    # Azure CLIにはPowerShellとは別の公式APTリポジトリが必要です。
    # 鍵をこのリポジトリだけのSigned-Byへ指定し、署名検証を無効にはしません。
    # Noble/amd64は冒頭で確認済みなので、別OSのパッケージへ置き換えて続行しません。
    curl --proto '=https' --tlsv1.2 -fsSL --retry 3 \
        https://packages.microsoft.com/keys/microsoft.asc \
        -o "$work/microsoft.asc" >/dev/null 2>&1
    gpg --batch --yes --dearmor --output "$work/microsoft.gpg" "$work/microsoft.asc" >/dev/null 2>&1
    install -d -m 0755 /etc/apt/keyrings
    install -m 0644 "$work/microsoft.gpg" /etc/apt/keyrings/egakumi-azure-cli.gpg
    cat > "$work/azure-cli.sources" <<'AZURE_CLI_REPOSITORY'
Types: deb
URIs: https://packages.microsoft.com/repos/azure-cli/
Suites: noble
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/egakumi-azure-cli.gpg
AZURE_CLI_REPOSITORY
    install -m 0644 "$work/azure-cli.sources" /etc/apt/sources.list.d/egakumi-azure-cli.sources
    apt-get -o DPkg::Lock::Timeout=300 update >/dev/null 2>&1
    apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends --allow-downgrades \
        "azure-cli=$azure_cli_package" >/dev/null 2>&1
    azure_cli_current=$(AZURE_CONFIG_DIR="$work/azure-cli-inspection" AZURE_CORE_COLLECT_TELEMETRY=0 \
        az version --query '"azure-cli"' --output tsv --only-show-errors 2>/dev/null)
    [ "$azure_cli_current" = "$azure_cli_expected" ] || exit 1
fi
'''

runtime_install = r'''
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
[[ -x /usr/bin/az ]]
/opt/core/maintenance/venv/bin/python -I -B -c 'import sys; assert sys.prefix == "/opt/core/maintenance/venv" and sys.prefix != sys.base_prefix' >/dev/null
PIP_CONFIG_FILE=/dev/null /opt/core/maintenance/venv/bin/python -I -B -m pip --isolated install --index-url https://pypi.org/simple --only-binary=:all: --no-deps --disable-pip-version-check --no-cache-dir -r /opt/core/maintenance/requirements.txt >/dev/null 2>&1
/opt/core/maintenance/venv/bin/python -I -B -c 'import pyodbc; assert "ODBC Driver 18 for SQL Server" in pyodbc.drivers()' >/dev/null 2>&1
/opt/core/maintenance/venv/bin/python -I -B /opt/core/maintenance/management_client.py --help >/dev/null
'''

runtime_odbc = r'''
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
# The supported management image is Ubuntu 24.04 amd64. This package and hash
# are from Microsoft's official Ubuntu 24.04 package index, not a floating repo.
source /etc/os-release
[[ ${ID} == ubuntu && ${VERSION_ID} == 24.04 && $(dpkg --print-architecture) == amd64 ]]
if [[ $(dpkg-query -W -f='${Version}' msodbcsql18 2>/dev/null || true) != 18.7.1.1-1 ]]; then
    task_odbc_deb=$(mktemp /opt/core/maintenance/.odbc-driver-XXXXXXXX.deb)
    trap 'rm -f -- "$task_odbc_deb"' EXIT
    python3 -I -B - "$task_odbc_deb" <<'PY_ODBC'
import hashlib,os,stat,sys
from urllib.request import HTTPRedirectHandler,ProxyHandler,Request,build_opener
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RuntimeError('The fixed SQL driver package must not redirect')
path=sys.argv[1]
info=os.lstat(path)
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
    raise SystemExit('The fixed SQL driver package requires a private regular file')
request=Request('https://packages.microsoft.com/ubuntu/24.04/prod/pool/main/m/msodbcsql18/msodbcsql18_18.7.1.1-1_amd64.deb')
with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=60) as response:
    data=response.read(8*1024*1024+1)
if len(data)>8*1024*1024 or hashlib.sha256(data).hexdigest() != 'f506cb3de8b34cd94e9014d247b39e9dfd7effad79edad3ab67096dc3c1b6b0e':
    raise SystemExit('The fixed SQL driver package hash did not match')
fd=os.open(path,os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW)
with os.fdopen(fd,'wb') as stream:
    actual=os.fstat(stream.fileno())
    if (actual.st_dev,actual.st_ino) != (info.st_dev,info.st_ino):
        raise SystemExit('The fixed SQL driver package changed while opening')
    stream.write(data)
    stream.flush()
    os.fsync(stream.fileno())
PY_ODBC
    apt-get -o DPkg::Lock::Timeout=300 update >/dev/null 2>&1
    ACCEPT_EULA=Y DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y --allow-downgrades --no-install-recommends "$task_odbc_deb" >/dev/null 2>&1
    [[ $(dpkg-query -W -f='${Version}' msodbcsql18) == 18.7.1.1-1 ]]
fi
'''


operator_gid = int(os.environ['CORE_MAINTENANCE_GID'])
if operator_gid <= 0:
    raise SystemExit('A separate maintenance group is required')


def private_node(path, *, directory=False, operator_read=False):
    if path.is_symlink():
        raise SystemExit('Maintenance paths must not be links')
    info = path.stat()
    mode = 0o700 if directory else 0o600
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    actual = stat.S_IMODE(info.st_mode)
    shared = operator_read and actual == (0o750 if directory else 0o640) and info.st_gid == operator_gid
    if info.st_uid != 0 or not kind(info.st_mode) or (actual != mode and not shared):
        raise SystemExit('Maintenance paths must be root-owned and private')


def private_fd(fd, *, operator_read=False):
    info = os.fstat(fd)
    mode = stat.S_IMODE(info.st_mode)
    shared = operator_read and mode == 0o640 and info.st_gid == operator_gid
    if info.st_uid != 0 or (mode != 0o600 and not shared) or not stat.S_ISREG(info.st_mode):
        raise SystemExit('Maintenance files must be root-owned and private')


def read_private(path, *, operator_read=False):
    private_node(path, operator_read=operator_read)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        private_fd(fd, operator_read=operator_read)
        with os.fdopen(fd, 'rb') as handle:
            fd = None
            return handle.read()
    finally:
        if fd is not None:
            os.close(fd)


def write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        private_fd(fd)
        with os.fdopen(fd, 'wb') as output:
            fd = None
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if fd is not None:
            os.close(fd)


def sync_directory(path):
    if hasattr(os, 'O_DIRECTORY'):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def verify_runtime(venv, *, prepared=False):
    for node in [venv, *(venv / name for name in ('bin', 'lib', 'include'))]:
        if node.exists() or node.is_symlink():
            private_node(node, directory=True, operator_read=True)
    library = venv / 'lib64'
    if library.is_symlink():
        if library.resolve(strict=True) != (venv / 'lib').resolve(strict=True):
            raise SystemExit('The maintenance runtime library link is unexpected')
    elif library.exists():
        private_node(library, directory=True, operator_read=True)
    executable = venv / 'bin/python'
    if executable.exists() or executable.is_symlink():
        target = executable.resolve(strict=True)
        if target != Path(sys.executable).resolve(strict=True):
            raise SystemExit('The maintenance runtime interpreter is unexpected')
        info = target.stat()
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o022:
            raise SystemExit('The maintenance runtime interpreter must be root-owned and protected')
    elif prepared:
        raise SystemExit('The maintenance runtime interpreter was not prepared')
    configuration = venv / 'pyvenv.cfg'
    if configuration.exists() or configuration.is_symlink():
        private_node(configuration, operator_read=True)
    elif prepared:
        raise SystemExit('The maintenance runtime configuration was not prepared')


def publish_runtime_permissions(venv):
    """保守担当者は依存と実行入口を読み取るだけ。更新はrootの配備で行う。"""
    interpreter = Path(sys.executable).resolve(strict=True)
    lib = venv / 'lib'
    python_lib = lib / f'python{sys.version_info.major}.{sys.version_info.minor}'
    packages = python_lib / 'site-packages'
    executable = venv / 'bin/python'
    nodes = [venv, executable.parent, executable, venv / 'pyvenv.cfg', lib]
    if python_lib.exists() or python_lib.is_symlink():
        private_node(python_lib, directory=True, operator_read=True)
        private_node(packages, directory=True, operator_read=True)
        nodes.extend((python_lib, packages))
        for directory, directories, files in os.walk(packages, followlinks=False):
            nodes.extend(Path(directory) / name for name in directories + files)
    for node in nodes:
        if node.is_symlink():
            if node == executable and node.resolve(strict=True) == interpreter:
                continue
            raise SystemExit('The maintenance runtime contains an unexpected link')
        info = node.stat()
        if (info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
            raise SystemExit('The maintenance runtime contains an unprotected node')
        mode = 0o750 if stat.S_ISDIR(info.st_mode) or info.st_mode & stat.S_IXUSR else 0o640
        os.chown(node, 0, operator_gid, follow_symlinks=False)
        node.chmod(mode)


root = Path('/opt/core/maintenance')
lock_fd = None
stage = 'storage'
try:
    for node in [root, *root.parents]:
        if node.is_symlink():
            raise SystemExit('Maintenance directories must not be links')
        if node.exists():
            info = node.stat()
            if info.st_uid != 0 or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o022:
                raise SystemExit('Maintenance directory ancestors must be root-owned and protected')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_node(root, directory=True, operator_read=True)
    source = base64.b64decode(os.environ['CORE_MAINTENANCE_SOURCE_B64'], validate=True)
    requirements = base64.b64decode(os.environ['CORE_MAINTENANCE_REQUIREMENTS_B64'], validate=True)
    namespace = {'__name__': '_maintenance_install_validation_', '__file__': str(root / 'management_client.py')}
    exec(compile(source, 'management_client.py', 'exec'), namespace)
    inventory = namespace['validate_inventory'](json.loads(base64.b64decode(os.environ['CORE_MAINTENANCE_INVENTORY_B64'], validate=True)))
    opener = build_opener(ProxyHandler({}), namespace['NoRedirect']())
    request = Request('http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01', headers={'Metadata':'true'})
    with opener.open(request, timeout=10) as response:
        compute = json.load(response)
    if str(compute.get('resourceId','')).casefold() != inventory['managementVmId'].casefold():
        raise SystemExit('The client inventory identifies another VM')

    lock_path = root / '.prepare.lock'
    if lock_path.exists() or lock_path.is_symlink():
        private_node(lock_path)
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    private_fd(lock_fd)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Another maintenance preparation is active') from None

    ownership = {'schemaVersion': 1, **{key: inventory[key].casefold() for key in
        ('subscriptionId', 'azureTenantId', 'managementVmId', 'managementPrincipalId')}}
    stamp = root / '.owner.json'
    if stamp.exists() or stamp.is_symlink():
        if json.loads(read_private(stamp)) != ownership:
            raise SystemExit('Maintenance ownership differs from the selected VM and identity')
    else:
        if any(path.name != '.prepare.lock' for path in root.iterdir()):
            raise SystemExit('An unmanaged maintenance directory already exists')
        pending_stamp = root / ('.owner-' + str(uuid.uuid4()) + '.pending')
        write_new(pending_stamp, (json.dumps(ownership, sort_keys=True)+'\n').encode())
        os.replace(pending_stamp, stamp)
        sync_directory(root)

    target = root / 'inventory.json'
    if target.exists() or target.is_symlink():
        previous = namespace['validate_inventory'](json.loads(read_private(target, operator_read=True)))
        for key in ('schemaVersion','subscriptionId','azureTenantId','managementVmId','managementPrincipalId','workspace','auditStorage'):
            if previous.get(key) != inventory.get(key):
                raise SystemExit('Existing inventory identity differs; reconcile before preparing the client')
        # Read and merge only while holding the guest lock. Later App phases
        # cannot overwrite IDs added by another completed preparation.
        for key in ('applications','jobs','environments'):
            inventory[key] = sorted(set(previous[key] + inventory[key]))
        sql = {item['alias']: item for item in previous['sqlDatabases']}
        for item in inventory['sqlDatabases']:
            if item['alias'] in sql and sql[item['alias']]['id'].casefold() != item['id'].casefold():
                raise SystemExit('Existing SQL inventory alias differs; reconcile before preparing the client')
            sql[item['alias']] = item
        inventory['sqlDatabases'] = [sql[alias] for alias in sorted(sql)]
    inventory = namespace['validate_inventory'](inventory)
    contents = {
        'management_client.py': source,
        'requirements.txt': requirements,
        'inventory.json': (json.dumps(inventory, ensure_ascii=False, indent=2)+'\n').encode(),
    }
    # A valid ownership stamp permits retry after an interrupted first commit.
    # Unknown files and old staging directories are never reused or deleted.
    for name in contents:
        path = root / name
        if path.exists() or path.is_symlink():
            private_node(path, operator_read=name != 'requirements.txt')
    temporary = root / ('.prepare-stage-' + str(uuid.uuid4()))
    temporary.mkdir(mode=0o700, exist_ok=False)
    private_node(temporary, directory=True)
    write_new(temporary / '.receipt.json', (json.dumps({'schemaVersion': 1, 'owner': ownership,
        'files': list(contents)}, sort_keys=True)+'\n').encode())
    for name, data in contents.items():
        write_new(temporary / name, data)
    for name, data in contents.items():
        if read_private(temporary / name) != data:
            raise SystemExit('Staged maintenance files differ from the prepared assets')
    namespace['validate_inventory'](json.loads(read_private(temporary / 'inventory.json')))
    sync_directory(temporary)
    # Each replacement is atomic; inventory is the last commit point. Once
    # ownership is established, staged and partial commits are retryable.
    for name in contents:
        if name != 'requirements.txt':
            os.chown(temporary / name, 0, operator_gid, follow_symlinks=False)
            (temporary / name).chmod(0o640)
        os.replace(temporary / name, root / name)
    sync_directory(root)
    for key in ('CORE_MAINTENANCE_SOURCE_B64', 'CORE_MAINTENANCE_REQUIREMENTS_B64', 'CORE_MAINTENANCE_INVENTORY_B64'):
        os.environ.pop(key, None)
    stage = 'runtime'
    venv = root / 'venv'
    runtime_owner = root / '.runtime-owner.json'
    runtime_receipt = {'schemaVersion': 1, 'owner': ownership, 'directory': 'venv',
                       'python': str(Path(sys.executable).resolve(strict=True))}
    if runtime_owner.exists() or runtime_owner.is_symlink():
        if json.loads(read_private(runtime_owner)) != runtime_receipt:
            raise SystemExit('Maintenance runtime ownership differs from the selected VM and interpreter')
    else:
        if venv.exists() or venv.is_symlink():
            raise SystemExit('An unmanaged maintenance runtime already exists')
        pending_runtime = root / ('.runtime-owner-' + str(uuid.uuid4()) + '.pending')
        write_new(pending_runtime, (json.dumps(runtime_receipt, sort_keys=True)+'\n').encode())
        os.replace(pending_runtime, runtime_owner)
        sync_directory(root)
    venv.mkdir(mode=0o700, exist_ok=True)
    verify_runtime(venv)
    # The child inherits the locked descriptor, so it retains the lock even
    # if the RunCommand parent is interrupted before package setup finishes.
    subprocess.run(['/bin/bash', '-c', runtime_prepare], check=True, pass_fds=(lock_fd,),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    verify_runtime(venv, prepared=True)
    subprocess.run(['/bin/bash', '-c', runtime_odbc], check=True, pass_fds=(lock_fd,),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['/bin/bash', '-c', runtime_azure_cli], check=True, pass_fds=(lock_fd,),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['/bin/bash', '-c', runtime_install], check=True, pass_fds=(lock_fd,),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    publish_runtime_permissions(venv)
    os.chown(root, 0, operator_gid, follow_symlinks=False)
    root.chmod(0o750)
    if root.parent == Path('/opt/core'):
        # Operators need to traverse the installer-owned parent, which has no user input.
        os.chown(root.parent, 0, operator_gid, follow_symlinks=False)
        root.parent.chmod(0o710)
    print('{"status":"client-prepared","runtimeVerified":false}')
except BaseException:
    print(json.dumps({'status': 'failed', 'stage': stage}, separators=(',', ':')))
    raise
finally:
    if lock_fd is not None:
        # Closing only this reference preserves an inherited child's lock.
        # An explicit LOCK_UN would also unlock a still-running child.
        os.close(lock_fd)
PY_PREPARE

"""CoreのACA Jobが保存したPortal認証証明書の初期状態を確認する。"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path

from deployment.helpers.configuration import load_configuration
from deployment.helpers.paths import LocalPaths, private_output, private_parent
from deployment.helpers.portal_auth_certificate import read_certificate_state
from deployment.helpers.private_permissions import check_private_permissions, protect_new_path, regular_path
from operations.common import OperationContext, OperationError

ROOT = Path(__file__).resolve().parents[2]


def load_context(config_path, runtime_root=None):
    """読込み前に指定runtimeのリンク・所有者専用権限を確認します。"""
    root = Path(runtime_root).expanduser().absolute() if runtime_root is not None else ROOT
    # resolve()より前に、明示されたroot自体のリンク/reparseを拒否します。
    regular_path(root, directory=True)
    path = private_output(Path(config_path).absolute(), root)
    relative = path.relative_to(root)
    if len(relative.parts) != 3 or relative.parts[0] != '.local' or relative.parts[2] != 'params.local.json':
        raise OperationError('Select the exact protected parameters in the explicit runtime root')
    workspace = LocalPaths(root, relative.parts[1])
    for directory in (root / '.local', workspace.root, workspace.state):
        check_private_permissions(directory, directory=True)
    for target in (workspace.checked(path), workspace.checked(workspace.state / 'latest.json')):
        check_private_permissions(target)
    azure = private_output(root / '.azure/config.json', root)
    if azure.parent.exists():
        check_private_permissions(azure.parent, directory=True)
    if azure.exists():
        check_private_permissions(azure)
    configuration = load_configuration(path)
    if configuration['environmentName'] != workspace.environment:
        raise OperationError('The parameters differ from the selected protected environment')
    return OperationContext.load(path, runtime_root=root)


@contextmanager
def locked(ctx):
    """共通lockを使い、新しい空lockも本文を書込む前に実権限で保護します。"""
    path = ctx.workspace.checked(ctx.workspace.root / '.operations.lock')
    private_parent(path, ctx.workspace.repository)
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError:
        check_private_permissions(path)
    else:
        os.close(descriptor)
        protect_new_path(path)
    check_private_permissions(path)
    with ctx.locked():
        yield


def initialize(ctx, *, execute=False):
    """Coreの発行結果を保護stateから照合する。"""
    if not ctx.state['outputs'].get('certificateBootstrap'):
        raise OperationError('Portal certificate initialization requires Core with the ACA certificate job')
    current = ctx.workspace.checked(ctx.workspace.state / 'portal-auth-certificate.json')
    if not current.is_file():
        raise OperationError('Resume azd up to complete the initial Portal certificate job')
    state = read_certificate_state(ctx.config, ctx.workspace)
    if state.get('phase') != 'initial':
        raise OperationError('Finish the certificate rotation before initialization')
    return {'action': 'initialize', 'phase': 'initial', 'execute': execute, 'resourcesPlanned': 0,
            'privateKeyExport': False, 'entraChangesAutomatic': False}

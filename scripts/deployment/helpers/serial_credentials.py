"""初期管理VMのローカル資格情報を、所有確認済みの保護領域に準備する。

既存VMを読み取れた場合は秘密を読まず、空文字だけを返す。
初期配備の再試行ではpending資格情報を再利用し、配備成功後にvmIdを記録する。
同じ名前でVMを再構築するときは、bound済みの旧秘密を保護保存して新世代を作る。
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import stat
import string
import tempfile
import uuid

from .paths import LocalPaths
from .private_permissions import (
    check_private_permissions as _owner_only, protect_new_path as _protect_new, regular_path as _regular,
)


class SerialCredentialError(RuntimeError):
    """秘密・識別子・外部エラーを公開しない初期資格情報エラー。"""


_FILE_NAME = "management-serial-credential.json"
_LOCK_NAME = ".management-serial-credential.lock"
_REPARSE_POINT = 0x400
_FIELDS = {"schemaVersion", "targetVmResourceId", "adminUsername", "generation", "password", "boundVmId"}
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")

def _storage(workspace: LocalPaths) -> Path:
    """選択環境の.localからsecretsまでを検査し、既存共有ACLは変更しない。"""
    directories = [workspace.repository / ".local", workspace.root, workspace.secrets]
    for directory in directories:
        # checked() rejects another environment and public output paths. The
        # .local root itself is checked against the repository below.
        if not directory.is_relative_to(workspace.repository / ".local"):
            raise SerialCredentialError("Serial credential storage is outside the selected environment")
        try:
            # Numeric Windows 0700 uses a different security descriptor.
            # Create an empty inherited directory, then protect its real DACL
            # before creating any child or generating/writing a credential.
            directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
        except FileExistsError:
            _owner_only(directory, directory=True)
        else:
            _protect_new(directory, directory=True)
    return workspace.checked(workspace.secrets / _FILE_NAME)


def _identity(info: os.stat_result) -> tuple:
    # Windows fstat(native handle) and lstat(path) may expose different ctime
    # meanings. File identity, size, mtime and mode remain comparable; DACLs
    # are independently checked and content is compared before publication.
    changed = info.st_ctime_ns if os.name != "nt" else None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, changed, info.st_mode)


def _open_existing(path: Path, *, write: bool = False) -> int:
    """既存ファイルをリンクとして開かず、本文読取前にdescriptorを検査する。"""
    if os.name != "nt":
        descriptor = os.open(path, (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW)
    else:
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        information = kernel.GetFileInformationByHandle
        information.argtypes = (wintypes.HANDLE, wintypes.LPVOID)
        information.restype = wintypes.BOOL
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        # OPEN_EXISTING + FILE_FLAG_OPEN_REPARSE_POINT opens the node itself,
        # rather than following a replacement symlink/junction before reading.
        handle = create(str(path), 0x80000000 | (0x40000000 if write else 0),
                        0x7, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise SerialCredentialError("The protected Serial credential could not be opened")
        try:
            attributes = (wintypes.DWORD * 13)()
            if not information(handle, ctypes.byref(attributes)) or attributes[0] & _REPARSE_POINT:
                raise SerialCredentialError("Serial credential storage must not use reparse points")
            descriptor = msvcrt.open_osfhandle(handle, (os.O_RDWR if write else os.O_RDONLY) | os.O_BINARY)
            handle = None  # descriptor now owns the Windows handle.
        finally:
            if handle is not None:
                close(handle)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or _identity(info) != _identity(_regular(path)):
            raise SerialCredentialError("The protected Serial credential changed while being opened")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


@contextmanager
def _locked(directory: Path):
    """同じ環境の資格情報の準備とbindingを短い排他区間にする。"""
    path = directory / _LOCK_NAME
    descriptor = None
    try:
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            _owner_only(path)
            descriptor = _open_existing(path, write=True)
        else:
            _protect_new(path)
        if _identity(os.fstat(descriptor)) != _identity(_regular(path)):
            raise SerialCredentialError("Serial credential lock changed while being opened")
        if os.name == "nt":
            import msvcrt
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _owner(configuration: dict) -> tuple[str, str]:
    """初期資材が構築する管理VMと既存管理ユーザーだけを指定する。"""
    target = (f"/subscriptions/{configuration['subscriptionId']}/resourceGroups/"
              f"{configuration['resourceGroupNames']['maint']}/providers/Microsoft.Compute/"
              f"virtualMachines/vm-{configuration['resourceStem']}-mgmt")
    username = configuration["management"]["adminUsername"]
    if (not isinstance(username, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", username)
            or any(c in target for c in ("\\", "%", "?", "#"))
            or any(ord(c) <= 32 for c in target)):
        raise SerialCredentialError("Initial Serial credentials require a validated management VM configuration")
    return target, username


def _owned_vm(configuration: dict, value: dict) -> str:
    target, _ = _owner(configuration)
    if not isinstance(value, dict):
        raise SerialCredentialError("The selected management VM ownership could not be verified")
    tags = value.get("tags") or {}
    if (not isinstance(tags, dict) or str(value.get("id", "")).casefold() != target.casefold()
            or str(value.get("type", "")).casefold() != "microsoft.compute/virtualmachines"
            or tags.get("product") != configuration["productName"]
            or tags.get("environment") != configuration["environmentName"]):
        raise SerialCredentialError("The selected management VM ownership could not be verified")
    return target


def _read_payload(path: Path, *, max_bytes: int = 16384) -> tuple[bytes, tuple]:
    """所有検査した同じ通常ファイルを、最大サイズ付きで一貫して読む。"""
    _owner_only(path)
    descriptor = _open_existing(path)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        data = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    current = _regular(path)
    if (_identity(before) != _identity(after) or _identity(after) != _identity(current)
            or len(data) > max_bytes):
        raise SerialCredentialError("Serial credential storage changed while being read")
    return data, _identity(current)


def _read(path: Path, configuration: dict) -> tuple[dict, tuple]:
    data, snapshot = _read_payload(path)
    value = json.loads(data)
    target, username = _owner(configuration)
    if (not isinstance(value, dict) or set(value) != _FIELDS or type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != 1
            or value["targetVmResourceId"] != target or value["adminUsername"] != username
            or not isinstance(value["generation"], str) or not _UUID.fullmatch(value["generation"])
            or value["boundVmId"] is not None and (not isinstance(value["boundVmId"], str)
                                                   or not _UUID.fullmatch(value["boundVmId"]))
            or not _valid_password(value["password"])):
        raise SerialCredentialError("The saved Serial credential belongs to another initial VM instance or is invalid")
    return value, snapshot


def _valid_password(value) -> bool:
    return (isinstance(value, str) and 48 <= len(value) <= 72
            and all(c in string.ascii_letters + string.digits + "._-!@" for c in value)
            and all(any(c in group for c in value) for group in
                    (string.ascii_uppercase, string.ascii_lowercase, string.digits, "._-!@")))


def _new(configuration: dict) -> dict:
    target, username = _owner(configuration)
    groups = (string.ascii_uppercase, string.ascii_lowercase, string.digits, "._-!@")
    alphabet = "".join(groups)
    characters = [secrets.choice(group) for group in groups]
    characters.extend(secrets.choice(alphabet) for _ in range(44))
    secrets.SystemRandom().shuffle(characters)
    return {"schemaVersion": 1, "targetVmResourceId": target, "adminUsername": username,
            "generation": str(uuid.uuid4()), "password": "".join(characters), "boundVmId": None}


def _publish(path: Path, value: dict, expected: tuple | None = None) -> None:
    """秘密を書き込む前に新規一時ファイルを保護し、検証済み対象だけを切り替える。"""
    _owner_only(path.parent, directory=True)
    payload = (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=".serial-credential-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            _protect_new(staged)
            _owner_only(path.parent, directory=True)
            if _identity(os.fstat(stream.fileno())) != _identity(_regular(staged)):
                raise SerialCredentialError("Serial credential staging changed before writing")
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            staged_info = os.fstat(stream.fileno())
        _owner_only(path.parent, directory=True)
        # Windows publishes a writer's mtime/ctime at close. Re-read the closed
        # file with owner-only ACL/no-follow checks, then compare its content
        # and stable node identity rather than stale pre-close timestamps.
        written, staged_snapshot = _read_payload(staged)
        closed = _regular(staged)
        if (written != payload or (closed.st_dev, closed.st_ino, closed.st_size, closed.st_mode) !=
                (staged_info.st_dev, staged_info.st_ino, staged_info.st_size, staged_info.st_mode)
                or _identity(closed) != staged_snapshot):
            raise SerialCredentialError("Serial credential staging changed before publication")
        if expected is None:
            os.link(staged, path, follow_symlinks=False)
        else:
            _owner_only(path)
            if _identity(_regular(path)) != expected:
                raise SerialCredentialError("Serial credential storage changed before publication")
            os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def prepare_initial_serial_password(configuration: dict, workspace: LocalPaths, vm_response: dict) -> str:
    """正確なVMの404だけ初期秘密を準備し、200では既存資格情報へ触れない。

戻り値はsecure ARM parameterへだけ渡す。呼出元はVM親RGの所有を先に確認し、
配備直前にも不存在を確認する。初期配備成功後にbind_initial_serial_passwordを呼ぶ。
"""
    try:
        _owner(configuration)
        if not isinstance(vm_response, dict):
            raise SerialCredentialError("The initial management VM absence could not be verified")
        if workspace.environment != configuration["environmentName"]:
            raise SerialCredentialError("Serial credential storage belongs to another environment")
        if vm_response.get("statusCode") == 200:
            _owned_vm(configuration, vm_response.get("body"))
            return ""
        if vm_response.get("statusCode") != 404:
            raise SerialCredentialError("The initial management VM absence could not be verified")
        path = _storage(workspace)
        with _locked(path.parent):
            if path.exists() or path.is_symlink():
                value, snapshot = _read(path, configuration)
                if value["boundVmId"] is None:
                    return value["password"]
                # Preserve the completed instance before publishing a new one.
                archive = path.with_name("management-serial-" + value["generation"] + ".json")
                if archive.exists() or archive.is_symlink():
                    archived, _ = _read(archive, configuration)
                    if archived != value:
                        raise SerialCredentialError("The previous Serial credential archive differs")
                else:
                    os.link(path, archive, follow_symlinks=False)
                _owner_only(archive)
                # Creating the archive link changes ctime on the source inode.
                # Read both nodes again before replacing the active credential.
                archived, _ = _read(archive, configuration)
                current, snapshot = _read(path, configuration)
                if archived != value or current != value:
                    raise SerialCredentialError("The previous Serial credential changed before rebuilding")
                value = _new(configuration)
                _publish(path, value, snapshot)
            else:
                value = _new(configuration)
                _publish(path, value)
            return value["password"]
    except SerialCredentialError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        raise SerialCredentialError("Initial Serial credentials could not be prepared; inspect owned protected storage") from None


def bind_initial_serial_password(configuration: dict, workspace: LocalPaths, constructed_vm: dict,
                                 *, initial_password: str) -> None:
    """自分の初期配備で秘密を渡した後だけ、生成秘密へ構築されたvmIdを結び付ける。"""
    try:
        if workspace.environment != configuration["environmentName"]:
            raise SerialCredentialError("Serial credential storage belongs to another environment")
        _owned_vm(configuration, constructed_vm)
        properties = constructed_vm.get("properties")
        if not isinstance(properties, dict):
            raise SerialCredentialError("The constructed management VM instance could not be verified")
        vm_id = str(properties.get("vmId", "")).lower()
        if not _UUID.fullmatch(vm_id):
            raise SerialCredentialError("The constructed management VM instance could not be verified")
        if not _valid_password(initial_password):
            raise SerialCredentialError("The initial Serial deployment input could not be verified")
        path = workspace.checked(workspace.secrets / _FILE_NAME)
        _owner_only(path.parent, directory=True)
        with _locked(path.parent):
            value, snapshot = _read(path, configuration)
            if not secrets.compare_digest(value["password"], initial_password):
                raise SerialCredentialError("The initial Serial deployment input differs from the saved generation")
            if value["boundVmId"] not in (None, vm_id):
                raise SerialCredentialError("The constructed VM differs from the saved Serial credential instance")
            if value["boundVmId"] is None:
                _publish(path, {**value, "boundVmId": vm_id}, snapshot)
    except SerialCredentialError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        raise SerialCredentialError("Initial Serial credential instance binding could not be verified") from None


def prepared_initial_serial_generation(configuration: dict, workspace: LocalPaths, password: str) -> str:
    """初回404または元作成証跡の照合済み回復だけ、保存世代を内部Core入力へ取得する。"""
    if not password:
        return ""
    if workspace.environment != configuration["environmentName"] or not _valid_password(password):
        raise SerialCredentialError("The initial Serial generation input could not be verified")
    path = workspace.checked(workspace.secrets / _FILE_NAME)
    _owner_only(path.parent, directory=True)
    with _locked(path.parent):
        value, _ = _read(path, configuration)
        if not secrets.compare_digest(value["password"], password):
            raise SerialCredentialError("The initial Serial input differs from the original saved generation")
        return value["generation"]

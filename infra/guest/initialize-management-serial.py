#!/usr/bin/env python3
"""Initialize an original VM's local credential with protected named input.

SSH password authentication stays disabled. Completed generations never write
again. Recovery performs at most one login-PAM authentication, never chpasswd.
PAM authentication can write logs and affect account lockout state. Protected
extension settings/root process memory can retain the password; stdout cannot.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True
import base64
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SOURCE = "managed-serial-initialization-v1"
DIRECTORY = Path("/var/lib/egakumi-serial-initialization")
FIELDS = {"resourceId", "vmId", "principalId", "tenantId", "adminUsername", "credentialGeneration", "source"}
PROOF_FIELDS = FIELDS | {"schemaVersion", "status", "passwordWriteRequested", "verificationAttempted", "pamAuthenticated", "sshVerified"}
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
CLEAN_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"}


class Blocked(RuntimeError):
    pass


def initialize(expected, password, *, observe, read, persist, pristine, write_password, authenticate, recovery=False):
    if observe() != expected:
        raise Blocked()
    receipt = read()
    if receipt is not None:
        if (not isinstance(receipt, dict) or set(receipt) != PROOF_FIELDS or receipt.get("schemaVersion") != 1
                or any(receipt.get(k) != expected[k] for k in FIELDS)
                or any(type(receipt.get(k)) is not bool for k in ("passwordWriteRequested", "verificationAttempted", "pamAuthenticated", "sshVerified"))
                or receipt.get("sshVerified") is not True or receipt.get("status") not in {"requested", "complete"}):
            raise Blocked()
        if receipt["status"] == "complete":
            if not all(receipt[k] is True for k in ("passwordWriteRequested", "verificationAttempted", "pamAuthenticated")):
                raise Blocked()
            return dict(receipt)
        if receipt["passwordWriteRequested"] is not True or receipt["pamAuthenticated"] is not False or receipt["verificationAttempted"]:
            raise Blocked()
    else:
        if recovery or not pristine():
            raise Blocked()
        receipt = {**expected, "schemaVersion": 1, "status": "requested", "passwordWriteRequested": True,
                   "verificationAttempted": False, "pamAuthenticated": False, "sshVerified": True}
        persist(dict(receipt))
        write_password(expected["adminUsername"], password)
    receipt = {**receipt, "verificationAttempted": True}
    persist(dict(receipt))
    if not authenticate(expected["adminUsername"], password):
        raise Blocked()
    if observe() != expected:
        raise Blocked()
    receipt = {**receipt, "status": "complete", "pamAuthenticated": True}
    persist(dict(receipt))
    return dict(receipt)


def protected(path, *, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise Blocked()
    return info


def sync_directory():
    fd = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


@contextmanager
def locked():
    import fcntl
    if os.geteuid() != 0:
        raise Blocked()
    for parent in (Path("/var"), Path("/var/lib")):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Blocked()
    try: DIRECTORY.mkdir(mode=0o700)
    except FileExistsError: pass
    protected(DIRECTORY, directory=True)
    path = DIRECTORY / "initialization.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        before = protected(path)
        current = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino): raise Blocked()
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally: os.close(fd)


def read_receipt():
    path = DIRECTORY / "initialization.json"
    if not path.exists() and not path.is_symlink(): return None
    before = protected(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino): raise Blocked()
        payload = stream.read(16385)
        after = os.fstat(stream.fileno())
    current = protected(path)
    if (len(payload) > 16384 or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) !=
            (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)): raise Blocked()
    return json.loads(payload)


def persist_receipt(value):
    protected(DIRECTORY, directory=True)
    target = DIRECTORY / "initialization.json"
    if target.exists() or target.is_symlink(): protected(target)
    fd, name = tempfile.mkstemp(prefix=".initialization-", dir=DIRECTORY)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        protected(staged)
        os.replace(staged, target)
        sync_directory()
    finally: staged.unlink(missing_ok=True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def imds(path, query):
    uri = "http://169.254.169.254/metadata/" + path + "?" + urlencode(query)
    with build_opener(ProxyHandler({}), NoRedirect()).open(Request(uri, headers={"Metadata": "true"}), timeout=5) as reply:
        payload = reply.read(131073)
    if len(payload) > 131072: raise Blocked()
    return json.loads(payload)


def observe(expected):
    compute = imds("instance/compute", {"api-version": "2021-02-01"})
    if (str(compute.get("resourceId", "")).casefold() != expected["resourceId"].casefold()
            or str(compute.get("vmId", "")).lower() != expected["vmId"]): raise Blocked()
    response = imds("identity/oauth2/token", {"api-version": "2018-02-01", "resource": "https://management.azure.com/", "object_id": expected["principalId"]})
    token = response.pop("access_token", None)
    if not isinstance(token, str) or len(token.split(".")) != 3: raise Blocked()
    fragment = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4)))
    token = None
    if (str(claims.get("oid", "")).lower() != expected["principalId"] or str(claims.get("tid", "")).lower() != expected["tenantId"]): raise Blocked()
    account = pwd.getpwnam(expected["adminUsername"])
    if account.pw_uid == 0 or account.pw_shell in ("/usr/sbin/nologin", "/bin/false"): raise Blocked()
    for command in (["/usr/sbin/sshd", "-T"], ["/usr/sbin/sshd", "-T", "-C", f"user={expected['adminUsername']},host=localhost,addr=127.0.0.1"]):
        result = subprocess.run(command, env=CLEAN_ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
        if result.returncode: raise Blocked()
        settings = dict(line.split(None, 1) for line in result.stdout.decode("utf-8", errors="strict").splitlines() if " " in line)
        if any(settings.get(k) != v for k, v in {"passwordauthentication": "no", "kbdinteractiveauthentication": "no", "pubkeyauthentication": "yes", "permitrootlogin": "no"}.items()): raise Blocked()
    return dict(expected)


def pristine(username):
    # Inspect only the configured account marker in memory; no hash or raw
    # shadow content is emitted or saved. Locked existing hashes are rejected.
    fd = os.open("/etc/shadow", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        matches = [line.split(":", 2)[1] for line in stream if line.split(":", 1)[0] == username]
    return len(matches) == 1 and matches[0] in {"", "!", "!!", "*"}


def write_password(username, password):
    result = subprocess.run(["/usr/sbin/chpasswd"], input=(username + ":" + password + "\n").encode("utf-8"),
                            env=CLEAN_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False)
    if result.returncode: raise Blocked()


def pam_authenticate(username, password):
    # One PAM transaction against login and ttyS0. This does not claim actual
    # Serial websocket/tty login success and is not a strict read-only check.
    pam = ctypes.CDLL("libpam.so.0")
    libc = ctypes.CDLL("libc.so.6")
    class Message(ctypes.Structure): _fields_ = [("style", ctypes.c_int), ("message", ctypes.c_char_p)]
    class Response(ctypes.Structure): _fields_ = [("response", ctypes.c_void_p), ("code", ctypes.c_int)]
    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(Message)), ctypes.POINTER(ctypes.POINTER(Response)), ctypes.c_void_p)
    libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
    libc.calloc.restype = ctypes.c_void_p
    libc.strdup.argtypes = [ctypes.c_char_p]
    libc.strdup.restype = ctypes.c_void_p
    libc.free.argtypes = [ctypes.c_void_p]
    @callback_type
    def conversation(count, messages, answer, _data):
        if count < 1 or count > 16: return 19
        allocated = libc.calloc(count, ctypes.sizeof(Response))
        if not allocated: return 19
        responses = ctypes.cast(allocated, ctypes.POINTER(Response))
        try:
            for index in range(count):
                style = messages[index].contents.style
                if style in (1, 2):
                    value = password if style == 1 else username
                    responses[index].response = libc.strdup(value.encode("utf-8"))
                    if not responses[index].response: raise Blocked()
                elif style not in (3, 4): raise Blocked()
            answer[0] = responses
            return 0
        except BaseException:
            for index in range(count):
                if responses[index].response: libc.free(responses[index].response)
            libc.free(allocated)
            return 19
    class Conversation(ctypes.Structure): _fields_ = [("callback", callback_type), ("data", ctypes.c_void_p)]
    handle = ctypes.c_void_p()
    conv = Conversation(conversation, None)
    pam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(Conversation), ctypes.POINTER(ctypes.c_void_p)]
    pam.pam_set_item.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    for name in ("pam_authenticate", "pam_acct_mgmt", "pam_end"):
        getattr(pam, name).argtypes = [ctypes.c_void_p, ctypes.c_int]
    status = pam.pam_start(b"login", username.encode("utf-8"), ctypes.byref(conv), ctypes.byref(handle))
    if status: return False
    try:
        status = pam.pam_set_item(handle, 3, ctypes.cast(ctypes.c_char_p(b"ttyS0"), ctypes.c_void_p))
        if not status: status = pam.pam_authenticate(handle, 0)
        if not status: status = pam.pam_acct_mgmt(handle, 0)
        return status == 0
    finally: pam.pam_end(handle, status)


def inputs(environment):
    password = environment.pop("CORE_SERIAL_PASSWORD", None)
    raw = environment.pop("CORE_SERIAL_EXPECTED", None)
    mode = environment.pop("CORE_SERIAL_MODE", None)
    expected = json.loads(raw) if isinstance(raw, str) else None
    if (not isinstance(expected, dict) or set(expected) != FIELDS or expected.get("source") != SOURCE
            or any(not isinstance(expected.get(k), str) or not UUID.fullmatch(expected[k]) for k in ("vmId", "principalId", "tenantId", "credentialGeneration"))
            or not isinstance(expected.get("resourceId"), str) or not re.fullmatch(r"/subscriptions/[0-9a-f-]+/resourceGroups/[^/%?#\\\s]+/providers/Microsoft.Compute/virtualMachines/[^/%?#\\\s]+", expected["resourceId"], re.I)
            or not isinstance(expected.get("adminUsername"), str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", expected["adminUsername"])
            or not isinstance(password, str) or not 48 <= len(password) <= 72 or not re.fullmatch(r"[A-Za-z0-9._!@-]+", password)
            or mode not in {"initial", "recover"}): raise Blocked()
    return expected, password, mode == "recover"


def main():
    try:
        expected, password, recovery = inputs(os.environ)
        with locked():
            proof = initialize(expected, password, observe=lambda: observe(expected), read=read_receipt, persist=persist_receipt,
                               pristine=lambda: pristine(expected["adminUsername"]), write_password=write_password,
                               authenticate=pam_authenticate, recovery=recovery)
        password = None
        print(json.dumps(proof, sort_keys=True, separators=(",", ":")))
        return 0
    except BaseException:
        # Suppress tracebacks, system responses and secrets on every failure.
        print('{"schemaVersion":1,"status":"blocked"}')
        return 1


if __name__ == "__main__": sys.exit(main())

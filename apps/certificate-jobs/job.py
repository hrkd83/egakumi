"""Run the selected certificate worker with a distributed lease and private receipt."""
import base64
import contextlib
import email.utils
import hashlib
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


ACTIONS = {"tls": {"initial", "renew", "check"},
           "portal": {"initial", "prepare", "inspect", "activate", "retire"}}
LEASE_SECONDS = 60
POLL_SECONDS = 15
MAX_RESULT_BYTES = 64 * 1024


class JobError(Exception):
    """An operation failed. Messages can be emitted to internal telemetry."""


class HttpFailure(Exception):
    def __init__(self, status):
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new_url):
        return None


def request(method, url, headers, body=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, data=body, headers=headers, method=method), timeout=8) as res:
            data = res.read(MAX_RESULT_BYTES + 1)
            if len(data) > MAX_RESULT_BYTES:
                raise JobError("service-response-too-large")
            return res.headers, data
    except urllib.error.HTTPError as error:
        with error:
            raise HttpFailure(error.code) from None


def local_identity_url(value):
    endpoint = urllib.parse.urlsplit(value)
    if endpoint.scheme != "http" or not endpoint.hostname or endpoint.username or endpoint.password:
        raise JobError("invalid-local-identity-endpoint")
    if endpoint.query or endpoint.fragment or not endpoint.path:
        raise JobError("invalid-local-identity-endpoint")
    if endpoint.hostname != "localhost":
        address = ipaddress.ip_address(endpoint.hostname)
        if not (address.is_loopback or address.is_link_local):
            raise JobError("invalid-local-identity-endpoint")
    return value


class Storage:
    def __init__(self, env):
        origin = env["CERTIFICATE_STORAGE_URL"]
        container = env["CERTIFICATE_RECEIPT_CONTAINER"]
        endpoint = urllib.parse.urlsplit(origin)
        suffix = ".blob.core.windows.net"
        hostname = endpoint.hostname or ""
        account = hostname.removesuffix(suffix)
        if (endpoint.scheme != "https" or endpoint.netloc != hostname or endpoint.path or
                endpoint.query or endpoint.fragment or not hostname.endswith(suffix) or
                not re.fullmatch(r"[a-z0-9]{3,24}", account)):
            raise JobError("invalid-storage-endpoint")
        self.audience = env["CERTIFICATE_STORAGE_AUDIENCE"]
        audience = urllib.parse.urlsplit(self.audience)
        if (audience.scheme != "https" or not audience.hostname or audience.netloc != audience.hostname
                or audience.path != "/" or audience.query or audience.fragment):
            raise JobError("invalid-storage-audience")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", container):
            raise JobError("invalid-storage-container")
        self.origin = origin + "/" + container
        self.identity_url = local_identity_url(env["IDENTITY_ENDPOINT"])
        self.identity_header = env["IDENTITY_HEADER"]
        self.client_id = str(uuid.UUID(env["CERTIFICATE_CLIENT_ID"]))
        if not self.identity_header or self.client_id == str(uuid.UUID(int=0)):
            raise JobError("missing-runtime-identity")
        self._token, self._expires, self._lock = "", 0, threading.Lock()

    def token(self):
        with self._lock:
            if self._expires <= time.time() + 120:
                query = urllib.parse.urlencode({"resource": self.audience,
                                               "api-version": "2019-08-01", "client_id": self.client_id})
                _, data = request("GET", self.identity_url + "?" + query,
                                  {"X-IDENTITY-HEADER": self.identity_header})
                response = json.loads(data)
                self._token, self._expires = response["access_token"], int(response["expires_on"])
                if not self._token or self._expires <= time.time() + 60:
                    raise JobError("storage-identity-unavailable")
            return self._token

    def blob(self, method, name, headers=None, body=None, *, lease=False):
        url = self.origin + "/" + urllib.parse.quote(name, safe="/")
        if lease:
            url += "?comp=lease"
        return request(method, url, {"Authorization": "Bearer " + self.token(),
                                     "x-ms-date": email.utils.formatdate(usegmt=True), "x-ms-version": "2023-11-03",
                                     **(headers or {})}, body)

    def read_json(self, name):
        try:
            _, data = self.blob("GET", name)
        except HttpFailure as error:
            if error.status != 404:
                raise
            return None
        value = json.loads(data)
        if not isinstance(value, dict):
            raise JobError("invalid-persisted-record")
        return value

    def write(self, name, body, *, new=False):
        headers = {"x-ms-blob-type": "BlockBlob", "Content-Type": "application/json"}
        if new:
            headers["If-None-Match"] = "*"
        self.blob("PUT", name, headers, body)

    def write_json(self, name, value, *, new=False):
        self.write(name, json.dumps(value, separators=(",", ":")).encode(), new=new)


class Lease:
    """Fence scheduled and approved on-demand workers, including on job termination."""
    def __init__(self, storage, name):
        self.storage, self.name = storage, name
        self.identifier = str(uuid.uuid4())
        self.stop, self.lost = threading.Event(), threading.Event()
        self.heartbeat = None

    def change(self, action, **headers):
        return self.storage.blob("PUT", self.name, {"x-ms-lease-action": action, **headers},
                                 b"", lease=True)[0]

    def __enter__(self):
        # A short wait handles initial role propagation and another active job.
        deadline = time.monotonic() + 300
        while True:
            try:
                try:
                    self.storage.write(self.name, b"", new=True)
                except HttpFailure as error:
                    if error.status not in (409, 412):
                        raise
                response = self.change("acquire", **{"x-ms-lease-duration": str(LEASE_SECONDS),
                                                      "x-ms-proposed-lease-id": self.identifier})
                if response.get("x-ms-lease-id") != self.identifier:
                    raise JobError("unexpected-lease-identifier")
                break
            except HttpFailure as error:
                if error.status not in (403, 409, 412, 429, 500, 503) or time.monotonic() >= deadline:
                    raise
                time.sleep(5)
        self.heartbeat = threading.Thread(target=self.renew, daemon=True)
        self.heartbeat.start()
        return self

    def renew(self):
        while not self.stop.wait(POLL_SECONDS):
            try:
                self.change("renew", **{"x-ms-lease-id": self.identifier})
            except (HttpFailure, JobError, OSError, ValueError, KeyError):
                self.lost.set()
                return

    def verify(self):
        if self.lost.is_set():
            raise JobError("lease-lost")
        self.change("renew", **{"x-ms-lease-id": self.identifier})

    def __exit__(self, *_):
        self.stop.set()
        self.heartbeat.join(timeout=POLL_SECONDS)
        with contextlib.suppress(HttpFailure, JobError, OSError, ValueError, KeyError):
            self.change("release", **{"x-ms-lease-id": self.identifier})


def validate_result(mode, action, result):
    if not isinstance(result, dict):
        raise JobError("invalid-worker-result")
    if mode == "portal" and action == "retire":
        if set(result) != {"retired", "keyId"} or result["retired"] is not True or not isinstance(result["keyId"], str):
            raise JobError("invalid-worker-result")
        return
    reference = "secretId" if mode == "tls" else "keyId"
    common = {"certificateId", "certificateThumbprint", "notBefore", "expires", "publicCertificate"}
    expected = common | {reference}
    if mode == "tls":
        expected.add("dnsNames")
        names = result.get("dnsNames")
        if (not isinstance(names, list) or not names or
                any(not isinstance(name, str) or not name for name in names)):
            raise JobError("invalid-worker-result")
    if set(result) != expected:
        raise JobError("invalid-worker-result")
    for name in ("certificateId", reference, "certificateThumbprint", "publicCertificate"):
        if not isinstance(result[name], str) or not result[name]:
            raise JobError("invalid-worker-result")
    if (not re.fullmatch(r"[a-f0-9]{40}", result["certificateThumbprint"])
            or type(result["notBefore"]) is not int or type(result["expires"]) is not int
            or result["notBefore"] >= result["expires"]):
        raise JobError("invalid-worker-result")
    der = base64.b64decode(result["publicCertificate"], validate=True)
    if (not 128 <= len(der) <= 16384 or der[0] != 0x30 or
            hashlib.sha1(der, usedforsecurity=False).hexdigest() != result["certificateThumbprint"]):
        raise JobError("invalid-worker-result")


def run_worker(lease, mode, action):
    process = subprocess.Popen(
        ["/usr/bin/pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
         "/app/certificate-job.ps1", "-Mode", mode, "-Action", action],
        start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            if lease.lost.is_set():
                raise JobError("lease-lost")
            try:
                stdout, _ = process.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                continue
        prefix = b"EGAKUMI_CERTIFICATE_RESULT:"
        lines = [line[len(prefix):] for line in stdout.splitlines() if line.startswith(prefix)]
        if process.returncode or len(lines) != 1 or len(lines[0]) > MAX_RESULT_BYTES:
            stages = [line for line in stdout.splitlines() if re.fullmatch(b"CERTIFICATE_STAGE:[a-z-]{1,70}", line)]
            detail = stages[-1].split(b":", 1)[1].decode() if stages else "certificate-worker"
            raise JobError(detail)
        return json.loads(lines[0])
    finally:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()


def run(env):
    mode, action = env["CERTIFICATE_MODE"], env["CERTIFICATE_ACTION"]
    if action not in ACTIONS.get(mode, ()):
        raise JobError("invalid-mode-or-action")
    attempt = env.get("CERTIFICATE_ATTEMPT_ID")
    scheduled = not attempt and mode == "tls" and action == "renew"
    if scheduled:
        attempt = uuid.uuid4().hex
    if not re.fullmatch(r"[a-f0-9]{32}", attempt or ""):
        raise JobError("invalid-attempt-identifier")

    origin = Storage(env)
    name = "receipts/" + attempt + ".json"
    expectation = {"attemptId": attempt, "mode": mode, "action": action}
    with Lease(origin, "locks/" + mode + ".lock") as lease:
        previous = origin.read_json(name)
        if previous is not None:
            if set(previous) != set(expectation) | {"result"} or any(previous[k] != v for k, v in expectation.items()):
                raise JobError("unrelated-existing-receipt")
            validate_result(mode, action, previous["result"])
            return "already-succeeded"

        schedule_name = "state/tls-schedule.json"
        interval = json.loads(env["CERTIFICATE_CONFIG_JSON"]).get("renewIntervalHours", 12)
        if mode == "tls" and (type(interval) is not int or not 1 <= interval <= 24):
            raise JobError("invalid-renewal-interval")
        if scheduled and 24 % interval:
            schedule = origin.read_json(schedule_name)
            if schedule is not None:
                last = schedule.get("checkedAt")
                if type(last) is not int or last > time.time() + 60:
                    raise JobError("invalid-schedule-state")
                if last + interval * 3600 > time.time():
                    return "not-due"

        result = run_worker(lease, mode, action)
        validate_result(mode, action, result)
        lease.verify()
        origin.write_json(name, {**expectation, "result": result}, new=True)
        if mode == "tls":
            print(f"certificate-job-expiry-unix:{result['expires']}", flush=True)
        if scheduled and 24 % interval:
            origin.write_json(schedule_name, {"checkedAt": int(time.time())})
        return "succeeded"


def interrupted(_signum, _frame):
    raise JobError("interrupted")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    try:
        print("certificate-job:" + run(os.environ), flush=True)
    except JobError as error:
        print("certificate-job-failed:" + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    except HttpFailure as error:
        print(f"certificate-job-service-error:{error.status}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    except (KeyError, OSError, ValueError):
        print("certificate-job-failed", file=sys.stderr, flush=True)
        raise SystemExit(1) from None

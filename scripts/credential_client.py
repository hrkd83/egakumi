#!/usr/bin/env python3
"""業務GETから本人認証を案内し、本人接続v2をバックエンドで管理する。

購読キー・仮セッション・pollSecret・更新候補は画面やログへ返さない。
CLIは単一ホストの診断用。本番は検証済み本人と共有保存先を指定する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from credential_session_store import FileSessionStore, MemorySessionStore


HEX = re.compile(r"[a-f0-9]{64}\Z")
SESSION = re.compile(r"cs2\.([a-f0-9]{64})\.([a-f0-9]{64})\Z")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z", re.I)
ROTATION_HEADERS = {
    "candidateSession": "x-core-connection-update", "rotationId": "x-core-connection-update-id",
    "fromGeneration": "x-core-connection-update-from-generation",
    "generation": "x-core-connection-update-generation", "expiresAt": "x-core-connection-update-expires-at",
}


class ConnectionError(RuntimeError):
    """応答本文や資格情報を含めず、固定診断だけを公開する。"""

    def __init__(self, message, **diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def header_values(headers):
    return {key.lower(): value for key, value in (headers or {}).items()}


def http_failure(operation, status, headers, body=b""):
    """HTTP失敗の段階・固定コード・要求IDのみを診断へ残す。"""
    details = {"stage": operation, "httpStatus": status}
    request_id = header_values(headers).get("apim-request-id", "")
    if UUID.fullmatch(request_id):
        details["requestId"] = request_id
    try:
        code = json.loads(body).get("error", {}).get("code")
        if isinstance(code, str) and code in {
            "AuthenticationRequired", "ConnectionRequired", "InvalidProvider", "ConnectionUnavailable",
            "ConnectionOperationFailed", "ConnectionNotAuthorized", "ConnectionRateLimited", "InvalidTransaction",
            "ConnectionClaimsRequired", "ConnectionPermissionDenied",
            "SessionRotationSuperseded", "SessionRotationExpired", "SessionUpdateRequired", "InvalidRotation",
        }:
            details["errorCode"] = code
    except (ValueError, TypeError, AttributeError):
        pass
    return ConnectionError("Gateway connection operation failed", **details)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ConnectionError("Gateway redirected the server request")


def valid_expiry(value):
    """Unix秒だけを許可し、真偽値・非有限値・異常範囲を拒否する。"""
    return type(value) in (int, float) and 0 < value <= 253402300799


def session_id(value):
    if not isinstance(value, str) or not (match := SESSION.fullmatch(value)):
        raise ValueError("Invalid private connection session")
    return match.group(1)


class LoginRequired:
    """仮セッションの保存後にだけ、表示可能なログインURLを公開する。"""

    def __init__(self, client, api_path, authentication):
        self._client, self._api_path, self._authentication = client, api_path, authentication
        client._save_login(authentication)
        self.login_url, self.expires_in = authentication["loginUrl"], authentication["expiresIn"]
        self._deadline = time.monotonic() + self.expires_in
        self.connection_session, self.session_expires_at, self._resumed = None, None, False

    def poll(self):
        """状態と検証済み本人を確認し、保存済み仮値を有効なセッションとして扱う。"""
        if self.connection_session is not None:
            return "complete"
        if time.monotonic() >= self._deadline:
            raise ConnectionError("Gateway connection transaction expired", stage="status")
        value = self._client.status(self._authentication["transactionId"], self._authentication["pollSecret"])
        if value["status"] == "complete":
            state = self._client.store.read()
            if state.get("status") != "complete" or state.get("sessionId") != value["sessionId"]:
                raise ConnectionError("Gateway connection state changed", stage="status")
            self.connection_session, self.session_expires_at = state["connectionSession"], value["expiresAt"]
        return value["status"]

    def resume(self):
        """認証後は読み取りGETだけを一度再開し、認証ループを作らない。"""
        if self._resumed:
            raise ConnectionError("Gateway API request was already resumed", stage="api")
        if self.poll() != "complete":
            return self
        self._resumed = True
        with self._client.store.locked():
            return self._client._get_api(self._api_path, allow_login=False)


class CredentialClient:
    """サーバー専用HTTP例。共有store・購読ID・検証済みtid/oidで利用者を分離する。"""

    def __init__(self, origin, api_key, *, key_header="api-key", opener=None, store=None,
                 expected_subject=None, subscription_id=None, profile="graph-delegated"):
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError("A fixed HTTPS Gateway origin is required")
        if (not re.fullmatch(r"[A-Za-z0-9-]+", key_header) or key_header.lower() in
                ("authorization", "host", "cookie", "content-type", "x-core-connection-session", "x-core-connection-poll")):
            raise ValueError("A dedicated subscription key header is required")
        if not api_key or any(character.isspace() for character in api_key):
            raise ValueError("Invalid subscription key")
        if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", profile):
            raise ValueError("Invalid configured provider name")
        if expected_subject is not None:
            if (not isinstance(expected_subject, dict) or set(expected_subject) != {"tenantId", "ownerId"}
                    or not all(isinstance(value, str) and UUID.fullmatch(value) for value in expected_subject.values())):
                raise ValueError("Verified tenant and object identifiers are required")
            expected_subject = {key: value.lower() for key, value in expected_subject.items()}
            if not subscription_id:
                raise ValueError("A stable subscription identifier is required for an application binding")
        if subscription_id is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", subscription_id):
            raise ValueError("Invalid configured subscription identifier")
        self.origin, self.api_key, self.key_header = origin.rstrip("/"), api_key, key_header
        self.opener, self.store = opener or build_opener(NoRedirect()), store if store is not None else MemorySessionStore()
        self.expected_subject, self.session_update_pending = expected_subject, False
        # 診断だけはキーのハッシュを使う。本番は再生成でも変わらない購読IDを指定する。
        subscription_binding = subscription_id or "diagnostic-key-sha256:" + hashlib.sha256(api_key.encode()).hexdigest()
        self.binding = {"origin": self.origin, "subscriptionId": subscription_binding, "profile": profile, "subject": expected_subject}
        with self.store.locked():
            self._state()

    def _state(self):
        state = self.store.read()
        if state.get("binding") is not None and state["binding"] != self.binding:
            raise ValueError("Private session state belongs to another application binding")
        if "connectionSession" in state:
            session_id(state["connectionSession"])
            if state.get("protocolVersion") != 2:
                raise ValueError("Only connection session protocol v2 is supported")
        return state

    def _save(self, before, after):
        return self.store.write({**after, "binding": self.binding}, expected_revision=before.get("revision", 0))

    def _request(self, method, operation, *, body=None, query=None, headers=None):
        path = "/auth/connections/" + operation
        if query:
            path += "?" + urlencode(query)
        return self._send_json(method, path, operation, body=body, headers=headers)

    def _send_json(self, method, path, operation, *, body=None, headers=None, api_path=None, allow_login=False,
                   allow_update=True):
        values = {"Accept": "application/json", "User-Agent": "egakumi-core-credential-client/2.0",
                  self.key_header: self.api_key, **(headers or {})}
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            values["Content-Type"] = "application/json"
        try:
            try:
                response = self.opener.open(Request(self.origin + path, data=data, headers=values, method=method), timeout=25)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(65537)
                status = getattr(response, "status", None) or getattr(response, "code", None)
                response_headers = getattr(response, "headers", {})
                if len(raw) > 65536:
                    raise ConnectionError("Gateway response is too large", stage=operation)
                if status != 200 and not (operation == "disconnect" and status == 202):
                    return self._failed_response(operation, status, response_headers, raw, api_path, allow_login, allow_update)
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ConnectionError("Invalid Gateway response", stage=operation)
                if api_path is not None:
                    self._business_metadata(response_headers)
                return result
        except ConnectionError:
            raise
        except (URLError, OSError) as error:
            cause = error.reason if isinstance(error, URLError) else error
            reason = ("certificate" if isinstance(cause, ssl.SSLCertVerificationError)
                      else "timeout" if isinstance(cause, TimeoutError) else "transport")
            raise ConnectionError("Gateway connection operation failed", stage=operation, reason=reason) from None
        except (ValueError, TypeError):
            raise ConnectionError("Gateway connection operation failed", stage=operation, reason="invalid-response") from None

    def _failed_response(self, operation, status, headers, body, api_path, allow_login, allow_update=True):
        if api_path is not None and any(key.startswith("x-core-connection-") for key in header_values(headers)):
            # 認証済み業務APIの429/500等でも更新ヘッダーは有効。本文とHTTP状態は変更しない。
            self._business_metadata(headers)
        if status != 409 or api_path is None:
            raise http_failure(operation, status, headers, body) from None
        try:
            value = json.loads(body)
            code = value.get("error", {}).get("code") if isinstance(value, dict) else None
            if status == 409 and api_path is not None:
                if code == "ConnectionRequired" and allow_login:
                    return LoginRequired(self, api_path, self._authentication(value.get("authentication"), stage="api"))
                if code == "SessionUpdateRequired" and allow_update:
                    self._persist_rotation(value.get("rotation"))
                    self._commit_pending()
                    # この409だけは業務実行前の拒否。成功した業務処理の再送には使わない。
                    return self._get_api(api_path, allow_login=allow_login, allow_update=False)
        except (ValueError, TypeError, AttributeError):
            raise ConnectionError("Invalid Gateway authentication challenge", stage=operation, reason="invalid-response") from None
        raise http_failure(operation, status, headers, body) from None

    def _authentication(self, value, *, stage):
        if not isinstance(value, dict):
            raise ConnectionError("Invalid Gateway transaction", stage=stage, reason="invalid-transaction")
        transaction, secret, expiry = value.get("transactionId"), value.get("pollSecret"), value.get("expiresIn")
        if (value.get("protocolVersion") != 2 or value.get("status") != "need_login"
                or not isinstance(transaction, str) or not HEX.fullmatch(transaction)
                or not isinstance(secret, str) or not HEX.fullmatch(secret)
                or type(expiry) is not int or not 60 <= expiry <= 600):
            raise ConnectionError("Invalid Gateway transaction", stage=stage, reason="invalid-transaction")
        provisional = value.get("provisionalConnectionSession")
        try:
            sid = session_id(provisional)
        except ValueError:
            raise ConnectionError("Invalid Gateway transaction", stage=stage, reason="invalid-session") from None
        if sid == transaction or provisional.endswith("." + secret):
            raise ConnectionError("Gateway session must be independent of polling", stage=stage)
        link = value.get("loginUrl", "")
        if not isinstance(link, str) or any(character.isspace() for character in link):
            raise ConnectionError("Invalid Gateway login URL", stage=stage, reason="invalid-login-url")
        try:
            parsed = urlsplit(link)
        except ValueError:
            raise ConnectionError("Invalid Gateway login URL", stage=stage, reason="invalid-login-url") from None
        query = parse_qs(parsed.fragment, keep_blank_values=True)
        if (parsed.scheme + "://" + parsed.netloc != self.origin or parsed.path != "/auth/connections/browser/launch"
                or parsed.query or parsed.username or parsed.password or set(query) != {"transactionId", "launch"}
                or query.get("transactionId") != [transaction] or len(query.get("launch", [])) != 1
                or not HEX.fullmatch(query["launch"][0])):
            raise ConnectionError("Gateway login URL left the configured origin", stage=stage, reason="invalid-login-url")
        status_url = self.origin + "/auth/connections/status?" + urlencode({"transactionId": transaction})
        if value.get("statusUrl", status_url) != status_url:
            raise ConnectionError("Invalid Gateway status URL", stage=stage, reason="invalid-status-url")
        return {"protocolVersion": 2, "status": "need_login", "transactionId": transaction, "pollSecret": secret,
                "provisionalConnectionSession": provisional, "loginUrl": link, "expiresIn": expiry, "statusUrl": status_url}

    def _save_login(self, authentication):
        with self.store.locked():
            before = self._state()
            after = {"protocolVersion": 2, "status": "pending", "generation": 0,
                     "connectionSession": authentication["provisionalConnectionSession"],
                     "sessionId": session_id(authentication["provisionalConnectionSession"]), "authentication": authentication}
            # 再認証時も元の本人へ限定する。未信頼のUPNで対応付けない。
            if "ownerId" in before:
                after.update({key: before[key] for key in ("ownerId", "tenantId")})
            self._save(before, after)

    def start(self, provider):
        if provider != self.binding["profile"]:
            raise ValueError("Provider does not match the configured application binding")
        value = self._authentication(self._request("POST", "start", body={"providerName": provider}), stage="start")
        self._save_login(value)
        return value

    @staticmethod
    def _metadata(value, *, require_owner=False):
        if not isinstance(value, dict) or value.get("protocolVersion") != 2:
            raise ConnectionError("Invalid Gateway session metadata", stage="status")
        fields = ("idleExpiresAt", "absoluteExpiresAt", "nextRotationAt", "expiresAt")
        if (not isinstance(value.get("sessionId"), str) or not HEX.fullmatch(value["sessionId"])
                or type(value.get("generation")) is not int or value["generation"] < 1
                or not all(valid_expiry(value.get(key)) for key in fields)
                or value["expiresAt"] != min(value["idleExpiresAt"], value["absoluteExpiresAt"])):
            raise ConnectionError("Invalid Gateway session metadata", stage="status")
        if any(key in value for key in ("connectionSession", "provisionalConnectionSession", "pollSecret", "candidateSession")):
            raise ConnectionError("Gateway status must not disclose session values", stage="status")
        selected = {key: value[key] for key in ("protocolVersion", "sessionId", "generation", *fields)}
        if require_owner:
            if not all(isinstance(value.get(key), str) and UUID.fullmatch(value[key]) for key in ("tenantId", "ownerId")):
                raise ConnectionError("Invalid verified Gateway identity", stage="status")
            selected.update({key: value[key].lower() for key in ("tenantId", "ownerId")})
            if isinstance(value.get("providerName"), str):
                selected["providerName"] = value["providerName"]
        return selected

    def status(self, transaction, poll_secret):
        if not all(isinstance(item, str) and HEX.fullmatch(item) for item in (transaction, poll_secret)):
            raise ValueError("Invalid private transaction reference")
        value = self._request("GET", "status", query={"transactionId": transaction}, headers={"X-Core-Connection-Poll": poll_secret})
        if value.get("status") == "pending":
            return {"status": "pending"}
        if value.get("status") != "complete":
            raise ConnectionError("Invalid Gateway connection state", stage="status")
        metadata = self._metadata(value, require_owner=True)
        with self.store.locked():
            before = self._state()
            if (before.get("status") != "pending" or before.get("authentication", {}).get("transactionId") != transaction
                    or before.get("sessionId") != metadata["sessionId"]):
                raise ConnectionError("Polling cannot recover a connection session", stage="status")
            owner = {key: metadata[key] for key in ("tenantId", "ownerId")}
            bound = self.expected_subject or ({key: before[key] for key in ("tenantId", "ownerId")} if "ownerId" in before else None)
            if bound is not None and owner != bound:
                raise ConnectionError("Gateway identity did not match the application user", stage="status", reason="identity-mismatch")
            self._save(before, {"status": "complete", "connectionSession": before["connectionSession"], **metadata})
        return {"status": "complete", **metadata}

    def get_api(self, path="/tools/graph/v1/me", *, connection_session=None):
        """業務GETまたはLoginRequiredを返す。日次更新で成功済みGETを再送しない。"""
        if (not isinstance(path, str) or not re.fullmatch(r"/[A-Za-z0-9_./~-]+", path)
                or "//" in path or any(item in (".", "..") for item in path.split("/")) or path.startswith("/auth/")):
            raise ValueError("A fixed Gateway API path is required")
        if connection_session is not None:
            session_id(connection_session)
        with self.store.locked():
            before = self._state()
            if connection_session is not None:
                if before.get("connectionSession") not in (None, connection_session):
                    raise ValueError("Explicit session cannot overwrite newer saved state")
                if "connectionSession" not in before:
                    if self.expected_subject is not None:
                        raise ValueError("Application sessions must originate from verified authentication")
                    self._save(before, {"protocolVersion": 2, "status": "complete", "generation": 1,
                                        "sessionId": session_id(connection_session), "connectionSession": connection_session})
            self._commit_pending()
            return self._get_api(path, allow_login=True)

    def _get_api(self, path, *, allow_login, allow_update=True):
        state = self._state()
        current = state.get("connectionSession") if state.get("status") == "complete" else None
        headers = {} if current is None else {"X-Core-Connection-Session": current}
        return self._send_json("GET", path, "api", headers=headers, api_path=path, allow_login=allow_login, allow_update=allow_update)

    def _business_metadata(self, headers):
        values, before = header_values(headers), self._state()
        if before.get("status") != "complete":
            return
        names = {"generation": "x-core-connection-generation", "idleExpiresAt": "x-core-connection-idle-expires-at",
                 "absoluteExpiresAt": "x-core-connection-absolute-expires-at", "nextRotationAt": "x-core-connection-rotate-after"}
        if any(name in values for name in names.values()):
            try:
                metadata = {key: int(values[name]) for key, name in names.items()}
            except (KeyError, ValueError, TypeError):
                raise ConnectionError("Invalid Gateway session headers", stage="api") from None
            self._metadata({**metadata, "protocolVersion": 2, "sessionId": before["sessionId"],
                            "expiresAt": min(metadata["idleExpiresAt"], metadata["absoluteExpiresAt"])})
            if "absoluteExpiresAt" in before and metadata["absoluteExpiresAt"] != before["absoluteExpiresAt"]:
                raise ConnectionError("Gateway changed the absolute session lifetime", stage="api")
            if metadata["generation"] == before["generation"]:
                self._save(before, {**before, **metadata, "expiresAt": min(metadata["idleExpiresAt"], metadata["absoluteExpiresAt"])})
        if any(name in values for name in ROTATION_HEADERS.values()):
            try:
                rotation = {key: (int(values[name]) if key in ("fromGeneration", "generation", "expiresAt") else values[name])
                            for key, name in ROTATION_HEADERS.items()}
            except (KeyError, ValueError, TypeError):
                raise ConnectionError("Invalid Gateway update headers", stage="api") from None
            self._persist_rotation(rotation)
            try:
                self._commit_pending()
            except ConnectionError:
                # 業務APIは成功済み。候補を残し、次回は確定APIだけを先に再試行する。
                self.session_update_pending = True

    def _persist_rotation(self, rotation):
        before = self._state()
        if not isinstance(rotation, dict) or before.get("status") != "complete":
            raise ConnectionError("Invalid Gateway rotation", stage="rotation")
        try:
            sid = session_id(rotation.get("candidateSession"))
        except ValueError:
            raise ConnectionError("Invalid Gateway rotation", stage="rotation") from None
        if (sid != before["sessionId"] or rotation["candidateSession"] == before["connectionSession"]
                or not isinstance(rotation.get("rotationId"), str) or not HEX.fullmatch(rotation["rotationId"])
                or type(rotation.get("fromGeneration")) is not int or type(rotation.get("generation")) is not int
                or rotation["fromGeneration"] != before["generation"] or rotation["generation"] != before["generation"] + 1
                or not valid_expiry(rotation.get("expiresAt"))):
            raise ConnectionError("Invalid Gateway rotation", stage="rotation")
        self._save(before, {**before, "pendingRotation": {key: rotation[key] for key in ROTATION_HEADERS}})

    def _commit_pending(self):
        before = self._state()
        pending = before.get("pendingRotation")
        if pending is None:
            return
        body = {key: pending[key] for key in ("rotationId", "candidateSession", "fromGeneration")}
        retry = before.get("rotationCommitAttempted", False)
        if not retry:
            # 要求を送る前に試行を記録する。応答消失後・プロセス再起動後も候補の証明で照会できる。
            before = self._save(before, {**before, "rotationCommitAttempted": True})
        try:
            proof = pending["candidateSession"] if retry else before["connectionSession"]
            try:
                value = self._request("POST", "session/commit", body=body, headers={"X-Core-Connection-Session": proof})
            except ConnectionError as error:
                if retry and error.diagnostics.get("httpStatus") == 403 and error.diagnostics.get("errorCode") == "ConnectionNotAuthorized":
                    # 候補が未確定なら現行値で確定する。旧値の猶予切れ後も既確定候補は上で確認できる。
                    value = self._request("POST", "session/commit", body=body,
                                          headers={"X-Core-Connection-Session": before["connectionSession"]})
                else:
                    raise
        except ConnectionError as error:
            if error.diagnostics.get("httpStatus") == 409 and error.diagnostics.get("errorCode") == "SessionRotationExpired":
                after = {**before}
                after.pop("pendingRotation", None)
                after.pop("rotationCommitAttempted", None)
                self._save(before, after)
                self.session_update_pending = False
                return
            if error.diagnostics.get("errorCode") == "SessionRotationSuperseded":
                latest = self._state()
                if latest.get("generation", 0) > pending["fromGeneration"] and latest.get("pendingRotation") is None:
                    self.session_update_pending = False
                    return
            raise
        metadata = self._metadata(value)
        if metadata["sessionId"] != before["sessionId"] or metadata["generation"] != pending["generation"]:
            raise ConnectionError("Invalid Gateway committed generation", stage="rotation")
        if "absoluteExpiresAt" in before and metadata["absoluteExpiresAt"] != before["absoluteExpiresAt"]:
            raise ConnectionError("Gateway changed the absolute session lifetime", stage="rotation")
        after = {**before, **metadata, "connectionSession": pending["candidateSession"]}
        after.pop("pendingRotation", None)
        after.pop("rotationCommitAttempted", None)
        self._save(before, after)
        self.session_update_pending = False

    def _end(self, operation, connection_session=None):
        with self.store.locked():
            # 確定応答を失った候補が既に現行になっている場合、旧値の猶予後も失効操作できる。
            # 未確定で期限切れの候補は候補だけを除去し、引き続き保存済み現行値を使用する。
            self._commit_pending()
            before = self._state()
            # 呼出側が保持していた旧スナップショットより、回復した共有保存先の現行値を優先する。
            current = before.get("connectionSession") or connection_session
            session_id(current)
            result = self._request("POST", operation, headers={"X-Core-Connection-Session": current})
            self._save(before, {"protocolVersion": 2, "status": "signed_out"})
            return result

    def logout(self, connection_session=None):
        return self._end("logout", connection_session)

    def logout_all(self):
        """有効なセッションの本人を全失効の対象とし、任意UPNは受け付けない。"""
        return self._end("logout-all")

    def disconnect(self):
        return self._end("disconnect")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--provider", help="認証窓口だけを試す診断用プロファイル")
    operation.add_argument("--api-path", help="本人接続を利用する固定の読み取りAPIパス")
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--key-header", default="api-key")
    parser.add_argument("--session-file", required=True)
    parser.add_argument("--expected-tenant-id", help="アプリ自身が検証したEntra tenant ID")
    parser.add_argument("--expected-owner-id", help="アプリ自身が検証したEntra object ID")
    parser.add_argument("--subscription-id", help="キー再生成で変わらない購読ID")
    args = parser.parse_args(argv)
    if bool(args.expected_tenant_id) != bool(args.expected_owner_id):
        raise ValueError("Both verified principal identifiers are required")
    from integration_client import private_text
    subject = ({"tenantId": args.expected_tenant_id, "ownerId": args.expected_owner_id} if args.expected_owner_id else None)
    client = CredentialClient(args.origin, private_text(args.key_file), key_header=args.key_header,
                              store=FileSessionStore(args.session_file), expected_subject=subject,
                              subscription_id=args.subscription_id, profile=args.provider or "graph-delegated")
    if args.api_path:
        result, session_saved = client.get_api(args.api_path), False
        if isinstance(result, LoginRequired):
            print("Open this temporary login URL: " + result.login_url)
            while result.poll() != "complete":
                time.sleep(2)
            session_saved, result = True, result.resume()
        print(json.dumps({"status": "complete", "sessionSaved": session_saved,
                          "sessionUpdatePending": client.session_update_pending, "response": result}))
        return 0
    started = client.start(args.provider)
    print("Open this temporary login URL: " + started["loginUrl"])
    deadline = time.monotonic() + started["expiresIn"]
    while time.monotonic() < deadline:
        time.sleep(2)
        if client.status(started["transactionId"], started["pollSecret"])["status"] == "complete":
            print(json.dumps({"status": "complete", "sessionSaved": True}))
            return 0
    raise ConnectionError("Gateway connection transaction expired")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConnectionError as error:
        print(json.dumps({"status": "failed", "error": "Connection operation failed", **error.diagnostics}))
        raise SystemExit(1)
    except (ValueError, OSError):
        print(json.dumps({"status": "failed", "error": "Connection operation failed", "stage": "local"}))
        raise SystemExit(1)

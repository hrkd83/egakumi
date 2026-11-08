"""BFF のキー・照会秘密・本人接続を、別ホストやブラウザーへ流さない HTTP 呼出契約。"""
import json
from contextlib import redirect_stdout
from datetime import datetime, timezone
from email.message import Message
from io import BytesIO, StringIO
import ssl
import sys
from pathlib import Path
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import credential_client
import cleanup_credential_connections

from test_credential_login import TX, SECRET, LAUNCH, settings


class Response:
    status = 200
    def __init__(self, value, status=200): self.value, self.status = value, status
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return json.dumps(self.value).encode()[:limit]


class Opener:
    def __init__(self, responses): self.responses, self.requests = list(responses), []
    def open(self, request, **kwargs):
        self.requests.append(request)
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value if isinstance(value, Response) else Response(value)


class FailingOpener:
    """通信せず、urllibが返す実際の例外型を要求処理へ渡します。"""
    def __init__(self, error): self.error = error
    def open(self, request, **kwargs): raise self.error


class CredentialFailureDiagnosticsTests(unittest.TestCase):
    """失敗原因の識別情報だけを返し、秘密を含む本文・例外は公開しません。"""
    REQUEST_ID = "11111111-2222-4333-8444-555555555555"
    PRIVATE_VALUE = "SENTINEL_PRIVATE_CREDENTIAL"

    def failed_request(self, upstream_error):
        client = credential_client.CredentialClient(
            "https://gateway.example.com", self.PRIVATE_VALUE,
            opener=FailingOpener(upstream_error))
        with self.assertRaises(credential_client.ConnectionError) as raised:
            client._request("GET", "status", query={"transactionId": TX},
                            headers={"X-Core-Connection-Poll": SECRET})
        error = raised.exception
        self.assertEqual(str(error), "Gateway connection operation failed")
        self.assertTrue(error.__suppress_context__)
        rendered = json.dumps({"error": str(error), **error.diagnostics})
        for secret in (self.PRIVATE_VALUE, SECRET, "SENTINEL_PRIVATE_MESSAGE"):
            self.assertNotIn(secret, rendered)
        return error.diagnostics

    def http_error(self, body, request_id=REQUEST_ID):
        headers = Message()
        # 実HTTPヘッダーと同じ、大文字・小文字を区別しない検索も確認します。
        if request_id is not None: headers["Apim-Request-Id"] = request_id
        headers["Set-Cookie"] = self.PRIVATE_VALUE
        return HTTPError(
            "https://gateway.example.com/auth/connections/status?private=" + self.PRIVATE_VALUE,
            503, "SENTINEL_PRIVATE_MESSAGE", headers,
            BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode()))

    def test_http_failure_retains_only_safe_status_code_and_request_id(self):
        details = self.failed_request(self.http_error({
            "error": {"code": "ConnectionUnavailable", "message": "SENTINEL_PRIVATE_MESSAGE"},
            "pollSecret": SECRET, "accessToken": self.PRIVATE_VALUE,
            "connectionSession": TX + "." + SECRET,
        }))
        self.assertEqual(details, {"stage": "status", "httpStatus": 503,
                                   "requestId": self.REQUEST_ID,
                                   "errorCode": "ConnectionUnavailable"})

    def test_untrusted_error_codes_and_request_identifiers_are_omitted(self):
        cases = (
            {"error": {"code": self.PRIVATE_VALUE}},
            {"error": {"code": {"secret": self.PRIVATE_VALUE}}},
            {"error": {"code": [self.PRIVATE_VALUE]}},
            {"error": self.PRIVATE_VALUE},
            [self.PRIVATE_VALUE],
            {"requestId": self.REQUEST_ID, "pollSecret": SECRET},
            b"<html>SENTINEL_PRIVATE_MESSAGE</html>",
            b"\xff\xfeSENTINEL_PRIVATE_MESSAGE",
        )
        for body in cases:
            with self.subTest(body_type=type(body).__name__):
                details = self.failed_request(self.http_error(body, self.PRIVATE_VALUE))
                self.assertEqual(details, {"stage": "status", "httpStatus": 503})

    def test_missing_or_non_uuid_header_cannot_be_replaced_by_body_identifier(self):
        for header in (None, "", "not-a-uuid", self.REQUEST_ID + "-extra"):
            with self.subTest(header=header):
                details = self.failed_request(self.http_error(
                    {"requestId": self.REQUEST_ID, "error": {"code": "InvalidTransaction"}}, header))
                self.assertEqual(details, {"stage": "status", "httpStatus": 503,
                                           "errorCode": "InvalidTransaction"})

    def test_transport_failures_classify_exception_type_without_exposing_reason(self):
        cases = (
            (URLError(self.PRIVATE_VALUE), "transport"),
            (URLError(OSError("SENTINEL_PRIVATE_MESSAGE")), "transport"),
            (URLError(TimeoutError(self.PRIVATE_VALUE)), "timeout"),
            (URLError(ssl.SSLCertVerificationError(self.PRIVATE_VALUE)), "certificate"),
            (OSError(self.PRIVATE_VALUE), "transport"),
            (TimeoutError(self.PRIVATE_VALUE), "timeout"),
        )
        for error, reason in cases:
            with self.subTest(exception_type=type(error).__name__, reason=reason):
                self.assertEqual(self.failed_request(error), {"stage": "status", "reason": reason})


class CredentialInventoryTests(unittest.TestCase):
    def config(self):
        return {"resourceStem":"test","subscriptionId":"11111111-1111-4111-8111-111111111111",
                "services":{"apimInstanceId":"abc123"},
                "resourceGroupNames":{"svc":"rg-test-svc"},"gateway":{"authentication":{"credentialLogin":{"providerNames":["graph-delegated"]}}},
                "credentialProviders":[{"name":"graph-delegated","displayName":"Graph delegated","pool":{"count":2,"capacity":8000}}]}

    def test_inventory_lists_all_pools_without_deleting_old_connections(self):
        config=self.config()
        class Transport:
            def __init__(self):self.calls=[]
            def request(self,method,path):
                self.calls.append((method,path))
                base=path.split("?")[0]
                name="old.user-0123456789abcdef"
                return {"statusCode":200,"body":{"value":[{"name":name,"id":base+"/"+name,"properties":{"status":"Connected"}}]}}
        transport=Transport()
        result=cleanup_credential_connections.inventory(config,transport,now=9999999999)
        self.assertEqual([method for method,_ in transport.calls],["GET","GET"])
        self.assertEqual(result["connectionCount"],2)
        self.assertEqual([item["providerName"] for item in result["connections"]],["graph-delegated-a","graph-delegated-b"])
        self.assertEqual([item["status"] for item in result["connections"]],["Connected","Connected"])
        self.assertFalse(result["automaticDeletion"])

    def test_delete_execution_is_rejected_before_azure_access(self):
        class Transport:
            def request(self,*args):raise AssertionError("Azure must not be contacted")
        with self.assertRaisesRegex(ValueError,"SQL reconciliation"):
            cleanup_credential_connections.inventory(self.config(),Transport(),execute=True)

    def test_inventory_does_not_follow_foreign_listing_url(self):
        class Transport:
            def request(self,method,path):
                return {"statusCode":200,"body":{"value":[],"nextLink":"https://other.example.com/authorizations"}}
        with self.assertRaises(ValueError):cleanup_credential_connections.inventory(self.config(),Transport())

    def test_inventory_rejects_resource_outside_its_provider(self):
        class Transport:
            def request(self,method,path):
                return {"statusCode":200,"body":{"value":[{"name":"user-id","id":"/unrelated/user-id"}]}}
        with self.assertRaises(ValueError):cleanup_credential_connections.inventory(self.config(),Transport())

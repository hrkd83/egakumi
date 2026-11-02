"""Minimal SQL protocol doubles shared by store and CLI contract tests."""
import json

from credential_broker.store import SqlStore


TENANT = "11111111-1111-4111-8111-111111111111"
OWNER = "22222222-2222-4222-8222-222222222222"
TX = "a" * 64
PROOF = "b" * 64


class Cursor:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.description = []
        self.rows = []
        self.rowcount = 1
    def execute(self, sql, *params):
        self.calls.append((sql, params))
        if self.replies:
            expected, response = self.replies.pop(0)
            if expected not in sql:
                raise AssertionError("Unexpected SQL operation")
            if isinstance(response, Exception):
                raise response
            if isinstance(response, dict):
                self.description = [(key,) for key in response]
                self.rows = [tuple(response.values())]
            elif isinstance(response, list):
                self.description = [(key,) for key in response[0]] if response else []
                self.rows = [tuple(row.values()) for row in response]
            elif isinstance(response, int):
                self.rowcount = response
        return self
    def fetchone(self):
        return self.rows.pop(0) if self.rows else None
    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows
    def close(self): pass
    def nextset(self): return False


class Connection:
    def __init__(self, cursor): self.value = cursor
    def cursor(self): return self.value
    def close(self): pass


def fixture(replies):
    cursor = Cursor(replies)
    return SqlStore(lambda: Connection(cursor)), cursor


def transaction(**changes):
    return {"TransactionId": TX, "ProfileName": "graph-delegated", "SubscriptionId": "subscription-a",
            "PollHash": PROOF, "LaunchHash": "c" * 64, "CookieHash": "d" * 64,
            "OidcStateHash": "e" * 64, "OidcFlow": json.dumps({"state": "nonce", "code_verifier": "private-verifier"}),
            "Phase": "identityPending", "TenantId": TENANT, "ObjectId": OWNER,
            "ProviderName": "graph-delegated-a", "ConnectionId": "user-0123456789abcdef",
            "ExpiresAt": 1200, "PendingSessionId": "7" * 64,
            "PendingSessionSecretHash": "8" * 64, "SubjectEpochAtVerification": 0, "ConnectionEpochAtVerification": 0,
            "TicketHash": "f" * 64, **changes}

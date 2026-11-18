"""検証済みユーザー、CM本人、購読契約の認可境界を合成データで確認する。"""
from urllib.parse import parse_qs, urlsplit
import unittest

from fastapi.testclient import TestClient

from credential_broker.app import create_app
from credential_broker.caller import UserCaller, matches
from credential_broker.identity import Identity
from credential_broker.service import BrokerError
from credential_broker.store import StoreNotAuthorized
from _broker_support import BrokerTestCase, KEY, ORIGIN, OWNER, PROFILE, TENANT
from _store_support import PROOF, TX, fixture, transaction


CALLER = UserCaller(TENANT, OWNER)
OTHER = UserCaller(TENANT, "44444444-4444-4444-8444-444444444444")


class VerifiedUserBrokerTests(BrokerTestCase):
    def headers(self, caller=CALLER, **extra):
        return {"X-Core-Broker-Key": KEY,
                "X-Core-Caller-Id": caller.caller_id, **extra}

    def activate(self, caller=CALLER):
        start = self.service.start(PROFILE, caller)
        launch = parse_qs(urlsplit(start["loginUrl"]).fragment)
        _, cookie = self.service.launch(
            start["transactionId"], launch["launch"][0], ORIGIN
        )
        record = self.store.get_transaction(start["transactionId"])
        self.service.identity_callback(
            {"state": record["flow"]["state"], "code": "synthetic-code"},
            cookie,
        )
        query = parse_qs(urlsplit(self.cm.callback).query)
        context = self.service.callback_context(
            start["transactionId"], query["state"][0], cookie
        )
        self.service.complete(
            start["transactionId"], context["callbackTicket"], OWNER
        )
        return start

    def test_bearer_subject_is_pinned_before_browser_signin_and_never_a_subscription(self):
        with TestClient(create_app(self.service)) as client:
            response = client.post("/v1/start", json={"providerName": PROFILE},
                                   headers=self.headers())
        self.assertEqual(200, response.status_code)
        row = self.store.transactions[response.json()["transactionId"]]
        self.assertIsNone(row["subscriptionId"])
        self.assertEqual(CALLER.caller_id, row["callerId"])
        self.assertEqual((TENANT, OWNER),
                         (row["requiredTenantId"], row["requiredObjectId"]))

    def test_shared_key_duplicate_mixed_or_malformed_caller_cannot_start(self):
        with TestClient(create_app(self.service)) as client:
            for headers, status in [
                ({"X-Core-Caller-Id": CALLER.caller_id}, 401),
                (self.headers(**{"X-Core-Broker-Key": "incorrect"}), 401),
                (self.headers(**{"X-Core-Subscription-Id": "subscription-a"}), 403),
                (self.headers(**{"X-Core-Caller-Id": "user:invalid"}), 403),
                ([("X-Core-Broker-Key", KEY),
                  ("X-Core-Caller-Id", CALLER.caller_id),
                  ("X-Core-Caller-Id", CALLER.caller_id)], 403),
            ]:
                with self.subTest(status=status):
                    response = client.post("/v1/start", json={"providerName": PROFILE},
                                           headers=headers)
                    self.assertEqual(status, response.status_code)
        self.assertFalse(self.store.transactions)

    def test_first_cm_identity_must_match_gateway_user(self):
        start = self.service.start(PROFILE, CALLER)
        launch = parse_qs(urlsplit(start["loginUrl"]).fragment)
        _, cookie = self.service.launch(start["transactionId"],
                                        launch["launch"][0], ORIGIN)
        record = self.store.get_transaction(start["transactionId"])
        self.identity.complete = lambda *args: Identity(
            TENANT, OTHER.principal_id, "other@example.invalid"
        )
        with self.assertRaises(BrokerError) as failure:
            self.service.identity_callback(
                {"state": record["flow"]["state"], "code": "synthetic-code"}, cookie
            )
        self.assertEqual(403, failure.exception.status)
        self.assertFalse(self.store.mappings)
        self.assertFalse(self.cm.ensured)

    def test_user_session_is_usable_only_by_same_user_and_owner(self):
        start = self.activate()
        token = start["provisionalConnectionSession"]
        with TestClient(create_app(self.service)) as client:
            body = {"connectionSession": token, "callerId": CALLER.caller_id,
                    "profileName": PROFILE}
            self.assertEqual(200, client.post("/v2/session/authorize", json=body,
                                             headers=self.headers()).status_code)
            for changed, headers, status in [
                ({**body, "callerId": OTHER.caller_id}, self.headers(), 403),
                ({**body, "callerId": OTHER.caller_id}, self.headers(OTHER), 403),
                ({**body, "subscriptionId": "subscription-a"}, self.headers(), 400),
            ]:
                with self.subTest(status=status):
                    self.assertEqual(status, client.post("/v2/session/authorize",
                                     json=changed, headers=headers).status_code)
            use = {**body, "apiId": "graph-me", "operationId": "get-me",
                   "verifiedOwnerId": OTHER.principal_id}
            self.assertEqual(403, client.post("/v2/session/use", json=use,
                                             headers=self.headers()).status_code)
            use["verifiedOwnerId"] = OWNER
            self.assertEqual(200, client.post("/v2/session/use", json=use,
                                             headers=self.headers()).status_code)
            for caller, status in [(OTHER, 403), (CALLER, 200)]:
                self.assertEqual(status, client.get("/v1/status",
                    params={"transactionId": start["transactionId"]},
                    headers=self.headers(caller, **{
                        "X-Core-Connection-Poll": start["pollSecret"]
                    })).status_code)
            logout = {"connectionSession": token, "callerId": CALLER.caller_id}
            self.assertEqual(200, client.post("/v1/logout", json=logout,
                                             headers=self.headers()).status_code)
        self.assertFalse(self.service.authorize(token, CALLER, PROFILE)["allowed"])

    def test_user_cannot_reauthenticate_a_key_bound_or_other_user_session(self):
        start = self.activate()
        token = start["provisionalConnectionSession"]
        for caller in (OTHER, "subscription-a"):
            with self.subTest(caller_type=type(caller).__name__), self.assertRaises(BrokerError):
                self.service.challenge(PROFILE, caller, token, "token_rejected")
        self.assertTrue(self.service.authorize(token, CALLER, PROFILE)["allowed"])


class VerifiedUserSqlTests(unittest.TestCase):
    def test_sql_identity_pin_rejects_mismatch_before_subject_or_connection_changes(self):
        row = transaction(Phase="identityProcessing", SubscriptionId=None,
                          CallerId=CALLER.caller_id, RequiredTenantId=TENANT,
                          RequiredObjectId=OWNER)
        store, cursor = fixture([
            ("BEGIN TRANSACTION", None),
            ("SELECT * FROM broker.Transactions", row),
            ("DATEDIFF_BIG", {"Now": 1000}), ("ROLLBACK", None),
        ])
        with self.assertRaises(StoreNotAuthorized):
            store.set_identity(TX, TENANT, OTHER.principal_id,
                               "other@example.invalid")
        self.assertFalse(any("UPDATE" in sql or "INSERT" in sql
                             for sql, _ in cursor.calls))

    def test_sql_transaction_stores_user_separately_and_pins_identity(self):
        row = transaction(SubscriptionId=None, CallerId=CALLER.caller_id,
                          RequiredTenantId=TENANT, RequiredObjectId=OWNER)
        store, cursor = fixture([
            ("BEGIN TRANSACTION", None), ("DATEDIFF_BIG", {"Now": 1000}),
            ("SELECT COUNT(*)", {"Count": 0}),
            ("INSERT broker.Transactions", None),
            ("SELECT * FROM broker.Transactions", row), ("COMMIT", None),
        ])
        store.create_transaction(TX, PROFILE, CALLER, PROOF, "c" * 64, 1300,
                                 pending_session_id="7" * 64,
                                 pending_secret_hash="8" * 64)
        statement, values = next(call for call in cursor.calls
                                 if call[0].startswith("INSERT"))
        self.assertIsNone(values[2])
        self.assertEqual(CALLER.caller_id, values[3])
        self.assertEqual((TENANT, OWNER), values[-5:-3])
        self.assertNotIn(CALLER.caller_id, statement)
        count, parameters = next(call for call in cursor.calls if "COUNT(*)" in call[0])
        self.assertIn("WHERE CallerId=?", count)
        self.assertEqual((CALLER.caller_id, 940), parameters)

    def test_matching_caller_id_cannot_hide_a_different_stored_cm_owner(self):
        row = {"subscriptionId": None, "callerId": CALLER.caller_id,
               "tenantId": TENANT, "objectId": OTHER.principal_id}
        self.assertFalse(matches(row, CALLER))
        self.assertFalse(matches(row, "subscription-a"))
        row["objectId"] = OWNER
        self.assertTrue(matches(row, CALLER))

    def test_sql_resolve_rejects_other_user_before_returning_cm_connection(self):
        row = {"SessionId": TX, "SecretHash": PROOF, "SubscriptionId": None,
               "CallerId": CALLER.caller_id, "TenantId": TENANT, "ObjectId": OWNER,
               "ProfileName": PROFILE}
        store, cursor = fixture([
            ("SELECT s.*", row), ("DATEDIFF_BIG", {"Now": 1000}),
        ])
        with self.assertRaises(StoreNotAuthorized):
            store.resolve_session(TX, PROOF, OTHER, PROFILE)
        self.assertFalse(any("UPDATE" in sql for sql, _ in cursor.calls))

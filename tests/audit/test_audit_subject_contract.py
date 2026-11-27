"""現在の主体C#と全監査emitterを、独立した合成内部状態の契約で検査する。"""
import itertools
import json
import re
import unittest

from tests.audit.test_audit_identification import CALLER, CLIENT, PERSON, TENANT, run_fixture
from tests import REPOSITORY


ROOT = REPOSITORY
COMMON = "gatewayCommonAuthenticationValidated"
ENTRA = "gatewayEntraAuthenticationValidated"
USER = "gatewayUserAuthentication"
CONFIG_TENANT = "gatewayEntraTenantId"
SAVED_SUBSCRIPTION = "gatewaySubscriptionId"
OTHER = "55555555-5555-4555-8555-555555555555"
ZERO = "00000000-0000-0000-0000-000000000000"
KEY_SUBJECT_FIELDS = {"status", "kind", "method", "callerId", "subscriptionId"}
USER_SUBJECT_FIELDS = {"status", "kind", "method", "callerId", "allocationId", "tenantId", "principalId", "clientId"}


def user_record():
    return {"schemaVersion": 1, "tenantId": TENANT, "principalId": PERSON,
            "clientId": CLIENT, "callerId": CALLER, "allocationId": CALLER}


def serialized(value):
    return json.dumps(value, separators=(",", ":"))


def cases():
    """期待する監査statusは認証・採用契約から指定し、実C#の条件を翻訳しない。"""
    result, statuses = [], {}

    def add(identifier, status, *, auth="user", variables=None, missing=(), **extra):
        result.append({"id": identifier, "auth": auth, "variables": variables or {},
                       "missingVariables": list(missing), **extra})
        statuses[identifier] = status

    # 存在しない値とfalseを同一視する一方、null/string/intは不整合として扱う契約です。
    states = {"missing": None, "false": False, "true": True, "null": None, "string": "true", "int": 1}
    for auth, common, entra in itertools.product(("key", "user"), states, states):
        variables, missing = {}, []
        for name, state in ((COMMON, common), (ENTRA, entra)):
            if state == "missing":
                missing.append(name)
            else:
                variables[name] = states[state]
        if common in {"null", "string", "int"} or entra in {"null", "string", "int"}:
            status = "inconsistent"
        elif common != "true":
            status = "inconsistent" if entra == "true" else "unverified"
        else:
            status = "verified" if auth == "key" or entra == "true" else "inconsistent"
        add(f"flags-{auth}-{common}-{entra}", status, auth=auth, variables=variables, missing=missing)

    # Native購読を採用するときは、STEP4が保存した同じ購読の証拠が必要です。
    add("key-saved-missing", "inconsistent", auth="key", missing=(SAVED_SUBSCRIPTION,))
    for name, value in (("null", None), ("int", 1), ("bool", True), ("blank", " "), ("different", "synthetic-other")):
        add("key-saved-" + name, "inconsistent", auth="key", variables={SAVED_SUBSCRIPTION: value})
    for name, value in (("missing", None), ("empty", ""), ("blank", " ")):
        add("key-native-" + name, "inconsistent", auth="key", subscriptionId=value)
    add("key-evidence-does-not-fallback", "inconsistent", variables={SAVED_SUBSCRIPTION: "synthetic-subscription"})
    add("key-native-does-not-fallback", "inconsistent", subscriptionId="synthetic-subscription")

    for size in (240, 241, 256, 257):
        sid = "s" * size
        variables = {SAVED_SUBSCRIPTION: sid,
                     "gatewayDecision": serialized({"allowed": True, "method": "subscriptionKey", "callerId": "subscription:" + sid})}
        add(f"sid-{size}", "verified" if size <= 256 else "inconsistent", auth="key", variables=variables, subscriptionId=sid)

    # 内部JSON／設定の取得失敗は利用者を作り上げず、HTTP応答を変更しません。
    add("user-missing", "inconsistent", missing=(USER,))
    add("config-missing", "inconsistent", missing=(CONFIG_TENANT,))
    for name, value in (("null", None), ("int", 1), ("bool", True), ("object", {}), ("array", [])):
        add("user-type-" + name, "inconsistent", variables={USER: value})
        add("config-type-" + name, "inconsistent", variables={CONFIG_TENANT: value})
    for name, value in (("empty", ""), ("malformed", "{"), ("array", "[]"), ("null", "null")):
        add("user-json-" + name, "inconsistent", variables={USER: value})
    raw = serialized(user_record())
    for size in (4096, 4097):
        add(f"user-raw-{size}", "verified" if size == 4096 else "inconsistent",
            variables={USER: raw + " " * (size - len(raw))})
    add("config-oversize", "inconsistent", variables={CONFIG_TENANT: "x" * 65})
    for name, value in (("empty", ""), ("invalid", "not-a-guid"), ("zero", ZERO), ("other", OTHER)):
        add("config-" + name, "inconsistent", variables={CONFIG_TENANT: value})
    for name, value in (("compact", TENANT.replace("-", "")), ("braced", "{" + TENANT + "}"), ("upper", TENANT.upper())):
        add("config-" + name, "verified", variables={CONFIG_TENANT: value})

    def mutate(identifier, field, value, *, missing=False, status="inconsistent"):
        record = user_record()
        if missing:
            del record[field]
        else:
            record[field] = value
        add(identifier, status, variables={USER: serialized(record)})

    for name, value in (("null", None), ("string", "1"), ("float", 1.0), ("bool", True), ("other", 2), ("overflow", 10 ** 30)):
        mutate("schema-" + name, "schemaVersion", value)
    mutate("schema-missing", "schemaVersion", None, missing=True)
    for field in ("tenantId", "principalId", "clientId"):
        mutate(field + "-missing", field, None, missing=True)
        for name, value in (("null", None), ("int", 1), ("bool", True), ("invalid", "not-a-guid"), ("zero", ZERO)):
            mutate(field + "-" + name, field, value)
        normal = user_record()[field]
        for name, value in (("upper", normal.upper()), ("compact", normal.replace("-", "")), ("braced", "{" + normal + "}")):
            # Synthetic IDs contain letters here so uppercase is observably noncanonical.
            if name == "upper":
                value = "abcdefab-cdef-4abc-8def-abcdefabcdef".upper()
            mutate(field + "-" + name, field, value)
    mutate("client-empty", "clientId", "", status="verified")
    mutate("client-unlisted", "clientId", OTHER, status="verified")
    for field in ("callerId", "allocationId"):
        mutate(field + "-missing", field, None, missing=True)
        for name, value in (("null", None), ("int", 1), ("bool", True), ("empty", ""), ("other", "user:" + TENANT + ":" + OTHER)):
            mutate(field + "-" + name, field, value)
    record = user_record(); record["unselected"] = "PRIVATE_INTERNAL_TEXT"
    add("extra-internal-field", "verified", variables={USER: serialized(record)})
    record = user_record(); record.update(tenantId=OTHER, callerId="user:" + OTHER + ":" + PERSON,
                                          allocationId="user:" + OTHER + ":" + PERSON)
    add("other-configured-tenant", "verified", variables={USER: serialized(record), CONFIG_TENANT: OTHER})
    return result, statuses


class AuditSubjectContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.statuses = cases()
        # 正本CSの実XMLを評価し、独立した期待statusと各emitterの契約を確認します。
        cls.results = run_fixture(cls.cases, subjects_only=True)


    def test_missing_false_and_wrong_type_markers_keep_distinct_audit_states(self):
        for identifier, expected in self.statuses.items():
            if identifier.startswith("flags-"):
                with self.subTest(case=identifier):
                    self.assertEqual(self.results[identifier]["current"]["status"], expected)

    def test_internal_identity_failures_never_adopt_partial_or_forged_subjects(self):
        for identifier, expected in self.statuses.items():
            with self.subTest(case=identifier):
                subject = self.results[identifier]["current"]
                self.assertEqual(subject["status"], expected)
                if expected != "verified":
                    self.assertEqual(subject, {"status": expected})
                else:
                    self.assertEqual(set(subject), KEY_SUBJECT_FIELDS if subject["method"] == "subscriptionKey" else USER_SUBJECT_FIELDS)

    def test_subscription_boundaries_survive_all_real_shared_emitters_without_truncation(self):
        for size in (240, 241, 256, 257):
            result = self.results[f"sid-{size}"]
            for name, record in result["emitters"].items():
                with self.subTest(size=size, emitter=name):
                    self.assertEqual(record["authenticatedSubject"], result["current"])
                    if size <= 256:
                        self.assertEqual(record["authenticatedSubject"]["subscriptionId"], "s" * size)
                        self.assertEqual(record["authenticatedSubject"]["callerId"], "subscription:" + "s" * size)
            if size <= 256:
                self.assertEqual(result["emitters"]["gateway"]["subscriptionId"], "s" * size)
                self.assertEqual(result["emitters"]["gateway"]["callerId"], "subscription:" + "s" * size)

    def test_client_id_is_optional_metadata_without_a_client_allowlist(self):
        self.assertEqual(self.results["client-empty"]["current"]["clientId"], "")
        self.assertEqual(self.results["client-unlisted"]["current"]["clientId"], OTHER)
        for identifier in ("client-empty", "client-unlisted"):
            self.assertEqual(self.results[identifier]["current"]["callerId"], CALLER)

    def test_audit_classification_never_changes_the_http_response_or_leaks_internal_fields(self):
        self.assertTrue(all(result["responseStatus"] == 400 for result in self.results.values()))
        self.assertNotIn("PRIVATE_", serialized(self.results))

    def test_maximal_subscription_subject_fits_the_unchanged_raw_budget_and_query_contract(self):
        subject = self.results["sid-256"]["current"]
        self.assertEqual(len(serialized(subject)), 633)
        self.assertLess(len(serialized(subject)), 1600)
        query = (ROOT / "scripts/audit/audit-identification.kql").read_text(encoding="utf-8")
        match = re.search(r"boundedIdentifier\(subject\.subscriptionId,\s*(\d+)\)", query)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), len(subject["subscriptionId"]))
        self.assertIn("subjectCaller == strcat('subscription:', subjectSubscription)", query)
        # KQLエンジンはここでは実行しません。実C#の境界結果と正本queryの契約値を結び付けます。


if __name__ == "__main__":
    unittest.main()

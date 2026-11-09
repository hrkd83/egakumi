"""Bounded hourly cleanup with synthetic SQL claims and no network access."""
import copy
import json
import unittest
from unittest import mock

from credential_broker import cli
from credential_broker.cm import CmError
from credential_broker.store import StoreConflict, StoreUnavailable
from _store_support import TENANT, OWNER


APIM = "/subscriptions/" + TENANT + "/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/fixture"
PROFILES = {"graph": {"pools": [{"name": "graph-a", "capacity": 8000}]}}


def connection(index=0, **changes):
    return {"profileName": "graph", "providerName": "graph-a", "connectionId": "member-" + str(index),
            "tenantId": TENANT, "objectId": OWNER, "state": "ready", "version": b"old", **changes}


class CleanupStore:
    def __init__(self, candidates):
        self.candidates = candidates
        self.claims, self.finished, self.blocks = [], [], []
        self.purges = 0
        self.in_flight = None

    def purge_expired_state(self, **kwargs):
        self.purges += 1
        return {"expiredProofs": 0, "deletedSessions": 0, "deletedTransactions": 0}

    def reconciliation_candidates(self):
        return []

    def cleanup_candidates(self, pools, **kwargs):
        assert pools == {("graph", "graph-a")}
        assert kwargs == {"batch_size": 100, "idle_seconds": 5184000}
        return copy.deepcopy(self.candidates)

    def claim_cleanup(self, candidate, owner, **kwargs):
        assert kwargs == {"idle_seconds": 5184000, "lease_seconds": 300}
        claim = dict(candidate, state="deleting", leaseOwner=owner, leaseUntil="future", version=b"claimed")
        self.claims.append(claim)
        return claim

    def validate_cleanup_claim(self, claim):
        return claim in self.claims and claim["version"] == b"claimed"

    def finish_cleanup(self, claim):
        assert self.validate_cleanup_claim(claim)
        self.finished.append(claim)
        return True

    def pool_connection_ids(self, *args):
        return {item["connectionId"] for item in self.candidates if item not in self.finished}

    def block_pool_allocation(self, *args):
        self.blocks.append(args)
        return True

    def finish_disconnect(self, *args):
        raise AssertionError("Hourly cleanup must use its SQL lease/version, not manual finish")


class CleanupCliTests(unittest.TestCase):
    def reconcile(self, store, delete, **kwargs):
        # Unknown full-pool inventory does not prevent exact claimed cleanup.
        return cli.reconcile(store, lambda _: (503, None), APIM, profiles=PROFILES,
                             delete_connection=delete, **kwargs)

    def test_default_plan_never_claims_purges_deletes_or_finishes(self):
        store = CleanupStore([connection()])
        store.cleanup_candidates = mock.Mock(side_effect=AssertionError("Plan must not claim cleanup work"))
        delete = mock.Mock(side_effect=AssertionError("Plan must not delete"))
        result = self.reconcile(store, delete)
        self.assertEqual((store.purges, store.claims, store.finished), (0, [], []))
        self.assertEqual(result["cleanup"]["attempted"], 0)
        self.assertFalse(result["cmCleanupAttempted"])
        store.cleanup_candidates.assert_not_called()

    def test_idle_and_manual_deleting_share_one_hundred_attempt_budget(self):
        store = CleanupStore([connection(i, state="deleting" if i % 2 else "ready") for i in range(105)])
        calls = []
        def delete(resource, *, validate_claim):
            calls.append(resource)
            self.assertTrue(validate_claim())
            return True
        result = self.reconcile(store, delete, execute=True)
        self.assertEqual((len(store.claims), len(calls), len(store.finished)), (100, 100, 100))
        self.assertEqual(result["deletedConnections"], 100)
        self.assertEqual(result["cleanup"]["attempted"], 100)
        self.assertTrue(result["cleanup"]["budgetExhausted"])
        serialized = json.dumps(result)
        self.assertNotIn("member-", serialized)
        self.assertNotIn(TENANT, serialized)

    def test_monotonic_budget_stops_admitting_work_and_claims_only_one_at_a_time(self):
        store = CleanupStore([connection(i) for i in range(3)])
        clock = [1000]
        original_claim = store.claim_cleanup
        def claim(candidate, owner, **kwargs):
            self.assertIsNone(store.in_flight)
            value = original_claim(candidate, owner, **kwargs)
            store.in_flight = value
            return value
        store.claim_cleanup = claim
        def delete(resource, *, validate_claim):
            self.assertIsNotNone(store.in_flight)
            self.assertTrue(validate_claim())
            clock[0] += 301
            store.in_flight = None
            return False
        with mock.patch.object(cli.time, "monotonic", side_effect=lambda: clock[0]):
            result = self.reconcile(store, delete, execute=True)
        self.assertEqual((len(store.claims), result["cleanup"]["pending"], len(store.finished)), (1, 1, 0))
        self.assertTrue(result["cleanup"]["budgetExhausted"])

    def test_failed_pending_and_conflicted_items_keep_slots_without_stopping_later_items(self):
        store = CleanupStore([connection(i) for i in range(5)])
        original_claim = store.claim_cleanup
        def claim(candidate, owner, **kwargs):
            if candidate["connectionId"] == "member-0":
                raise StoreConflict("Another worker claimed this row")
            return original_claim(candidate, owner, **kwargs)
        store.claim_cleanup = claim
        original_finish = store.finish_cleanup
        def finish(value):
            if value["connectionId"] == "member-3":
                raise StoreConflict("Lease expired after the network request")
            return original_finish(value)
        store.finish_cleanup = finish
        def delete(resource, *, validate_claim):
            self.assertTrue(validate_claim())
            if resource.endswith("member-1"):
                raise CmError("Synthetic transport failure")
            return not resource.endswith("member-2")
        result = self.reconcile(store, delete, execute=True)
        self.assertEqual(result["cleanup"], {"attempted": 5, "requested": 4, "deleted": 1,
            "pending": 1, "conflicts": 2, "failed": 1, "budgetExhausted": False})
        self.assertEqual([item["connectionId"] for item in store.finished], ["member-4"])
        self.assertEqual(result["conflicts"], 2)

    def test_claim_identity_change_or_unknown_pool_never_reaches_cm(self):
        for candidate, change in ((connection(providerName="foreign"), None),
                                  (connection(), {"connectionId": "new-connection"}),
                                  (connection(), {"leaseOwner": "other-worker"})):
            with self.subTest(candidate=candidate, change=change):
                store = CleanupStore([candidate])
                original = store.claim_cleanup
                if change:
                    store.claim_cleanup = lambda candidate, owner, **kwargs: dict(original(candidate, owner, **kwargs), **change)
                delete = mock.Mock(side_effect=AssertionError("Unowned resource"))
                result = self.reconcile(store, delete, execute=True)
                self.assertEqual(result["cleanup"]["conflicts"], 1)
                self.assertEqual(store.finished, [])
                delete.assert_not_called()

    def test_sql_claim_failure_does_not_stop_later_candidates(self):
        store = CleanupStore([connection(0), connection(1)])
        original = store.claim_cleanup
        def claim(candidate, owner, **kwargs):
            if candidate["connectionId"] == "member-0":
                raise StoreUnavailable("Synthetic SQL failure with private details")
            return original(candidate, owner, **kwargs)
        store.claim_cleanup = claim
        result = self.reconcile(store, lambda resource, *, validate_claim: validate_claim(), execute=True)
        self.assertEqual((result["cleanup"]["failed"], result["deletedConnections"]), (1, 1))
        self.assertNotIn("private details", json.dumps(result))

    def test_existing_orphan_checks_finish_before_mapping_deletion(self):
        store = CleanupStore([connection()])
        resource = APIM + "/authorizationProviders/graph-a/authorizations/member-0"
        entry = {"id": resource, "type": "Microsoft.ApiManagement/service/authorizationProviders/authorizations",
                 "properties": {"status": "Connected"}}
        checked = []
        def current(*args):
            self.assertEqual(store.finished, [])
            checked.append(True)
            return {"member-0"}
        store.pool_connection_ids = current
        def delete(resource, *, validate_claim):
            self.assertEqual(checked, [True])
            return validate_claim()
        result = cli.reconcile(store, lambda _: (200, {"value": [entry]}), APIM, profiles=PROFILES,
                               execute=True, delete_connection=delete)
        self.assertEqual((result["deletedConnections"], result["poolsQuarantined"]), (1, 0))
        self.assertEqual(store.blocks, [])

    def test_old_callback_signature_has_no_unguarded_fallback(self):
        store = CleanupStore([connection()])
        called = []
        def old_delete(resource):
            called.append(resource)
            return True
        result = self.reconcile(store, old_delete, execute=True)
        self.assertEqual(called, [])
        self.assertEqual(store.finished, [])
        self.assertEqual(result["cleanup"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()

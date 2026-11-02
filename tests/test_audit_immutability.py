"""azdの各段階を正しい順で実行し、失敗時に止まることを検証する。

偽のCLIとARM transportで事前確認・監視準備・構築後設定・撤去の境界を再現する。秘密の一時入力や保持設定を扱うため、成功系だけでなく中断時の挙動も調べる。
"""
from __future__ import annotations

import copy
import contextlib
import io
import json
import os
from pathlib import Path
import re
import stat
import ssl
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers import cleanup
from deployment.helpers import prepare_secrets as secret_preparation
from deployment.helpers.configuration import normalize_configuration
from configuration_fixtures import connection_fixture, fixture, ssh_fixture_key


from lifecycle_fixtures import LifecycleFixture, ScriptedTransport, response_policy


class ImmutabilityTests(unittest.TestCase):
    """azdの各段階を正しい順で実行し、失敗時に止まることを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def setUp(self):
        self.storage = {"immutabilityMode": "Locked", "retentionInDays": 2557, "immutabilityPolicyIds": ["/subscriptions/fixture/resourceGroups/fixture/providers/Microsoft.Storage/storageAccounts/fixture/blobServices/default/containers/audit/immutabilityPolicies/default"]}

    def test_existing_locked_policies_are_retained_while_missing_or_unlocked_can_be_added(self):
        self.storage["immutabilityPolicyIds"].append(self.storage["immutabilityPolicyIds"][0].replace("/audit/", "/other/"))
        for second in (response_policy(), {"statusCode": 404, "headers": {}, "body": None},
                       {"statusCode": 200, "headers": {}, "body": {"properties": {
                           "state": "Deleted", "immutabilityPeriodSinceCreationInDays": 0}}}):
            transport = ScriptedTransport([response_policy("Locked"), second])
            self.assertEqual(lifecycle.prepare_immutability(self.storage, transport),
                             [self.storage["immutabilityPolicyIds"][0]])
            self.assertFalse(transport.mutations())

    def test_all_locked_policies_are_retained_without_put(self):
        transport = ScriptedTransport([response_policy("Locked")])
        self.assertEqual(lifecycle.prepare_immutability(self.storage, transport),
                         self.storage["immutabilityPolicyIds"])
        self.assertFalse(transport.mutations())

    def test_retained_locked_policy_must_meet_requested_retention_and_append_settings(self):
        for before in (response_policy("Locked", days=2556), response_policy("Locked", append=False)):
            with self.subTest(properties=before["body"]["properties"]):
                transport = ScriptedTransport([before])
                with self.assertRaisesRegex(lifecycle.DeploymentError, "retention/append"):
                    lifecycle.prepare_immutability(self.storage, transport)
                self.assertFalse(transport.mutations())

    def test_unknown_policy_state_is_not_treated_as_safe_to_update(self):
        transport = ScriptedTransport([response_policy("Unknown")])
        with self.assertRaisesRegex(lifecycle.DeploymentError, "state"):
            lifecycle.prepare_immutability(self.storage, transport)
        self.assertFalse(transport.mutations())

    def test_deleted_zero_policy_is_absent_in_each_requested_mode_without_mutations(self):
        for mode in ("Disabled", "Unlocked", "Locked"):
            with self.subTest(mode=mode):
                self.storage["immutabilityMode"] = mode
                transport = ScriptedTransport([{"statusCode": 200, "headers": {}, "body": {
                    "properties": {"state": "Deleted", "immutabilityPeriodSinceCreationInDays": 0}}}])
                self.assertEqual(lifecycle.prepare_immutability(self.storage, transport), [])
                self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_deleted_policy_without_exact_zero_retention_is_not_safe_to_update(self):
        for period in (None, 1, -1, False, 0.0, "0"):
            with self.subTest(period=period):
                properties = {"state": "Deleted"}
                if period is not None:
                    properties["immutabilityPeriodSinceCreationInDays"] = period
                transport = ScriptedTransport([{"statusCode": 200, "headers": {},
                                                "body": {"properties": properties}}])
                with self.assertRaisesRegex(lifecycle.DeploymentError, "state"):
                    lifecycle.prepare_immutability(self.storage, transport)
                self.assertFalse(transport.mutations())

    def test_added_apim_categories_lock_without_relocking_existing_then_repeat_is_read_only(self):
        old = self.storage["immutabilityPolicyIds"][0]
        added = [old.replace('/audit/', '/' + name + '/') for name in
                 ('insights-logs-gatewayllmlogs', 'insights-logs-websocketconnectionlogs')]
        self.storage["immutabilityPolicyIds"] += added
        # 既存はLocked、新カテゴリは未作成。準備では既存の保護を変更しない。
        transport = ScriptedTransport([response_policy("Locked")] +
                                      [{"statusCode": 404, "headers": {}, "body": None}] * 2)
        self.assertEqual(lifecycle.prepare_immutability(self.storage, transport), [old])
        self.assertFalse(transport.mutations())
        # 新ポリシー配備後に2つだけをロックし、どちらも実状態を再取得する。
        responses = [response_policy("Locked")]
        for index in range(2):
            responses += [response_policy(etag=f'"new-{index}"'),
                          {"statusCode": 200, "headers": {}, "body": {}}, response_policy("Locked")]
        transport = ScriptedTransport(responses)
        lifecycle.lock_and_verify_policies(self.storage, transport)
        self.assertEqual([call["path"] for call in transport.mutations()],
                         [path + '/lock?api-version=2023-05-01' for path in added])
        self.assertEqual([call["headers"] for call in transport.mutations()],
                         [{"If-Match": '"new-0"'}, {"If-Match": '"new-1"'}])
        repeated = ScriptedTransport([response_policy("Locked")] * 6)
        self.assertEqual(lifecycle.prepare_immutability(self.storage, repeated),
                         self.storage["immutabilityPolicyIds"])
        lifecycle.lock_and_verify_policies(self.storage, repeated)
        self.assertFalse(repeated.mutations())

    def test_lock_uses_exact_etag_then_reads_back(self):
        transport = ScriptedTransport([response_policy(etag='"observed-version"'), {"statusCode": 200, "headers": {}, "body": {}}, response_policy("Locked")])
        lifecycle.lock_and_verify_policies(self.storage, transport)
        self.assertEqual([call["method"] for call in transport.calls], ["GET", "POST", "GET"])
        self.assertEqual(transport.mutations()[0]["headers"], {"If-Match": '"observed-version"'})
        self.assertTrue(transport.mutations()[0]["path"].endswith("/lock?api-version=2023-05-01"))
        self.assertEqual(transport.responses, [])

    def test_missing_or_wildcard_etag_never_locks(self):
        for etag in (None, "*", '"*"'):
            with self.subTest(etag=etag):
                transport = ScriptedTransport([response_policy(etag=etag)])
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.lock_and_verify_policies(self.storage, transport)
                self.assertFalse(transport.mutations())

    def test_etag_header_is_case_insensitive(self):
        before = response_policy(etag=None)
        before["headers"]["etag"] = '"lowercase-header-version"'
        transport = ScriptedTransport([before, {"statusCode": 200, "headers": {}, "body": {}}, response_policy("Locked")])
        lifecycle.lock_and_verify_policies(self.storage, transport)
        self.assertEqual(transport.mutations()[0]["headers"], {"If-Match": '"lowercase-header-version"'})

    def test_retention_or_append_mismatch_is_rejected_before_lock(self):
        for before in (response_policy(days=2556), response_policy(append=False)):
            transport = ScriptedTransport([before])
            with self.assertRaisesRegex(lifecycle.DeploymentError, "retention/append"):
                lifecycle.lock_and_verify_policies(self.storage, transport)
            self.assertFalse(transport.mutations())

    def test_readback_must_confirm_locked_retention_and_append(self):
        for after in (response_policy("Unlocked"), response_policy("Locked", days=2556), response_policy("Locked", append=False)):
            with self.subTest(properties=after["body"]["properties"]):
                transport = ScriptedTransport([response_policy(), {"statusCode": 200, "headers": {}, "body": {}}, after])
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.lock_and_verify_policies(self.storage, transport)
                self.assertEqual(len(transport.mutations()), 1)

    def test_disabled_config_still_reads_actual_policy_but_does_not_lock(self):
        self.storage["immutabilityMode"] = "Disabled"
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": None}])
        self.assertEqual(lifecycle.prepare_immutability(self.storage, transport), [])
        lifecycle.lock_and_verify_policies(self.storage, transport)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["method"], "GET")


if __name__ == "__main__":
    unittest.main()

"""One-sided ARM key regeneration never replays an uncertain mutation or leaks keys."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from operations import api_key


APIM = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-test-svc/providers/Microsoft.ApiManagement/service/apim-test"
SUBSCRIPTION = "portal-22222222-2222-2222-2222-222222222222"
PRIMARY = "synthetic-primary-key-abcdefghijkl"
SECONDARY = "synthetic-secondary-key-abcdefghij"


class Context:
    def __init__(self, root):
        secrets = root / ".local/test/secrets"
        for directory in (root / ".local", root / ".local/test", secrets):
            directory.mkdir(mode=0o700)
        state = root / ".local/test/state"
        state.mkdir(mode=0o700)
        self.workspace = mock.Mock(secrets=secrets, repository=root, state=state)
        self.requests, self.events, self.saved, self.verified = [], [], [], []
        self.metadata = {"id": APIM + "/subscriptions/" + SUBSCRIPTION,
                         "properties": {"scope": APIM + "/products/released-apis", "state": "active"}}
        self.regeneration = {"statusCode": 204, "body": None}
        self.secret_response = {"statusCode": 200, "body": {"primaryKey": PRIMARY, "secondaryKey": SECONDARY}}
        self.get_hook = None
        self.save_failure = False
        self.receipt_failure = False
        self.lock_depth = 0

    @contextlib.contextmanager
    def locked(self):
        self.lock_depth += 1
        try:
            yield
        finally:
            self.lock_depth -= 1

    def output_path(self, path):
        return Path(path)

    def service_id(self, kind):
        assert kind == "apim"
        return APIM

    def verify_resource(self, resource_id, api, **options):
        self.verified.append((resource_id, api, options))

    def record(self, kind, payload):
        if self.receipt_failure and payload.get("status") == "completed":
            raise OSError("SECRET_SENTINEL")
        self.events.append((kind, copy.deepcopy(payload)))

    def request(self, method, resource_id, api):
        assert self.lock_depth == 1
        self.requests.append((method, resource_id, api))
        if method == "GET":
            if self.get_hook:
                self.get_hook()
            return {"statusCode": 200, "body": copy.deepcopy(self.metadata)}
        value = self.secret_response if resource_id.endswith("/listSecrets") else self.regeneration
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def save_private(self, path, value, *, overwrite):
        self.saved.append((path, value, overwrite))
        if self.save_failure and path.is_relative_to(self.workspace.secrets):
            raise OSError("SECRET_SENTINEL")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL), 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(value if isinstance(value, str) else json.dumps(value))


class OperationsApiKeyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.context = Context(self.root)
        self.output = self.context.workspace.secrets / "rotated.txt"
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.loader = self.enterContext(mock.patch.object(api_key.OperationContext, "load", return_value=self.context))
        self.enterContext(contextlib.redirect_stdout(self.stdout))
        self.enterContext(contextlib.redirect_stderr(self.stderr))

    def run_key(self, *extra, side="primary", output=None, subscription=SUBSCRIPTION):
        return api_key.main(["--config", str(self.root / ".local/test/params.local.json"),
                             "--subscription-id", subscription, "--key", side,
                             "--secret-output", str(output or self.output), *extra])

    def assert_no_secret_leak(self):
        public = self.stdout.getvalue() + self.stderr.getvalue() + json.dumps(self.context.events)
        for value in (PRIMARY, SECONDARY, "SECRET_SENTINEL"):
            self.assertNotIn(value, public)

    def test_plan_is_local_and_does_not_create_secret_or_fetch_metadata(self):
        self.assertEqual(self.run_key(), 0)
        self.assertEqual(self.context.requests, [])
        self.assertEqual(self.context.verified, [])
        self.assertFalse(self.output.exists())
        self.assertFalse(self.context.events[-1][1]["currentMetadataVerified"])

    def test_master_and_arbitrary_subscription_ids_stop_before_context_or_arm(self):
        for name in ("master", "platform-model-users", "../other", "portal-UNKNOWN"):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self.run_key(subscription=name)
        self.loader.assert_not_called()
        self.assertEqual(self.context.requests, [])

    def test_each_side_regenerates_once_and_saves_only_that_side_as_new_private_text(self):
        for side, expected in (("primary", PRIMARY), ("secondary", SECONDARY)):
            with self.subTest(side=side):
                output = self.output.with_name(side + ".txt")
                before = len(self.context.requests)
                self.assertEqual(self.run_key("--execute", side=side, output=output), 0)
                calls = self.context.requests[before:]
                self.assertEqual(calls, [("GET", APIM + "/subscriptions/" + SUBSCRIPTION, api_key.API_VERSION),
                    ("POST", APIM + "/subscriptions/" + SUBSCRIPTION + "/regenerate" + side.title() + "Key", api_key.API_VERSION),
                    ("POST", APIM + "/subscriptions/" + SUBSCRIPTION + "/listSecrets", api_key.API_VERSION)])
                self.assertEqual(output.read_text(), expected + "\n")
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                self.assertFalse(next(item[2] for item in self.context.saved if item[0] == output))
        self.assert_no_secret_leak()

    def test_foreign_or_management_product_scope_and_wrong_entity_are_rejected(self):
        for field, value in (("scope", APIM + "/apis"), ("scope", APIM + "/products/model-users"),
                             ("scope", APIM + "-other/products/released-apis"), ("state", "cancelled"),
                             ("state", "submitted"), ("id", APIM + "/subscriptions/other")):
            with self.subTest(field=field, value=value):
                current = copy.deepcopy(self.context.metadata)
                if field == "id": self.context.metadata["id"] = value
                else: self.context.metadata["properties"][field] = value
                self.context.requests.clear()
                self.assertEqual(self.run_key("--execute"), 1)
                self.assertEqual(len(self.context.requests), 1)
                self.assertEqual(self.context.requests[0][0], "GET")
                self.context.metadata = current
        self.assertFalse(self.output.exists())

    def test_parent_ownership_failure_performs_no_subscription_or_key_request(self):
        with mock.patch.object(self.context, "verify_resource", side_effect=ValueError("SECRET_SENTINEL")):
            self.assertEqual(self.run_key("--execute"), 1)
        self.assertEqual(self.context.requests, [])
        self.assert_no_secret_leak()

    def test_existing_symlink_or_nonsecret_output_is_refused_during_plan(self):
        existing = self.output.with_name("existing.txt")
        existing.write_text("preserve-original")
        linked = self.output.with_name("linked.txt")
        linked.symlink_to(self.output.with_name("absent.txt"))
        for output in (existing, linked, self.root / "public.txt"):
            with self.subTest(output=output.name):
                self.assertEqual(self.run_key(output=output), 1)
        self.assertEqual(existing.read_text(), "preserve-original")
        self.assertEqual(self.context.requests, [])

    def test_shared_output_directory_is_rejected_before_any_arm_request(self):
        self.context.workspace.secrets.chmod(0o755)
        self.assertEqual(self.run_key("--execute"), 1)
        self.assertEqual(self.context.requests, [])
        self.assertFalse(self.output.exists())

    def test_output_created_during_metadata_read_stops_before_regeneration(self):
        self.context.get_hook = lambda: self.output.write_text("another-writer")
        self.assertEqual(self.run_key("--execute"), 1)
        self.assertEqual(len(self.context.requests), 1)
        self.assertEqual(self.output.read_text(), "another-writer")

    def test_uncertain_regeneration_never_retries_or_reads_secrets(self):
        self.context.regeneration = OSError("SECRET_SENTINEL")
        self.assertEqual(self.run_key("--execute"), 1)
        self.assertEqual(len(self.context.requests), 2)
        self.assertEqual(self.context.events[-1][1]["status"], "regeneration-unconfirmed")
        self.assertEqual(self.context.events[-1][1]["regenerationAttempts"], 1)
        self.assertFalse(self.output.exists())
        self.assert_no_secret_leak()

    def test_non204_response_is_not_treated_as_regenerated_or_replayed(self):
        for status in (200, 202, 401, 403, 404, 429, 500, 504):
            with self.subTest(status=status):
                # 独立した応答fixtureへ戻す。製品の未解決intent解除の入口ではない。
                for marker in (self.context.workspace.state / "operations").glob("api-key-*.json"):
                    marker.unlink()
                self.context.requests.clear()
                self.context.regeneration = {"statusCode": status, "body": {"message": "SECRET_SENTINEL"}}
                self.assertEqual(self.run_key("--execute"), 1)
                self.assertEqual(len(self.context.requests), 2)
                self.assertFalse(self.output.exists())
        self.assert_no_secret_leak()

    def test_secret_read_failure_preserves_regenerated_not_saved_phase_without_replay(self):
        self.context.secret_response = OSError("SECRET_SENTINEL")
        self.assertEqual(self.run_key("--execute"), 1)
        self.assertEqual(len(self.context.requests), 3)
        self.assertEqual(self.context.events[-1][1]["status"], "regenerated-not-saved")
        self.assertFalse(self.context.events[-1][1]["retryRegeneration"])
        self.assertFalse(self.output.exists())
        self.assert_no_secret_leak()

    def test_invalid_secret_response_never_saves_payload_or_other_side(self):
        for body in (None, {}, {"primaryKey": "short", "secondaryKey": SECONDARY},
                     {"primaryKey": PRIMARY + "\n"}, {"primaryKey": "x" * 513}):
            with self.subTest(bodyType=type(body).__name__):
                for marker in (self.context.workspace.state / "operations").glob("api-key-*.json"):
                    marker.unlink()
                self.context.requests.clear()
                self.context.secret_response = {"statusCode": 200, "body": body}
                self.assertEqual(self.run_key("--execute"), 1)
                self.assertEqual(len(self.context.requests), 3)
                self.assertFalse(self.output.exists())
        self.assert_no_secret_leak()

    def test_save_failure_does_not_repeat_successful_regeneration(self):
        self.context.save_failure = True
        self.assertEqual(self.run_key("--execute"), 1)
        self.assertEqual(len(self.context.requests), 3)
        self.assertEqual(self.context.events[-1][1]["status"], "regenerated-not-saved")
        self.assertFalse(self.context.events[-1][1]["secretSaved"])
        self.assert_no_secret_leak()

    def test_nooverwrite_race_after_regeneration_keeps_other_writer_file(self):
        original = self.context.save_private
        def concurrent_write(path, value, *, overwrite):
            if path == self.output:
                path.write_text("another-writer")
            original(path, value, overwrite=overwrite)
        with mock.patch.object(self.context, "save_private", side_effect=concurrent_write):
            self.assertEqual(self.run_key("--execute"), 1)
        self.assertEqual(self.output.read_text(), "another-writer")
        self.assertEqual(len(self.context.requests), 3)
        self.assertEqual(self.context.events[-1][1]["status"], "regenerated-not-saved")

    def test_receipt_failure_after_save_keeps_secret_and_does_not_regenerate_again(self):
        self.context.receipt_failure = True
        self.assertEqual(self.run_key("--execute"), 1)
        self.assertEqual(self.output.read_text(), PRIMARY + "\n")
        self.assertEqual(self.context.events[-1][1]["status"], "saved-receipt-unconfirmed")
        self.assertTrue(self.context.events[-1][1]["secretSaved"])
        self.assertEqual(len(self.context.requests), 3)
        self.assert_no_secret_leak()

    def test_unresolved_intent_blocks_next_invocation_even_with_another_output(self):
        self.context.regeneration = OSError("SECRET_SENTINEL")
        self.assertEqual(self.run_key("--execute"), 1)
        before = len(self.context.requests)
        self.context.regeneration = {"statusCode": 204, "body": None}
        self.assertEqual(self.run_key("--execute", output=self.output.with_name("second.txt")), 1)
        self.assertEqual(len(self.context.requests), before)
        self.assertEqual(self.run_key(output=self.output.with_name("third.txt")), 1)
        self.assertEqual(len(self.context.requests), before)
        self.assert_no_secret_leak()

    def test_explicit_recovery_gets_current_side_once_without_regeneration_then_allows_new_rotation(self):
        self.context.regeneration = OSError("SECRET_SENTINEL")
        self.assertEqual(self.run_key("--execute"), 1)
        self.context.requests.clear()
        recovered = self.output.with_name("recovered.txt")
        self.assertEqual(self.run_key("--execute", "--recover-current", output=recovered), 0)
        self.assertEqual(self.context.requests, [
            ("GET", APIM + "/subscriptions/" + SUBSCRIPTION, api_key.API_VERSION),
            ("POST", APIM + "/subscriptions/" + SUBSCRIPTION + "/listSecrets", api_key.API_VERSION)])
        self.assertEqual(recovered.read_text(), PRIMARY + "\n")
        receipt = self.context.events[-1][1]
        self.assertEqual(receipt["status"], "current-key-recovered")
        self.assertEqual(receipt["regenerationAttempts"], 0)
        self.assertEqual(receipt["originalRegenerationOutcome"], "unconfirmed")
        self.context.regeneration = {"statusCode": 204, "body": None}
        self.assertEqual(self.run_key("--execute", output=self.output.with_name("next-rotation.txt")), 0)
        self.assert_no_secret_leak()

    def test_failed_recovery_keeps_intent_and_never_attempts_regeneration(self):
        self.context.regeneration = OSError("SECRET_SENTINEL")
        self.assertEqual(self.run_key("--execute"), 1)
        self.context.requests.clear()
        self.context.secret_response = OSError("SECRET_SENTINEL")
        self.assertEqual(self.run_key("--execute", "--recover-current"), 1)
        self.assertEqual([call[0] for call in self.context.requests], ["GET", "POST"])
        self.assertTrue(self.context.requests[-1][1].endswith("/listSecrets"))
        before = len(self.context.requests)
        self.assertEqual(self.run_key("--execute"), 1)
        self.assertEqual(len(self.context.requests), before)
        self.assert_no_secret_leak()

    def test_recovery_requires_an_exact_pending_intent_and_revalidates_scope(self):
        self.assertEqual(self.run_key("--execute", "--recover-current"), 1)
        self.assertEqual(self.context.requests, [])
        self.context.regeneration = OSError("SECRET_SENTINEL")
        self.assertEqual(self.run_key("--execute"), 1)
        self.context.requests.clear()
        self.context.metadata["properties"]["scope"] = APIM + "/apis"
        self.assertEqual(self.run_key("--execute", "--recover-current"), 1)
        self.assertEqual([call[0] for call in self.context.requests], ["GET"])
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()

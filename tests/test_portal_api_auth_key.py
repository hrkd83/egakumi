"""Exercise the dedicated Portal API auth identity for both authorization purposes."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from uuid import UUID

from deployment.helpers.portal_api_auth import deployment_settings, ensure_initial_key


class ApiStorageKeyTests(unittest.TestCase):
    def setUp(self):
        subscriber = str(UUID(int=111, version=4))
        tenant = str(UUID(int=112, version=4))
        self.identity_scope = (
            "/subscriptions/" + subscriber + "/resourceGroups/rg-synthetic-test-id/"
            "providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
        )
        self.identity = {
            "id": self.identity_scope + "id-synthetic-test-portal-api-auth",
            "clientId": str(UUID(int=114, version=4)),
        }
        self.outputs = {"apiAuthIdentity": self.identity}
        self.config = {
            "entraTenantId": tenant,
            "resourceStem": "synthetic-test",
            "portal": {
                "authentication": {"tenantId": tenant},
                "apiAuthentication": {"enabled": True, "previousKeyVersions": ["a" * 32]},
            },
            "gateway": {
                "authentication": {
                    "entra": {"audience": "synthetic-api", "requiredScope": "Gateway.Invoke"},
                },
            },
        }

    def test_subscription_management_can_use_the_dedicated_identity_without_trial_bearer(self):
        self.assertEqual(
            deployment_settings(self.config, self.outputs, self.identity_scope, management=True),
            {
                "identity": self.identity,
                "scope": "api://synthetic-api/Gateway.Invoke",
                "previousKeyVersions": ["a" * 32],
            },
        )

        self.config["portal"]["apiAuthentication"]["enabled"] = False
        self.assertEqual(deployment_settings(self.config, self.outputs, self.identity_scope), {})
        self.assertEqual(
            deployment_settings(self.config, self.outputs, self.identity_scope, management=True),
            {"identity": self.identity, "scope": "", "previousKeyVersions": ["a" * 32]},
        )

    def test_subscription_management_rejects_mismatched_foundation_identity_and_tenant(self):
        wrong_identity = {"apiAuthIdentity": {**self.identity, "id": self.identity["id"] + "-other"}}
        with self.assertRaisesRegex(ValueError, "dedicated identity"):
            deployment_settings(self.config, wrong_identity, self.identity_scope, management=True)

        self.config["portal"]["authentication"]["tenantId"] = "different-entra"
        with self.assertRaisesRegex(ValueError, "dedicated identity"):
            deployment_settings(self.config, self.outputs, self.identity_scope, management=True)

    def test_missing_explicit_storage_key_version_stops_before_any_creation(self):
        self.config.update({
            "subscriptionId": str(UUID(int=111, version=4)), "azureTenantId": str(UUID(int=113, version=4)),
            "resourceGroupNames": {"svc": "rg-synthetic-test-svc"},
        })
        self.config["portal"]["apiAuthentication"]["keyVersion"] = "b" * 32
        transport = Mock()
        transport.request.return_value = {"statusCode": 404}
        save = Mock()
        with TemporaryDirectory() as directory:
            workspace = SimpleNamespace(checked=lambda path: path, state=Path(directory))
            with self.assertRaisesRegex(ValueError, "configured Portal API storage key version is missing"):
                ensure_initial_key(self.config, workspace, transport, save)
        self.assertEqual([call.args[0] for call in transport.request.call_args_list], ["GET"])
        save.assert_not_called()

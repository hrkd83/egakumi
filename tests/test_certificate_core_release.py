"""Coreの初回発行と公開順序を、合成状態・応答だけで確認する。"""
from contextlib import nullcontext
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from lifecycle_fixtures import fixture, normalize_configuration
from deployment import lifecycle
from deployment.helpers.portal_gateway import gateway_id


class CertificateCoreReleaseTests(unittest.TestCase):
    def setUp(self):
        self.config = normalize_configuration(fixture())
        self.outputs = {
            'certificateBootstrap': {'certificateSecretUri': 'https://vault.example.com/secrets/tls/'},
            'gatewayPlan': {},
            'ingressMonitoringPlan': {},
            'ingressSecrets': {'enabled': False},
            'endpoints': {'foundry': 'https://backend.example.com', 'public': ''},
        }
        self.gateway = {
            'id': gateway_id(self.config),
            'publicUrl': 'https://' + self.config['gateway']['publicHostName'],
        }
        self.issued = {'tls': {'status': 'issued'}, 'portal': {'status': 'issued'}, 'imageDigest': 'sha256:' + 'a' * 64}
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name).resolve()
        self.workspace = mock.Mock(params=self.path / 'params.local.json', state=self.path)

    def finish(self, *, issuance_error=False, live_tls_error=False):
        events = []

        def checkpoint(*args):
            events.append('published-state' if args[1]['ingressSecrets']['enabled'] else 'prepared-state')

        def issue(*args, **kwargs):
            events.append('issue')
            if issuance_error:
                raise ValueError('Job pending')
            return self.issued

        def public_tls(*args, **kwargs):
            events.append('live-tls')
            if live_tls_error:
                raise lifecycle.DeploymentError('Expected certificate still unavailable')

        with mock.patch.object(lifecycle, 'local_paths', return_value=self.workspace), \
             mock.patch.object(lifecycle, 'state_from_outputs', return_value={}), \
             mock.patch('operations.common.OperationContext') as context, \
             mock.patch.object(lifecycle, '_save_deployment_state', side_effect=checkpoint), \
             mock.patch.object(lifecycle, '_bind_management_serial_password'), \
             mock.patch.object(lifecycle, 'verify_core_network_after_deployment'), \
             mock.patch('deployment.helpers.certificate_jobs.complete_initial_certificates', side_effect=issue), \
             mock.patch('deployment.helpers.portal_auth_certificate.store_initial_certificate',
                        side_effect=lambda *args: events.append('portal-public-certificate')), \
             mock.patch.object(lifecycle, 'deploy_public_ingress',
                               side_effect=lambda *args: events.append('appgw') or self.gateway), \
             mock.patch.object(lifecycle, 'require_appgw_backend_tls_ready'), \
             mock.patch.object(lifecycle, 'observe_initial_certificate', side_effect=public_tls), \
             mock.patch.object(lifecycle, 'verify_apim_endpoint',
                               side_effect=lambda *args, **kwargs: events.append('ingress-settings') or {}), \
             mock.patch.object(lifecycle, 'finish_public_dns',
                               side_effect=lambda *args: events.append('public-dns') or {'status': 'configured'}), \
             mock.patch.object(lifecycle, 'protected_json'):
            context.return_value.locked.return_value = nullcontext()
            if issuance_error:
                with self.assertRaisesRegex(ValueError, 'Job pending'):
                    lifecycle.finish_certificate_core(self.config, self.outputs, mock.Mock(), '', 'test', '')
            elif live_tls_error:
                with self.assertRaisesRegex(lifecycle.DeploymentError, 'still unavailable'):
                    lifecycle.finish_certificate_core(self.config, self.outputs, mock.Mock(), '', 'test', '')
            else:
                final, dns = lifecycle.finish_certificate_core(self.config, self.outputs, mock.Mock(), '', 'test', '')
                self.assertTrue(final['ingressSecrets']['enabled'])
                self.assertEqual(final['endpoints']['public'], self.gateway['publicUrl'])
                self.assertEqual(final['certificateBootstrap']['imageDigest'], self.issued['imageDigest'])
                self.assertEqual(dns['status'], 'configured')
        return events

    def test_certificate_registry_preflight_preserves_stricter_existing_configuration(self):
        config = self.config
        name = "acr" + config["resourceStem"].replace("-", "")
        registry_id = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}"
                       f"/providers/Microsoft.ContainerRegistry/registries/{name}")
        transport = mock.Mock()
        transport.request.return_value = {"statusCode": 404, "body": None}
        lifecycle.require_certificate_registry_ready(config, transport)
        transport.request.assert_called_once_with("GET", registry_id + "?api-version=2025-11-01")

        registry = {"id": registry_id, "sku": {"name": "Premium"}, "tags": {
            "product": config["productName"], "environment": config["environmentName"]}, "properties": {
            "publicNetworkAccess": "Disabled", "networkRuleBypassOptions": "None", "adminUserEnabled": False,
            "dataEndpointEnabled": True, "roleAssignmentMode": "LegacyRegistryPermissions",
            "policies": {"azureADAuthenticationAsArmPolicy": {"status": "enabled"}}}}
        transport.request.return_value = {"statusCode": 200, "body": registry}
        lifecycle.require_certificate_registry_ready(config, transport)
        registry["properties"]["roleAssignmentMode"] = "AbacRepositoryPermissions"
        with self.assertRaisesRegex(lifecycle.DeploymentError, "private registry"):
            lifecycle.require_certificate_registry_ready(config, transport)
        registry["tags"]["product"] = "another-product"
        with self.assertRaisesRegex(lifecycle.DeploymentError, "selected environment"):
            lifecycle.require_certificate_registry_ready(config, transport)
        self.assertTrue(all(call.args[0] == "GET" for call in transport.request.call_args_list))

    def test_only_new_secret_role_propagation_is_eligible_for_ingress_retry(self):
        role = {"code": "UserAssignedIdentityDoesNotHaveGetPermissionOnKeyVault"}
        invalid = {"code": "KeyVaultHasRestrictedAccess"}
        self.assertTrue(lifecycle.waiting_for_appgw_certificate_role(
            {"code": "DeploymentFailed", "details": [{"code": "ResourceDeploymentFailure", "details": [role]}]}))
        self.assertFalse(lifecycle.waiting_for_appgw_certificate_role(
            {"code": "DeploymentFailed", "details": [role, invalid]}))

    def test_only_owned_provisional_ingress_can_resume_the_native_hook(self):
        config, outputs = self.config, self.outputs
        state = {key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem', 'resourceGroupNames')}
        state.update(apimInstanceId=lifecycle.apim_instance_id(config), gatewayPlan=outputs['gatewayPlan'],
                     ingressMonitoringPlan=outputs['ingressMonitoringPlan'])
        state_path = self.path / 'public-ingress-pending.json'
        state_path.write_text(json.dumps(state), encoding='utf-8')
        state_path.chmod(0o600)
        self.workspace.checked.side_effect = lambda path: path
        operation = {
            'id': lifecycle.public_ingress_deployment_id(config),
            'tags': {'azd-env-name': config['environmentName']},
            'properties': {'provisioningState': 'Failed',
                           'parameters': {key: {'value': outputs[key]} for key in ('gatewayPlan', 'ingressMonitoringPlan')}},
        }
        appgw = {'id': self.gateway['id'],
                 'tags': {'environment': config['environmentName'], 'product': config['productName']},
                 'properties': {'provisioningState': 'Failed'}}
        with mock.patch.object(lifecycle, 'local_paths', return_value=self.workspace), \
             mock.patch.object(lifecycle, 'require_appgw_backend_tls_ready', side_effect=ValueError('TLS gate')):
            transport = mock.Mock()
            transport.request.side_effect = [{'statusCode': 200, 'body': operation}, {'statusCode': 200, 'body': appgw}]
            self.assertTrue(lifecycle.require_core_appgw_ready(config, transport)['certificateReleasePending'])
            appgw['tags']['product'] = 'another-product'
            transport.request.side_effect = [{'statusCode': 200, 'body': operation}, {'statusCode': 200, 'body': appgw}]
            with self.assertRaisesRegex(ValueError, 'TLS gate'):
                lifecycle.require_core_appgw_ready(config, transport)

    def test_public_entry_waits_for_owned_issuance_and_live_tls(self):
        self.assertEqual(self.finish(), ['prepared-state', 'issue', 'portal-public-certificate', 'appgw',
                                         'live-tls', 'ingress-settings', 'public-dns', 'published-state'])

    def test_pending_issuance_never_deploys_appgw_or_dns(self):
        self.assertEqual(self.finish(issuance_error=True), ['prepared-state', 'issue'])

    def test_appgw_with_unconfirmed_leaf_never_advertises_public_dns_or_complete_state(self):
        self.assertEqual(self.finish(live_tls_error=True),
                         ['prepared-state', 'issue', 'portal-public-certificate', 'appgw', 'live-tls'])


if __name__ == '__main__':
    unittest.main()

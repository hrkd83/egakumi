"""Synthetic ARM transports and isolated cleanup resources shared by test suites."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import cleanup
from deployment.helpers.configuration import load_configuration


class FakeTransport:
    """ARM HTTP要求を受ける偽物。削除対象と要求の順番を記録し、Azureを呼ばずに撤去を試す。"""
    def __init__(self, resources=None):
        self.resources = copy.deepcopy(resources or {})
        self.calls = []
        self.forced = {}
        self.keep_after_delete = set()
        self.policy_state_after_delete = None

    def request(self, method, path, *, body=None, headers=None):
        resource_id = path.split("?", 1)[0]
        self.calls.append({"method": method, "id": resource_id, "path": path, "headers": headers, "body": body})
        if (method, resource_id) in self.forced:
            return {"statusCode": self.forced[method, resource_id], "body": {"error": {"message": "PRIVATE_SENTINEL"}}, "headers": {}}
        if method == "GET" and resource_id.endswith("/resources"):
            # 実ARMのRG一覧はid/name/typeを持つ資源のvalue配列。404や削除履歴で代用しない。
            group = resource_id[:-len("/resources")]
            values = []
            for key, value in self.resources.items():
                if not key.casefold().startswith((group + "/providers/").casefold()):
                    continue
                parts = key.split("/")
                provider = max(i for i, part in enumerate(parts) if part.casefold() == "providers")
                tail = parts[provider + 1:]
                kind = "/".join([tail[0], *tail[1::2]])
                values.append({"id": key, "name": key.rsplit("/", 1)[-1], "type": kind})
            return {"statusCode": 200, "body": {"value": values}, "headers": {}}
        if method == "GET":
            found = self.resources.get(resource_id)
            return {"statusCode": 200 if found is not None else 404, "body": copy.deepcopy(found), "headers": {}}
        if method == "POST" and resource_id.endswith("/purge"):
            target = resource_id[:-6]
        elif method == "DELETE":
            target = resource_id
        else:
            raise AssertionError("Unexpected transport mutation")
        if target not in self.keep_after_delete:
            if "/immutabilityPolicies/" in target and target in self.resources:
                self.resources[target]["properties"]["immutabilityPeriodSinceCreationInDays"] = 0
                if self.policy_state_after_delete is not None:
                    self.resources[target]["properties"]["state"] = self.policy_state_after_delete
            else:
                self.resources.pop(target, None)
        return {"statusCode": 200, "body": {}, "headers": {}}

    def mutations(self):
        return [call for call in self.calls if call["method"] != "GET"]


class ApiCenterListTransport(FakeTransport):
    """API Centerの一覧ページだけを順番に返す。実HTTPには接続しない。"""
    def __init__(self, collection_id, pages, resources=None):
        super().__init__(resources)
        self.collection_id = collection_id
        self.pages = copy.deepcopy(pages)

    def request(self, method, path, *, body=None, headers=None):
        if method == "GET" and urlsplit(path).path == self.collection_id:
            self.calls.append({"method": method, "id": self.collection_id, "path": path, "headers": headers, "body": body})
            if not self.pages:
                raise AssertionError("Unexpected API Center page request")
            return {"statusCode": 200, "body": self.pages.pop(0), "headers": {}}
        return super().request(method, path, body=body, headers=headers)


class ModelTransport(FakeTransport):
    """Represent paged models, delayed deletes and unrelated accounts offline."""
    def __init__(self, account, deployments, *, page_size=1):
        self.account_id = account["id"]
        self.collection_id = self.account_id + "/deployments"
        super().__init__({self.account_id: account, **{item["id"]: item for item in deployments}})
        self.page_size = page_size
        self.next_link_override = None
        self.inventory_extra = []
        self.delayed_reads = {}
        self.pending_delete = set()

    def request(self, method, path, *, body=None, headers=None):
        parsed = urlsplit(path)
        resource_id = parsed.path
        if method == "GET" and resource_id == self.collection_id:
            self.calls.append({"method": method, "id": resource_id, "path": path, "headers": headers, "body": body})
            forced = self.forced.get((method, resource_id))
            if forced:
                return {"statusCode": forced, "body": {"error": {"message": "PRIVATE_SENTINEL"}}, "headers": {}}
            models = [value for key, value in self.resources.items() if key.startswith(self.collection_id + "/")] + self.inventory_extra
            start = int(parse_qs(parsed.query).get("$skiptoken", ["0"])[0])
            page = {"value": copy.deepcopy(models[start:start + self.page_size])}
            if start + self.page_size < len(models):
                page["nextLink"] = "https://management.azure.com" + self.collection_id + "?api-version=2025-06-01&$skiptoken=" + str(start + self.page_size)
                if self.next_link_override:
                    page["nextLink"] = self.next_link_override
            return {"statusCode": 200, "body": page, "headers": {}}
        if method == "DELETE" and resource_id in self.delayed_reads:
            self.calls.append({"method": method, "id": resource_id, "path": path, "headers": headers, "body": body})
            self.pending_delete.add(resource_id)
            return {"statusCode": 202, "body": {}, "headers": {}}
        if method == "GET" and resource_id in self.pending_delete:
            if self.delayed_reads[resource_id] <= 0:
                self.resources.pop(resource_id, None)
                self.pending_delete.remove(resource_id)
            else:
                self.delayed_reads[resource_id] -= 1
        return super().request(method, path, body=body, headers=headers)


class CredentialTransport(ModelTransport):
    """CM の一覧・ETag・個別削除を再現し、親削除による子の消失には依存しない。"""
    def __init__(self, model_transport):
        account = model_transport.resources[model_transport.account_id]
        models = [value for key, value in model_transport.resources.items()
                  if key.startswith(model_transport.collection_id + "/")]
        super().__init__(account, models)
        self.provider_headers = {}
        self.provider_get_counts = {}
        self.provider_fresh_results = {}
        self.cm_collection_pages = {}
        self.cm_collection_extras = {}
        self.cm_next_links = {}
        self.cm_page_size = 1
        self.etag_after_child_delete = {}

    def request(self, method, path, *, body=None, headers=None):
        parsed = urlsplit(path)
        resource_id = parsed.path
        if method == "GET" and resource_id.endswith(("/authorizationProviders", "/authorizations", "/accessPolicies")):
            self.calls.append({"method": method, "id": resource_id, "path": path, "headers": headers, "body": body})
            if (method, resource_id) in self.forced:
                return {"statusCode": self.forced[method, resource_id], "body": {"error": {"message": "PRIVATE_SENTINEL"}}, "headers": {}}
            pages = self.cm_collection_pages.get(resource_id)
            if pages:
                return copy.deepcopy(pages.pop(0))
            prefix = resource_id + "/"
            children = [value for key, value in self.resources.items()
                        if key.startswith(prefix) and "/" not in key[len(prefix):]]
            children += self.cm_collection_extras.get(resource_id, [])
            start = int(parse_qs(parsed.query).get("$skiptoken", ["0"])[0])
            page = {"value": copy.deepcopy(children[start:start + self.cm_page_size])}
            if start + self.cm_page_size < len(children):
                page["nextLink"] = self.cm_next_links.get(resource_id,
                    "https://management.azure.com" + resource_id + "?api-version=2024-05-01&$skiptoken=" + str(start + self.cm_page_size))
            return {"statusCode": 200, "body": page, "headers": {}}
        if method == "GET" and resource_id in self.provider_headers:
            count = self.provider_get_counts.get(resource_id, 0) + 1
            self.provider_get_counts[resource_id] = count
            if count == 2 and resource_id in self.provider_fresh_results:
                # ETag未提供時の再GETに、更新・消失・読取拒否を再現する。
                self.calls.append({"method": method, "id": resource_id, "path": path, "headers": headers, "body": body})
                fresh = copy.deepcopy(self.provider_fresh_results[resource_id])
                if fresh["statusCode"] == 404:
                    self.resources.pop(resource_id, None)
                return fresh
        response = super().request(method, path, body=body, headers=headers)
        if method == "GET" and response["statusCode"] == 200 and resource_id in self.provider_headers:
            response["headers"] = copy.deepcopy(self.provider_headers[resource_id])
        if method == "DELETE" and resource_id not in self.keep_after_delete:
            # 子削除で親の版が変わり得る実サービスを再現する。
            for parent_id, changed in self.etag_after_child_delete.items():
                if resource_id.startswith(parent_id + "/"):
                    self.provider_headers[parent_id] = copy.deepcopy(changed)
        return response


class ProviderGetInconsistencyTransport(CredentialTransport):
    """DELETE済みproviderは一覧から消え、個別GETだけ旧entityを返す実RP形を再現する。"""
    def __init__(self, original, provider_id, *, delete_status=200):
        self.__dict__ = copy.deepcopy(original.__dict__)
        self.inconsistent_provider_id = provider_id
        self.provider_delete_status = delete_status
        self.deleted_provider_gets = {}
        self.after_delete_mutation = None

    def request(self, method, path, *, body=None, headers=None):
        resource_id = urlsplit(path).path
        if method == "GET" and resource_id in self.deleted_provider_gets:
            self.calls.append({"method": method, "id": resource_id, "path": path, "headers": headers, "body": body})
            current = copy.deepcopy(self.deleted_provider_gets[resource_id])
            if self.after_delete_mutation:
                self.after_delete_mutation(current)
            return {"statusCode": 200, "body": current,
                    "headers": copy.deepcopy(self.provider_headers.get(resource_id, {}))}
        if method == "DELETE" and resource_id == self.inconsistent_provider_id:
            original = copy.deepcopy(self.resources[resource_id])
            response = super().request(method, path, body=body, headers=headers)
            if self.provider_delete_status in (200, 204):
                self.deleted_provider_gets[resource_id] = original
            return {**response, "statusCode": self.provider_delete_status}
        return super().request(method, path, body=body, headers=headers)


class InventoryTransport:
    """実RG一覧だけをページ列で再現し、他要求は既存transportへ渡す。"""
    def __init__(self, original, collection, pages):
        self.original, self.collection, self.pages = original, collection, copy.deepcopy(pages)

    def __getattr__(self, name):
        return getattr(self.original, name)

    def request(self, method, path, *, body=None, headers=None):
        if method == "GET" and urlsplit(path).path == self.collection:
            self.calls.append({"method": method, "id": self.collection, "path": path, "headers": headers, "body": body})
            if not self.pages:
                raise AssertionError("Unexpected extra RG inventory read")
            return copy.deepcopy(self.pages.pop(0))
        return self.original.request(method, path, body=body, headers=headers)


class ManagementTransport(ModelTransport):
    """実VMの非同期削除とdeleteOptionによる専用NICの回収を再現する。"""
    def __init__(self, original, vm_id, nic_id, *, auto_nic=False):
        self.__dict__ = copy.deepcopy(original.__dict__)
        self.management_vm_id, self.management_nic_id, self.auto_nic = vm_id, nic_id, auto_nic
        self.after_vm_deleted = None

    def request(self, method, path, *, body=None, headers=None):
        resource_id = urlsplit(path).path
        response = super().request(method, path, body=body, headers=headers)
        if resource_id == self.management_vm_id and resource_id not in self.resources:
            if self.auto_nic:
                self.resources.pop(self.management_nic_id, None)
            if self.after_vm_deleted:
                self.after_vm_deleted(self)
                self.after_vm_deleted = None
        return response


class CleanupFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "configuration.json"
        from configuration_fixtures import fixture
        self.raw = fixture()
        self.write_configuration()
        sub = self.configuration["subscriptionId"]
        groups = self.configuration["resourceGroupNames"]
        self.monitor_rg = f"/subscriptions/{sub}/resourceGroups/{groups['monitor']}"
        self.svc_rg = f"/subscriptions/{sub}/resourceGroups/{groups['svc']}"
        accounts = [self.monitor_rg + "/providers/Microsoft.Storage/storageAccounts/" + name
                    for name in ("sttestegakumijp", "sttestegakumius")]
        self.policy_ids = [account + "/blobServices/default/containers/insights-logs-audit/immutabilityPolicies/default" for account in accounts]
        self.diag_id = f"/subscriptions/{sub}/providers/Microsoft.Insights/diagnosticSettings/diag-test-egakumi-activity"
        self.workspace_id = self.monitor_rg + "/providers/Microsoft.OperationalInsights/workspaces/law-test-egakumi"
        self.state = {
            "subscriptionId": sub, "azureTenantId": self.configuration["azureTenantId"], "resourceStem": "test-egakumi",
            "resourceGroupNames": groups,
            "storage": {"collectJapan": {"id": accounts[0]}, "collectFoundry": {"id": accounts[1]}, "immutabilityPolicyIds": self.policy_ids},
            "monitoring": {"workspaceId": self.workspace_id},
            "serviceResourceIds": {
                "keyVault": self.svc_rg + "/providers/Microsoft.KeyVault/vaults/kv-test-egakumi",
                "foundry": self.svc_rg + "/providers/Microsoft.CognitiveServices/accounts/aif-test-egakumi",
                "apim": self.svc_rg + "/providers/Microsoft.ApiManagement/service/apim-test-egakumi",
            },
            "serviceLocations": {"keyVault": "japaneast", "foundry": "eastus2", "apim": "japaneast"},
            "outputs": {"names": {"apiCenter": "apic-test-egakumi"}},
        }

    def write_configuration(self):
        self.config_path.write_text(json.dumps(self.raw))
        self.configuration = load_configuration(self.config_path)

    def policy(self, resource_id, mode="Unlocked", etag='"version-1"'):
        return {"id": resource_id, "etag": etag, "properties": {"state": mode, "immutabilityPeriodSinceCreationInDays": 2557}}

    def model_transport(self, count=2):
        account_id = self.state["serviceResourceIds"]["foundry"]
        account = {"id": account_id, "name": account_id.rsplit("/", 1)[1], "type": "Microsoft.CognitiveServices/accounts", "kind": "AIServices", "location": "eastus2",
                   "tags": {"product": "egakumi", "environment": "test"}}
        deployments = [{"id": account_id + "/deployments/model-" + str(index), "name": "model-" + str(index),
                        "type": "Microsoft.CognitiveServices/accounts/deployments", "properties": {"provisioningState": "Succeeded"}}
                       for index in range(count)]
        return ModelTransport(account, deployments)

    def apim(self):
        """実配備と同じ固定の標準名で、所有確認可能なAPIMの応答を返す。"""
        resource_id = self.state["serviceResourceIds"]["apim"]
        return {"id": resource_id, "name": resource_id.rsplit("/", 1)[-1],
                "type": "Microsoft.ApiManagement/service", "location": "Japan East",
                "tags": {"product": "egakumi", "environment": "test"}}

    def tombstones(self):
        current = cleanup._validate_state(self.configuration, self.state)
        result = {}
        for key, original in self.state["serviceResourceIds"].items():
            tombstone = cleanup._tombstone(self.configuration, current, key)
            properties = {"purgeProtectionEnabled": False}
            if key == "keyVault": properties["vaultId"] = original
            if key == "apim": properties["serviceId"] = original
            result[tombstone] = {"id": tombstone, "properties": properties}
        return result

    def pre(self, transport, **options):
        return cleanup.pre_down(self.config_path, self.state, transport, max_checks=2, wait_seconds=0,
                                apim_max_checks=2, apim_wait_seconds=0, **options)

    def post(self, transport, **options):
        # 通常ケースは削除済みAPI Centerなし。専用ケースでは明示した一覧を使う。
        collection = self.svc_rg + "/providers/Microsoft.ApiCenter/deletedServices"
        transport.resources.setdefault(collection, {"value": []})
        return cleanup.post_down(self.config_path, self.state, transport, max_checks=2, wait_seconds=0, **options)

    def assert_code(self, code, function):
        with self.assertRaises(cleanup.CleanupError) as raised:
            function()
        self.assertEqual(code, raised.exception.code)
        self.assertNotIn("PRIVATE_SENTINEL", str(raised.exception))

    def api_center_tombstone(self):
        """Bicep出力の名前とexact svc RGに一致する、削除済み一件を作る。"""
        name = self.state["outputs"]["names"]["apiCenter"]
        return {"id": self.svc_rg + "/providers/Microsoft.ApiCenter/deletedServices/" + name,
                "name": name, "type": "Microsoft.ApiCenter/deletedServices", "properties": {
                    "softDeletionDate": "2026-10-19T08:17:39.1335362Z",
                    "scheduledPurgeDate": "2026-10-26T08:17:39.1335362Z"}}

    def credential_transport(self, names=("approved-oauth",), models=0):
        """所有 APIM と設定済み provider を用意する。実 secret ファイルは作成しない。"""
        self.raw["credentialProviders"] = [{"name": name, "displayName": name, "identityProvider": "aad",
            "clientId": "44444444-4444-4444-8444-444444444444", "clientSecretFile": "secrets/not-read-by-cleanup.txt",
            "resourceUri": "https://graph.microsoft.com", "scopes": "User.Read", "connectionNames": ["owner-1"]}
            for name in names]
        self.write_configuration()
        transport = CredentialTransport(self.model_transport(models))
        apim_id = self.state["serviceResourceIds"]["apim"]
        transport.resources[apim_id] = self.apim()
        for name in names:
            provider_id = apim_id + "/authorizationProviders/" + name
            transport.resources[provider_id] = {"id": provider_id, "name": name,
                "type": "Microsoft.ApiManagement/service/authorizationProviders",
                "properties": {"displayName": name, "syntheticPrivateField": "PRIVATE_SENTINEL"}}
            transport.provider_headers[provider_id] = {"eTaG": '"provider-version-1"'}
        return transport

    def credential_children(self, transport, provider_id, names=("owner-1",), policies=1):
        """正しい子ID・種類・公開識別項目とETagを持つCM接続を追加する。"""
        result = []
        for name in names:
            authorization_id = provider_id + "/authorizations/" + name
            transport.resources[authorization_id] = {
                "id": authorization_id, "name": name,
                "type": "Microsoft.ApiManagement/service/authorizationProviders/authorizations",
                "properties": {"authorizationType": "OAuth2", "oauth2grantType": "AuthorizationCode",
                               "syntheticPrivateField": "PRIVATE_SENTINEL"},
            }
            transport.provider_headers[authorization_id] = {"ETag": '"authorization-version-1"'}
            policy_ids = []
            for index in range(policies):
                policy_name = "opaque-owner-" + str(index)
                policy_id = authorization_id + "/accessPolicies/" + policy_name
                transport.resources[policy_id] = {
                    "id": policy_id, "name": policy_name,
                    "type": "Microsoft.ApiManagement/service/authorizationProviders/authorizations/accessPolicies",
                    "properties": {"objectId": "44444444-4444-4444-8444-444444444444",
                                   "tenantId": self.configuration["azureTenantId"]},
                }
                transport.provider_headers[policy_id] = {"ETag": '"policy-version-1"'}
                policy_ids.append(policy_id)
            result.append((authorization_id, policy_ids))
        return result

    def missing_etag_transport(self, models=0):
        """実CMの公開メタデータと、ETagを返さないGETを再現する。"""
        transport = self.credential_transport(models=models)
        provider_id = next(iter(transport.provider_headers))
        transport.provider_headers[provider_id] = {}
        transport.resources[provider_id]["properties"].update({"identityProvider": "aad", "oauth2": {
            "redirectUrl": "https://authorization-manager.consent.azure-apim.net/redirect/apim/fixture",
            "grantTypes": {"authorizationCode": {"clientId": "44444444-4444-4444-8444-444444444444",
                "tenantId": self.configuration["azureTenantId"], "resourceUri": "https://graph.microsoft.com",
                "scopes": "User.Read", "clientSecret": "PRIVATE_SENTINEL"}}}})
        return transport, provider_id

    def provider_get_inconsistency_transport(self, *, status=200, children=True):
        original, provider_id = self.missing_etag_transport()
        if children:
            self.credential_children(original, provider_id)
        return ProviderGetInconsistencyTransport(original, provider_id, delete_status=status), provider_id

    def management_transport(self, *, auto_nic=False, models=0):
        """IaCの実出力・VMのdeleteOption・NICの逆参照を持つ最小ARM形。"""
        stem = self.configuration["resourceStem"]
        maint = cleanup._rg_id(self.configuration, "maint")
        vm_id = maint + "/providers/Microsoft.Compute/virtualMachines/vm-" + stem + "-mgmt"
        nic_id = maint + "/providers/Microsoft.Network/networkInterfaces/nic-" + stem + "-mgmt"
        disk_id = maint + "/providers/Microsoft.Compute/disks/osdisk-" + stem + "-mgmt"
        subnet_id = cleanup._rg_id(self.configuration, "nw") + "/providers/Microsoft.Network/virtualNetworks/vnet-" + stem + "/subnets/snet-" + stem + "-vm"
        principal = "44444444-4444-4444-8444-444444444444"
        self.state["outputs"]["managementVm"] = {"id": vm_id, "name": vm_id.rsplit("/", 1)[-1],
            "nicId": nic_id, "osDiskId": disk_id, "principalId": principal}
        transport = ManagementTransport(self.model_transport(models), vm_id, nic_id, auto_nic=auto_nic)
        tags = {"product": "egakumi", "environment": "test"}
        transport.resources[vm_id] = {"id": vm_id, "name": vm_id.rsplit("/", 1)[-1],
            "type": "Microsoft.Compute/virtualMachines", "location": "japaneast", "tags": tags,
            "identity": {"type": "SystemAssigned", "principalId": principal},
            "properties": {"provisioningState": "Succeeded", "networkProfile": {"networkInterfaces": [
                {"id": nic_id, "properties": {"primary": True, "deleteOption": "Delete"}}]},
                "storageProfile": {"osDisk": {"managedDisk": {"id": disk_id}, "deleteOption": "Delete"}, "dataDisks": []}}}
        transport.resources[nic_id] = {"id": nic_id, "name": nic_id.rsplit("/", 1)[-1],
            "type": "Microsoft.Network/networkInterfaces", "location": "japaneast", "tags": tags,
            "properties": {"provisioningState": "Succeeded", "virtualMachine": {"id": vm_id},
                "ipConfigurations": [{"name": "ipconfig-primary", "properties": {"primary": True, "subnet": {"id": subnet_id}}}]}}
        return transport

    def azure_dns_management_transport(self, *, models=0):
        """別RGのDNS用IDを併用する、実際の管理VMの割り当てを再現する。"""
        dns_id = ("/subscriptions/22222222-2222-4222-8222-222222222222/resourceGroups/rg-shared-dns"
                  "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-shared-dns")
        self.raw["certificate"].update(plugin="Azure",
            azureDnsSubscriptionId="22222222-2222-4222-8222-222222222222",
            azureDnsManagedIdentityResourceId=dns_id)
        self.raw["certificate"].pop("pluginArgumentsFile", None)
        self.write_configuration()
        transport = self.management_transport(models=models)
        identity = transport.resources[transport.management_vm_id]["identity"]
        identity.update(type="SystemAssigned, UserAssigned", userAssignedIdentities={dns_id: {}})
        return transport, dns_id

    def management_module(self, transport):
        """mainの未保存出力を、成功済みの正確なnested deploymentで再現する。"""
        module_id = cleanup._rg_id(self.configuration, "maint") + "/providers/Microsoft.Resources/deployments/test-egakumi-management"
        transport.resources[module_id] = {"id": module_id, "name": "test-egakumi-management",
            "type": "Microsoft.Resources/deployments", "properties": {"provisioningState": "Succeeded", "outputs": {
                "managementVm": {"type": "Object", "value": copy.deepcopy(self.state["outputs"]["managementVm"])}}}}
        return module_id

"""構築済み基盤へAPIの差分だけを反映する計画を検証する。

偽のARM transportで対象・権限・モデル版・外部FQDNを照合する。通常は計画作成だけで、明示した実行時だけ変更する境界を確認する。
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/gateway/deploy"))
import api_publication as publish
from deployment.helpers.configuration import build_bicep_configuration
from deployment.helpers.apim_authentication import render_service_policy
from deployment.helpers.subscription_profiles import DEFAULTS as PROFILE_DEFAULTS, PREFIX as PROFILE_PREFIX
import render_apim_policy


def ok(body, status=200, headers=None):
    """偽transportから返す成功応答を作る。実ARM接続の代わりに使う。"""
    return {"statusCode": status, "body": copy.deepcopy(body), "headers": headers or {}}


class Transport:
    """ネットワークの代わりに決めた応答を返し、対象ID・更新順・失敗時の停止を調べる。"""
    def __init__(self, inventory):
        self.inventory = inventory
        self.calls = []

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, copy.deepcopy(body), headers))
        resource_id = path.split("?", 1)[0]
        if method == "GET":
            if resource_id.endswith("/products") and resource_id not in self.inventory:
                return ok({"value": []})
            if resource_id not in self.inventory and resource_id.endswith(("/apis/echo-api", "/products/starter", "/products/unlimited")):
                return ok({}, 404)
            return copy.deepcopy(self.inventory[resource_id])
        if method == "PUT":
            observed = {"id": resource_id, **copy.deepcopy(body)}
            observed["properties"]["provisioningState"] = "Succeeded"
            if "/deployments/" in resource_id:
                apim = self.inventory[resource_id]["body"]["fixtureApimId"]
                observed["properties"]["outputs"] = {"publishedApiResourceIds": {"value": [apim + "/apis/model", apim + "/apis/sample"]}}
            self.inventory[resource_id] = ok(observed)
            return ok(observed, 202)
        raise AssertionError("Unexpected ARM method")


class PublicationTests(unittest.TestCase):
    """構築済み基盤へAPIの差分だけを反映する計画を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def setUp(self):
        self.config = {"subscriptionId": "11111111-1111-4111-8111-111111111111",
            "azureTenantId": "22222222-2222-4222-8222-222222222222", "resourceStem": "test-fixture",
            "productName": "fixture", "environmentName": "test", "location": "japaneast", "foundryLocation": "eastus2",
            "services": {"apimInstanceId": "abc123"},
            "resourceGroupNames": {layer: "rg-test-fixture-" + layer for layer in publish.LAYERS},
            "gateway": {"publicHostName": "fixture.example.com", "grpc": {"enabled": True}, "realtimeControlEnabled": True,
                "authentication": {"subscriptionKeyHeader": "api-key"}},
            "network": {"subnetPrefixes": {"apim": "10.40.2.64/26"}}, "publishApis": True,
            "apiDefinitions": [{"name": "model", "backendService": "foundryOpenAi"},
                {"name": "sample", "backendService": "external", "backendUrl": "https://sample.azurecontainerapps.io"}]}
        # 本番と同じ公開契約を持たせる。接続説明の生成に内部URLや試験固有IDは使わない。
        for api in self.config["apiDefinitions"]:
            api.update(displayName=api["name"].title(), path="tools/" + api["name"], apiType="http",
                productId="tool-users", subscriptionKeyHeader="api-key",
                operations=[{"name": "invoke", "method": "POST", "urlTemplate": "/invoke"}],
                authorization={"mode": "subscriptionKey",
                    "operationConstraints": {"invoke": {"kind": "registeredOperation", "maxRequestBytes": 32768}}})
        names = {"apim": "apim-test-fixture-abc123", "keyVault": "kv-test-fixture",
            "foundry": "aif-test-fixture", "project": "proj-test-fixture", "apiCenter": "apic-test-fixture"}
        svc = publish._scope(self.config, "svc")
        ids = {key: svc + "/providers/" + value + "/" + names[key] for key, value in {
            "apim": "Microsoft.ApiManagement/service", "keyVault": "Microsoft.KeyVault/vaults", "foundry": "Microsoft.CognitiveServices/accounts"}.items()}
        identity_id = publish._scope(self.config, "id") + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-test-fixture-certificate"
        client_id = "33333333-3333-4333-8333-333333333333"
        self.state = {**{key: self.config[key] for key in ("subscriptionId", "azureTenantId", "resourceStem", "resourceGroupNames")},
            "apimInstanceId": "abc123", "serviceResourceIds": ids, "outputs": {"names": names, "resourceGroups": self.config["resourceGroupNames"],
                "cleanup": {"serviceResourceIds": ids}, "services": ids,
                "endpoints": {"public": "https://fixture.example.com", "foundry": "https://aif-test-fixture.services.ai.azure.com"},
                "certificateIdentity": {"id": identity_id, "clientId": client_id}, "grpc": {"enabled": True},
                "ingressSecrets": {"enabled": True, "grpcEnabled": True, "realtimeEnabled": True}}}
        self.plan = publish.prepare_plan(self.config, self.state)
        self.tags = {"product": "fixture", "environment": "test"}
        self.inventory = {}
        for layer in publish.LAYERS:
            rid = publish._scope(self.config, layer)
            self.inventory[rid] = ok({"id": rid, "tags": {**self.tags, "layer": layer, "azd-env-name": "test"}})
        self.inventory[ids["apim"]] = ok({"id": ids["apim"], "tags": self.tags,
            "identity": {"type": "SystemAssigned", "principalId": "44444444-4444-4444-8444-444444444444"},
            "properties": {"virtualNetworkType": "Internal", "customProperties": {publish.grpc_lifecycle.HTTP2: "true"}}})
        self.inventory[ids["apim"] + "/policies/policy"] = ok({"properties": {"format": "rawxml", "value": render_service_policy()}})
        for name, value in self.plan["commonAuthentication"].items():
            self.inventory[ids["apim"] + "/namedValues/" + name] = ok({"properties": {
                "displayName": name, "secret": False, "value": value}})
        # 通常の再配備fixtureでは接続初期化済み。初回作成は専用ケースで検証する。
        for suffix, value in PROFILE_DEFAULTS.items():
            self.inventory[ids["apim"] + "/namedValues/" + PROFILE_PREFIX + suffix] = ok({"properties": {
                "displayName": PROFILE_PREFIX + suffix, "secret": suffix == "secret", "value": value}})
        self.inventory[identity_id] = ok({"id": identity_id, "tags": self.tags, "properties": {"clientId": client_id}})
        policy = self.plan["firewall"]["policyId"]
        self.inventory[policy] = ok({"id": policy, "tags": self.tags})
        subnet = publish._scope(self.config, "nw") + "/providers/Microsoft.Network/virtualNetworks/vnet-test-fixture/subnets/snet-test-fixture-apim"
        self.inventory[subnet] = ok({"id": subnet, "properties": {"addressPrefix": "10.40.2.64/26"}})
        self.inventory[self.plan["deploymentId"]] = ok({"fixtureApimId": ids["apim"], "properties": {
            "provisioningState": "Succeeded", "outputs": {"publishedApiResourceIds": {"value": [ids["apim"] + "/apis/model"]}}}})
        for name in (publish.grpc_lifecycle.SECRET_NAME, publish.grpc_lifecycle.TICKET_NAME):
            self.inventory[ids["apim"] + "/namedValues/" + name] = ok({"properties": {"keyVault": {
                "secretIdentifier": "https://" + names["keyVault"] + ".vault.azure.net/secrets/" + name,
                "lastStatus": {"code": "Success"}}}})
        self.old_rule = {"name": "existing-apim-rule", "ruleType": "ApplicationRule", "targetFqdns": ["existing.example.com"]}
        self.other_collection = {"name": "allow-vm-egress", "priority": 210, "rules": [{"name": "keep-vm", "targetFqdns": ["vm.example.com"]}]}
        self.group = {"id": self.plan["firewall"]["groupId"], "etag": '"fixture-etag"', "properties": {
            "priority": 400, "provisioningState": "Succeeded", "ruleCollections": [{"name": "allow-apim-egress",
                "priority": 200, "ruleCollectionType": "FirewallPolicyFilterRuleCollection", "action": {"type": "Allow"},
                "rules": [self.old_rule]}, self.other_collection]}}
        self.inventory[self.group["id"]] = ok(self.group)
        self.template = {"resources": [{"type": "Microsoft.ApiManagement/service/apis"}]}

    def test_render_uses_owned_outputs_and_contains_no_secret_values_or_core_resource_templates(self):
        params = self.plan["parameters"]
        self.assertEqual(params["apiDefinitions"]["value"][0]["backendUrl"], "https://aif-test-fixture.openai.azure.com/openai/v1")
        self.assertEqual(self.plan["azureRequests"], 0)
        self.assertEqual(self.plan["firewall"]["externalFqdns"], ["sample.azurecontainerapps.io"])
        self.assertIn(".vault.azure.net/secrets/core-realtime-ticket-key", params["realtimeTicketSecretIdentifier"]["value"])
        self.assertNotIn("realtimeTicketKey", params)
        self.assertNotIn("l7IngressSecret", params)
        self.assertNotIn("l7IngressIdentityClientId", params)
        self.assertNotIn("realtimeTicketIdentityClientId", params)

    def test_first_publication_initializes_missing_profile_connection_before_policy_deployment(self):
        """未構成の照会を無効で用意し、既存のAPI公開と別の所有範囲へ広げない。"""
        for suffix in PROFILE_DEFAULTS:
            self.inventory[self.plan["apimId"] + "/namedValues/" + PROFILE_PREFIX + suffix] = ok({}, 404)
        transport = Transport(self.inventory)
        publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
        writes = [call for call in transport.calls if call[0] == "PUT" and "/namedValues/" + PROFILE_PREFIX in call[1]]
        self.assertEqual(len(writes), 3)
        deployment_index = next(index for index, call in enumerate(transport.calls)
                                if call[0] == "PUT" and call[1].split("?")[0] == self.plan["deploymentId"])
        for call in writes:
            self.assertLess(transport.calls.index(call), deployment_index)
            self.assertEqual(call[3], {"If-None-Match": "*"})
        enabled = self.inventory[self.plan["apimId"] + "/namedValues/" + PROFILE_PREFIX + "enabled"]
        self.assertEqual(enabled["body"]["properties"]["value"], "false")

    def test_gateway_republication_preserves_active_portal_profile_connection(self):
        """Gateway公開ではPortalが所有するURL・KV参照・有効化状態を上書きしない。"""
        root = self.plan["apimId"] + "/namedValues/" + PROFILE_PREFIX
        self.inventory[root + "enabled"] = ok({"properties": {"value": "true"}})
        self.inventory[root + "url"] = ok({"properties": {
            "value": "https://ca-test-fixture-portal.private.example/api/internal/subscription-profile"}})
        self.inventory[root + "secret"] = ok({"properties": {"secret": True, "keyVault": {
            "secretIdentifier": "https://kv-test-fixture.vault.azure.net/secrets/core-subscription-profile-key"}}})
        before = {suffix: copy.deepcopy(self.inventory[root + suffix]) for suffix in PROFILE_DEFAULTS}
        transport = Transport(self.inventory)
        publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
        self.assertEqual({suffix: self.inventory[root + suffix] for suffix in PROFILE_DEFAULTS}, before)
        self.assertFalse(any(method != "GET" and "/namedValues/" + PROFILE_PREFIX in path
                             for method, path, _, _ in transport.calls))

    def renamed_apim_state(self, name):
        """APIM 本体の出力だけを切り替え、他サービスを変更しない配備記録を作る。"""
        state = copy.deepcopy(self.state)
        resource_id = publish._scope(self.config, "svc") + "/providers/Microsoft.ApiManagement/service/" + name
        state["outputs"]["names"]["apim"] = name
        for ids in (state["serviceResourceIds"], state["outputs"]["cleanup"]["serviceResourceIds"],
                    state["outputs"]["services"]):
            ids["apim"] = resource_id
        return state

    def test_standard_service_names_share_the_environment_product_stem(self):
        self.assertEqual(self.plan["parameters"]["names"]["value"], {
            "apim": "apim-test-fixture-abc123", "keyVault": "kv-test-fixture",
            "foundry": "aif-test-fixture", "project": "proj-test-fixture",
            "apiCenter": "apic-test-fixture"})

    def test_other_or_missing_instance_state_and_same_environment_old_apim_are_rejected(self):
        for value in (None, 'def456'):
            with self.subTest(instance=value):
                state = copy.deepcopy(self.state)
                if value is None:
                    del state['apimInstanceId']
                else:
                    state['apimInstanceId'] = value
                with self.assertRaisesRegex(ValueError, 'construction instance'):
                    publish.prepare_plan(self.config, state)
        for name in ('apim-test-fixture', 'apim-test-fixture-def456'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'naming policy'):
                publish.prepare_plan(self.config, self.renamed_apim_state(name))

    def test_open_product_membership_is_rejected_before_any_change(self):
        api_id=self.plan["publishedApiIds"][0]
        self.inventory[api_id+"/products"]=ok({"value":[{"properties":{"subscriptionRequired":False}}]})
        transport=Transport(self.inventory)
        with self.assertRaisesRegex(ValueError,"subscription-free"):
            publish.execute_plan(self.config,self.state,self.plan,self.template,transport)
        self.assertTrue(all(method=="GET" for method,_,_,_ in transport.calls))

    def test_separate_entra_tenant_is_recorded_without_changing_api_publication_parameters(self):
        config = copy.deepcopy(self.config)
        config['entraTenantId'] = '55555555-5555-4555-8555-555555555555'
        plan = publish.prepare_plan(config, self.state)
        self.assertNotIn('entraTenantId', plan['parameters'])
        self.assertEqual(plan['azureTenantId'], self.state['azureTenantId'])
        self.assertNotIn('tenantId', plan['parameters'])
        self.assertEqual(plan['apimId'], self.plan['apimId'])
        self.assertEqual(plan['deploymentId'], self.plan['deploymentId'])
        transport = Transport(self.inventory)
        result = publish.execute_plan(config, self.state, plan, self.template, transport, sleep=lambda _: None)
        self.assertEqual(result['azureTenantId'], self.state['azureTenantId'])
        self.assertEqual(result['entraTenantId'], config['entraTenantId'])
        self.assertTrue(result['cleanupIdsPreserved'])

    def test_common_authentication_plan_requires_explicit_foundation_and_matching_api_settings(self):
        """基盤と各APIの購読キーヘッダーを一致させ、公開前に検査する。"""
        for reason in ("missing", "invalid-header", "api-header"):
            config = copy.deepcopy(self.config)
            if reason == "missing": config["gateway"].pop("authentication")
            elif reason == "invalid-header": config["gateway"]["authentication"]["subscriptionKeyHeader"] = "Authorization"
            else: config["apiDefinitions"][0]["subscriptionKeyHeader"] = "Another-Key"
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                publish.prepare_plan(config, self.state)

    def credential_role_fixture(self):
        """二つの物理poolとbroker基盤を読み取り検査するfixture。"""
        role_id = "/subscriptions/" + self.config["subscriptionId"] + "/providers/Microsoft.Authorization/roleDefinitions/312a565d-c81f-4fd8-895a-4e21e48d571c"
        expected = {"name": "graph-delegated", "displayName":"Graph delegated", "pool":{"count":2,"capacity":8000}, "clientId": "66666666-6666-4666-8666-666666666666",
                    "oauthTenantId": self.config["azureTenantId"], "resourceUri": "https://graph.microsoft.com", "scopes": "openid profile email offline_access User.Read"}
        config = {**self.config, "credentialProviders": [expected]}
        plan = {**self.plan, "credentialLogin": {"providerNames": [expected["name"]]}}
        apim = self.inventory[self.plan["apimId"]]["body"]
        names=publish.resource_names(config)
        identity_id=publish._scope(config,"id")+"/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"+names["runtimeIdentity"]
        app_id=publish._scope(config,"svc")+"/providers/Microsoft.App/containerApps/"+names["application"]
        broker_principal="88888888-8888-4888-8888-888888888888"
        url="https://"+names["application"]+".internal.fixture.azurecontainerapps.io"
        inventory={identity_id:ok({"id":identity_id,"properties":{"principalId":broker_principal}}),
          app_id:ok({"id":app_id,"identity":{"userAssignedIdentities":{identity_id:{}}},"properties":{"provisioningState":"Succeeded","latestReadyRevisionName":"ready-001","latestRevisionName":"ready-001","configuration":{"ingress":{"fqdn":url[8:]}},"template":{"containers":[{"image":"fixture.azurecr.io/credential-broker@sha256:"+"a"*64}]}}}),
          plan["apimId"]+"/backends/credential-broker":ok({"properties":{"url":url}}),
          plan["apimId"]+"/namedValues/core-credential-broker-url":ok({"properties":{"displayName":"core-credential-broker-url","secret":False,"value":url}}),
          plan["apimId"]+"/namedValues/core-credential-broker-secret":ok({"properties":{"secret":True,"keyVault":{"secretIdentifier":"https://kv-"+config["resourceStem"]+".vault.azure.net/secrets/core-credential-broker-key"}}})}
        for suffix in ("a","b"):
            provider_id=plan["apimId"]+"/authorizationProviders/graph-delegated-"+suffix
            assignment_path=provider_id+"/providers/Microsoft.Authorization/roleAssignments"
            inventory[provider_id]=ok({"id":provider_id,"properties":{"identityProvider":"aad","oauth2":{"grantTypes":{"authorizationCode":{**{key:expected[key] for key in ("clientId","resourceUri","scopes")},"tenantId":expected["oauthTenantId"]}}}}})
            inventory[assignment_path]=ok({"value":[{"properties":{"principalId":principal,"scope":provider_id,"roleDefinitionId":role_id}} for principal in (apim["identity"]["principalId"],broker_principal)]})
        return config,plan,apim,inventory,plan["apimId"]+"/authorizationProviders/graph-delegated-a/providers/Microsoft.Authorization/roleAssignments"

    def test_personal_cm_access_requires_exact_builtin_role_at_provider_for_apim_identity(self):
        config, plan, apim, inventory, assignment_path = self.credential_role_fixture()
        for case_insensitive_ids in (False, True):
            current = copy.deepcopy(inventory)
            if case_insensitive_ids:
                properties = current[assignment_path]["body"]["value"][0]["properties"]
                for key in ("principalId", "scope", "roleDefinitionId"):
                    properties[key] = properties[key].upper()
            transport = Transport(current)
            publish.verify_credential_login(config, plan, apim, transport)
            self.assertEqual([method for method, _, _, _ in transport.calls], ["GET"]*9)
            self.assertEqual([path.split("?")[0] for _, path, _, _ in transport.calls][-4:],
                             [plan["apimId"]+"/authorizationProviders/graph-delegated-a",assignment_path,plan["apimId"]+"/authorizationProviders/graph-delegated-b",assignment_path.replace("graph-delegated-a/","graph-delegated-b/")])

    def test_personal_cm_access_rejects_other_roles_scopes_principals_and_incomplete_inventory(self):
        role_guids = {
            "old-custom": "55555555-5555-4555-8555-555555555555",
            "owner": "8e3af657-a8ff-443c-a75c-2fe8c4bcb635",
            "contributor": "b24988ac-6180-42a0-ab88-20f7382dd24c",
            "service-operator": "e022b0c1-759a-4c35-9cf1-926f5b851a56",
        }
        for reason in (*role_guids, "custom-role-scope", "role-subscription", "resource-group", "apim-scope", "other-provider",
                       "child-scope", "principal", "missing-principal", "missing-role", "missing-scope",
                       "empty-inventory", "pagination"):
            config, plan, apim, inventory, assignment_path = self.credential_role_fixture()
            listing = inventory[assignment_path]["body"]
            properties = listing["value"][0]["properties"]
            if reason in role_guids:
                properties["roleDefinitionId"] = properties["roleDefinitionId"].rsplit("/", 1)[0] + "/" + role_guids[reason]
            elif reason == "custom-role-scope":
                properties["roleDefinitionId"] = publish._scope(config, "svc") + "/providers/Microsoft.Authorization/roleDefinitions/55555555-5555-4555-8555-555555555555"
            elif reason == "role-subscription":
                properties["roleDefinitionId"] = properties["roleDefinitionId"].replace(config["subscriptionId"], "77777777-7777-4777-8777-777777777777")
            elif reason == "resource-group": properties["scope"] = publish._scope(config, "svc")
            elif reason == "apim-scope": properties["scope"] = plan["apimId"]
            elif reason == "other-provider": properties["scope"] += "-other"
            elif reason == "child-scope": properties["scope"] += "/authorizations/connection"
            elif reason == "principal": properties["principalId"] = "77777777-7777-4777-8777-777777777777"
            elif reason.startswith("missing-"):
                properties.pop({"missing-principal": "principalId", "missing-role": "roleDefinitionId", "missing-scope": "scope"}[reason])
            elif reason == "empty-inventory": listing["value"] = []
            else: listing["nextLink"] = "https://management.azure.com/next-page"
            transport = Transport(inventory)
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    publish.verify_credential_login(config, plan, apim, transport)
                self.assertTrue(all(method == "GET" for method, _, _, _ in transport.calls))
                self.assertEqual(len(transport.calls), 7)

    def test_broker_prerequisites_are_required_before_api_publication(self):
        for failure in ("bootstrap","not-ready","wrong-backend","wrong-secret","broker-role","second-pool-role"):
            config,plan,apim,inventory,assignment_path=self.credential_role_fixture()
            if failure=="bootstrap":
                next(v for k,v in inventory.items() if "/containerApps/" in k)["body"]["properties"]["template"]["containers"][0]["image"]="mcr.microsoft.com/k8se/quickstart:latest"
            elif failure=="not-ready":
                next(v for k,v in inventory.items() if "/containerApps/" in k)["body"]["properties"]["latestReadyRevisionName"]="old"
            elif failure=="wrong-backend":inventory[plan["apimId"]+"/backends/credential-broker"]["body"]["properties"]["url"]="https://elsewhere.example"
            elif failure=="wrong-secret":inventory[plan["apimId"]+"/namedValues/core-credential-broker-secret"]["body"]["properties"]["keyVault"]["secretIdentifier"]+="-wrong"
            elif failure=="broker-role":inventory[assignment_path]["body"]["value"].pop()
            else:inventory[assignment_path.replace("graph-delegated-a/","graph-delegated-b/")]["body"]["value"].pop()
            with self.subTest(failure=failure),self.assertRaises(ValueError):
                publish.verify_credential_login(config,plan,apim,Transport(inventory))

    def test_common_authentication_missing_old_modified_or_duplicated_blocks_stop_before_any_write(self):
        """共通認可ブロックの内容を全体照合し、改変を公開前に検出する。"""
        policy_id = self.plan["apimId"] + "/policies/policy"
        for reason in ("malformed", "marker-only", "key-header", "telemetry", "marker", "duplicate", "nested"):
            inventory = copy.deepcopy(self.inventory)
            xml = render_service_policy()
            if reason == "missing":
                inventory[policy_id] = ok({}, 404)
            elif reason == "old": xml = '<policies><inbound /><backend><forward-request /></backend><outbound /><on-error /></policies>'
            elif reason == "malformed": xml = '<policies><inbound>'
            elif reason == "marker-only": xml = '<policies><inbound><choose id="core-common-authentication"><otherwise><set-variable name="gatewayCommonAuthenticationValidated" value="@(true)" /></otherwise></choose></inbound></policies>'
            elif reason == "anonymous": xml = xml.replace('context.Api.Id == &quot;gateway-oauth-metadata&quot;', 'context.Api.Id != &quot;gateway-oauth-metadata&quot;')
            elif reason == "key-header": xml = xml.replace('{{core-subscription-key-header}}', 'UNRELATED_PRIVATE_AUDIENCE')
            elif reason == "telemetry": xml = xml.replace('source="core-gateway"', 'source="changed"')
            elif reason == "marker": xml = xml.replace('name="gatewayCommonAuthenticationValidated" value="@(true)"', 'name="gatewayCommonAuthenticationValidated" value="@(false)"')
            else:
                document = ET.fromstring(xml)
                inbound = document.find("inbound")
                block = inbound.find("choose[@id='core-common-authentication']")
                if reason == "duplicate":
                    document.find("on-error").append(copy.deepcopy(block))
                else:
                    inbound.remove(block)
                    ET.SubElement(ET.SubElement(inbound, "choose"), "otherwise").append(block)
                xml = ET.tostring(document, encoding="unicode")
            if reason != "missing": inventory[policy_id]["body"]["properties"]["value"] = xml
            transport = Transport(inventory)
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, "common authentication") as error:
                publish.execute_plan(self.config, self.state, self.plan, self.template, transport)
            self.assertNotIn("UNRELATED_PRIVATE_AUDIENCE", str(error.exception))
            self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_common_named_values_must_match_foundation_before_any_write(self):
        """公開前に、名前付きの購読キーヘッダー設定と基盤設定を照合する。"""
        for name in self.plan["commonAuthentication"]:
            for reason in ("missing", "value", "display-name", "secret", "key-vault", "invalid-body"):
                inventory = copy.deepcopy(self.inventory)
                named_id = self.plan["apimId"] + "/namedValues/" + name
                props = inventory[named_id]["body"]["properties"]
                if reason == "missing": inventory[named_id] = ok({}, 404)
                elif reason == "value": props["value"] = "PRIVATE_FOREIGN_SETTING"
                elif reason == "display-name": props["displayName"] = "other"
                elif reason == "secret": props["secret"] = True
                elif reason == "key-vault": props["keyVault"] = {"secretIdentifier": "PRIVATE_SECRET_REFERENCE"}
                else: inventory[named_id]["body"]["properties"] = []
                transport = Transport(inventory)
                with self.subTest(name=name, reason=reason), self.assertRaisesRegex(ValueError, "common authentication") as error:
                    publish.execute_plan(self.config, self.state, self.plan, self.template, transport)
                self.assertNotIn("PRIVATE_", str(error.exception))
                self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_common_authentication_unreadable_resources_do_not_fall_back_to_new_or_success(self):
        for suffix in ("/policies/policy", "/namedValues/core-subscription-key-header"):
            inventory = copy.deepcopy(self.inventory)
            inventory[self.plan["apimId"] + suffix] = ok({"error": {"message": "PRIVATE_DIAGNOSTIC"}}, 403)
            transport = Transport(inventory)
            with self.subTest(suffix=suffix), self.assertRaises(publish.DeploymentError) as error:
                publish.execute_plan(self.config, self.state, self.plan, self.template, transport)
            self.assertNotIn("PRIVATE_DIAGNOSTIC", str(error.exception))
            self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_initializes_factory_and_missing_policy_after_confirmed_api_deployment(self):
        policy_id = self.plan["apimId"] + "/policies/policy"
        factory = '<policies><inbound/><backend><forward-request/></backend><outbound/><on-error/></policies>'
        custom = factory.replace('<inbound/>', '<inbound><!-- operator note --><set-header name="X-Custom" exists-action="override"><value>kept</value></set-header></inbound>')
        for value in (None, factory, custom):
            with self.subTest(policy=value):
                inventory = copy.deepcopy(self.inventory)
                inventory[policy_id] = ok({}, 404) if value is None else ok({"etag": '"policy-version"', "properties": {"value": value}})
                transport = Transport(inventory)
                result = publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
                writes = [call for call in transport.calls if call[0] == "PUT"]
                self.assertEqual(writes[-1][1].split("?")[0], policy_id)
                self.assertEqual(writes[-1][3], {"If-None-Match": "*"} if value is None else {"If-Match": '"policy-version"'})
                xml = writes[-1][2]["properties"]["value"]
                self.assertIn('id="core-common-authentication"', xml)
                if value == custom:
                    self.assertIn('operator note', xml)
                    self.assertIn('X-Custom', xml)
                    self.assertLess(xml.index('core-common-authentication'), xml.index('X-Custom'))
                self.assertTrue(result["commonAuthenticationPolicyUpdated"])
                self.assertFalse(any('/namedValues/' in call[1] for call in writes))

    def test_policy_preflight_requires_etag_and_other_prerequisites(self):
        policy_id = self.plan["apimId"] + "/policies/policy"
        factory = '<policies><inbound/><backend><forward-request/></backend><outbound/><on-error/></policies>'
        for reason in ("etag", "named-value", "firewall"):
            inventory = copy.deepcopy(self.inventory)
            inventory[policy_id] = ok({"etag": '"policy-version"', "properties": {"value": factory}})
            if reason == "etag": inventory[policy_id]["body"].pop("etag")
            elif reason == "named-value": inventory[self.plan["apimId"] + "/namedValues/core-subscription-key-header"] = ok({}, 404)
            else: inventory[self.group["id"]]["body"]["properties"]["ruleCollections"] = []
            transport = Transport(inventory)
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
            self.assertFalse(any(call[0] != "GET" for call in transport.calls))

    def test_policy_concurrent_edit_or_failed_readback_reports_failure_after_child_deployment(self):
        policy_id = self.plan["apimId"] + "/policies/policy"
        factory = '<policies><inbound/><backend><forward-request/></backend><outbound/><on-error/></policies>'
        for reason in ("conflict", "ignored"):
            inventory = copy.deepcopy(self.inventory)
            inventory[policy_id] = ok({"etag": '"policy-version"', "properties": {"value": factory}})
            class ChangedPolicy(Transport):
                def request(self, method, path, body=None, headers=None):
                    if method == "PUT" and path.split("?")[0] == policy_id:
                        self.calls.append((method, path, body, headers))
                        return ok({}, 412 if reason == "conflict" else 200)
                    return super().request(method, path, body, headers)
            transport = ChangedPolicy(inventory)
            with self.subTest(reason=reason), self.assertRaises((ValueError, publish.DeploymentError)):
                publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
            self.assertTrue(any(call[0] == "PUT" and "/deployments/" in call[1] for call in transport.calls))

    def test_common_preflight_allows_unrelated_custom_global_edits_and_named_value_case(self):
        """独自設定を全体上書きせず保持し、実際の共通ブロックだけを照合する。"""
        policy_id = self.plan["apimId"] + "/policies/policy"
        document = ET.fromstring(render_service_policy())
        ET.SubElement(document.find("inbound"), "set-variable", {"name": "custom-setting", "value": "preserved"})
        custom = ET.tostring(document, encoding="unicode")
        self.inventory[policy_id]["body"]["properties"]["value"] = custom
        for name in self.plan["commonAuthentication"]:
            props = self.inventory[self.plan["apimId"] + "/namedValues/" + name]["body"]["properties"]
            props["value"] = props["value"].upper()
        transport = Transport(self.inventory)
        result = publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
        self.assertEqual(result["status"], "management-plane-published")
        self.assertEqual(self.inventory[policy_id]["body"]["properties"]["value"], custom)
        self.assertTrue(any(headers == {"Accept": "application/json"} for method, path, _, headers in transport.calls if method == "GET" and "/policies/policy?" in path))
        self.assertTrue(all("/policies/policy" not in path and "/namedValues/" not in path
                            for method, path, _, _ in transport.calls if method != "GET"))

    def test_common_policy_reads_request_rawxml_before_and_after_first_publication(self):
        """ARM の既定 XML 表現による属性の再エスケープと、実際の変更を区別する。"""
        from urllib.parse import parse_qs,urlsplit
        policy_id=self.plan["apimId"]+"/policies/policy"
        inventory=copy.deepcopy(self.inventory)
        inventory[policy_id]=ok({"etag":'"policy-version"',"properties":{
            "format":"rawxml","value":'<policies><inbound/><backend><forward-request/></backend><outbound/></policies>'}})
        class PolicyRepresentations(Transport):
            def request(self,method,path,body=None,headers=None):
                response=super().request(method,path,body,headers)
                if method=="GET" and path.split("?")[0]==policy_id:
                    properties=response["body"]["properties"]
                    if parse_qs(urlsplit(path).query).get("format")!=["rawxml"]:
                        properties["format"]="xml"
                        properties["value"]=properties["value"].replace("&quot;","&amp;quot;")
                return response
        transport=PolicyRepresentations(inventory)
        first=publish.execute_plan(self.config,self.state,self.plan,self.template,transport,sleep=lambda _:None)
        self.assertTrue(first["commonAuthenticationPolicyUpdated"])
        again=publish.verify_common_authentication(self.plan,transport)
        self.assertFalse(again["changed"])
        policy_calls=[call for call in transport.calls if call[1].split("?")[0]==policy_id]
        self.assertEqual(len([call for call in policy_calls if call[0]=="PUT"]),1)
        for method,path,_,headers in policy_calls:
            with self.subTest(method=method,path=path):
                query=parse_qs(urlsplit(path).query)
                if method=="GET":
                    self.assertEqual(query.get("format"),["rawxml"])
                    self.assertEqual(headers,{"Accept":"application/json"})
                else:
                    self.assertNotIn("format",query)

    def test_offline_renderer_generates_reviewable_policy_without_overwriting_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "service.xml"
            self.assertEqual(render_apim_policy.main(["--output", str(output)]), 0)
            xml = output.read_text(encoding="utf-8")
            self.assertEqual(xml, render_service_policy())
            self.assertNotIn("__AUTHENTICATION_TELEMETRY_XML__", xml)
            self.assertNotIn("__L7_INGRESS_", xml)
            self.assertIn('{{core-l7-ingress-secret}}', xml)
            publish.validate_common_authentication_policy(xml)
            with self.assertRaises(FileExistsError):
                render_apim_policy.main(["--output", str(output)])


    def test_coherent_foreign_apim_identity_is_rejected_before_publication(self):
        with self.assertRaises(ValueError):
            publish.prepare_plan(self.config, self.renamed_apim_state("apim-unrelated-environment"))

    def test_standard_plan_preserves_external_tool_url_without_hosting_metadata(self):
        """ツールの配備先は独立入力であり、Gatewayの資源名から推測しない。"""
        config = copy.deepcopy(self.config)
        config["apiDefinitions"][1]["backendUrl"] = "https://tools.example.com/mcp"
        plan = publish.prepare_plan(config, self.state)
        self.assertEqual(plan["parameters"]["apiDefinitions"]["value"][1]["backendUrl"],
                         "https://tools.example.com/mcp")
        self.assertNotIn("platformTools", plan)
        self.assertNotIn("managementApplication", plan)

    def test_operation_backends_resolve_owned_services_and_extend_firewall_destinations(self):
        config = copy.deepcopy(self.config)
        operation = config["apiDefinitions"][0]["operations"][0]
        operation["backendOverride"] = {"backendService": "external", "backendUrl": "https://speech.example.com/api"}
        plan = publish.prepare_plan(config, self.state)
        self.assertEqual(plan["firewall"]["externalFqdns"], ["sample.azurecontainerapps.io", "speech.example.com"])
        published = plan["parameters"]["apiDefinitions"]["value"][0]
        self.assertEqual(published["operations"][0]["backendOverride"]["backendUrl"], "https://speech.example.com/api")
        operation["backendOverride"] = {"backendService": "foundryCognitive"}
        published = publish.prepare_plan(config, self.state)["parameters"]["apiDefinitions"]["value"][0]
        self.assertEqual(published["operations"][0]["backendOverride"]["backendUrl"], "https://aif-test-fixture.cognitiveservices.azure.com")
        self.assertNotIn("backendUrl", operation["backendOverride"])

    def test_operation_backends_reject_credential_urls_before_publication(self):
        config = copy.deepcopy(self.config)
        config["apiDefinitions"][0]["operations"][0]["backendOverride"] = {
            "backendService": "external", "backendUrl": "https://speech.example.com/api?token=private"}
        with self.assertRaisesRegex(ValueError, "credential-bearing"):
            publish.prepare_plan(config, self.state)

    def test_standard_name_rejects_foreign_apim_scope(self):
        for group in ("rg-other-svc", "rg-test-fixture-svc/other"):
            state = copy.deepcopy(self.state)
            state["outputs"]["services"]["apim"] = state["outputs"]["services"]["apim"].replace("rg-test-fixture-svc", group)
            with self.subTest(group=group), self.assertRaises(ValueError):
                publish.prepare_plan(self.config, state)

    def test_native_and_api_only_publication_share_descriptions_without_changing_api_contracts(self):
        """両入口の説明が一致し、バックエンド解決以外の認可・API契約は完全に保持する。"""
        config = copy.deepcopy(self.config)
        config.update(management={}, certificate={}, credentialProviders=[])
        native = build_bicep_configuration(config)["apiDefinitions"]
        publication = publish.prepare_plan(config, self.state)["parameters"]["apiDefinitions"]["value"]
        self.assertEqual([api["catalogDescription"] for api in native], [api["catalogDescription"] for api in publication])
        for original, built, published in zip(config["apiDefinitions"], native, publication):
            built.pop("catalogDescription")
            published.pop("catalogDescription")
            self.assertEqual(built, original)
            expected = copy.deepcopy(original)
            if expected["backendService"] == "foundryOpenAi":
                expected["backendUrl"] = "https://aif-test-fixture.openai.azure.com/openai/v1"
            self.assertEqual(published, expected)

    def test_foreign_state_names_identity_and_unprepared_feature_flags_fail_offline(self):
        for key in ("subscription", "groups", "names", "identity", "grpc", "realtime"):
            state = copy.deepcopy(self.state)
            if key == "subscription": state["subscriptionId"] = "other"
            elif key == "groups": state["resourceGroupNames"]["nw"] = "rg-other"
            elif key == "names": state["outputs"]["names"]["keyVault"] = "kv-other"
            elif key == "identity": state["outputs"]["certificateIdentity"]["id"] += "-foreign"
            elif key == "grpc": state["outputs"]["grpc"]["enabled"] = False
            else: state["outputs"]["ingressSecrets"]["realtimeEnabled"] = False
            with self.subTest(key=key), self.assertRaises(ValueError):
                publish.prepare_plan(self.config, state)

    def test_foreign_backend_ports_ip_literals_and_credential_urls_fail_offline(self):
        for value in ("https://host.example.com:8443", "http://host.example.com", "https://169.254.169.254", "https://host.example.com?api-key=PRIVATE"):
            config = copy.deepcopy(self.config); config["apiDefinitions"][1]["backendUrl"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                publish.prepare_plan(config, self.state)

    def test_firewall_merge_preserves_existing_collections_and_is_idempotent(self):
        body, changed, etag = publish.merge_firewall(ok(self.group), self.plan)
        self.assertTrue(changed); self.assertEqual(etag, '"fixture-etag"')
        collections = body["properties"]["ruleCollections"]
        self.assertEqual(collections[1], self.other_collection)
        self.assertEqual(collections[0]["rules"][0], self.old_rule)
        rule = collections[0]["rules"][-1]
        self.assertEqual(rule["sourceAddresses"], ["10.40.2.64/26"])
        self.assertEqual(rule["targetFqdns"], ["sample.azurecontainerapps.io"])
        repeated = {"id": self.group["id"], "etag": '"next"', **body}
        self.assertFalse(publish.merge_firewall(ok(repeated), self.plan)[1])

    def test_firewall_readonly_size_is_neither_sent_nor_used_as_a_configuration_postcondition(self):
        """宛先追加でsizeが旧値→新値へ変わっても、規則が同じなら保持確認を通す。"""
        group = copy.deepcopy(self.group)
        group["properties"]["size"] = "0.001000000 MB"
        body, changed, _ = publish.merge_firewall(ok(group), self.plan)
        self.assertTrue(changed)
        self.assertNotIn("size", body["properties"])
        self.assertNotIn("provisioningState", body["properties"])
        self.assertEqual(group["properties"]["size"], "0.001000000 MB")
        observed = copy.deepcopy(body["properties"])
        observed.update(size="0.002000000 MB", provisioningState="Succeeded")
        self.assertTrue(publish._contains(observed, body["properties"]))

    def test_firewall_readback_still_rejects_changed_priorities_actions_sources_protocols_and_rules(self):
        """sizeだけを除外し、予想外の許可拡大や既存規則の欠落・変更は拒否する。"""
        body, _, _ = publish.merge_firewall(ok(self.group), self.plan)
        for reason in ("priority", "action", "source", "protocol", "other-rule", "missing-rule", "extra-target"):
            observed = copy.deepcopy(body["properties"])
            observed.update(size="0.002000000 MB", provisioningState="Succeeded")
            collection = observed["ruleCollections"][0]
            added = collection["rules"][-1]
            if reason == "priority": observed["priority"] += 1
            elif reason == "action": collection["action"]["type"] = "Deny"
            elif reason == "source": added["sourceAddresses"] = ["*"]
            elif reason == "protocol": added["protocols"][0]["port"] = 80
            elif reason == "other-rule": observed["ruleCollections"][1]["rules"][0]["targetFqdns"] = ["other.example.com"]
            elif reason == "missing-rule": collection["rules"].pop(0)
            else: added["targetFqdns"].append("unplanned.example.com")
            with self.subTest(reason=reason):
                self.assertFalse(publish._contains(observed, body["properties"]))

    def test_missing_etag_or_changed_source_protocol_never_produces_a_write(self):
        for change in ("etag", "source", "protocol"):
            group = copy.deepcopy(self.group)
            if change == "etag": group.pop("etag")
            else:
                rule = publish.merge_firewall(ok(group), self.plan)[0]["properties"]["ruleCollections"][0]["rules"][-1]
                if change == "source": rule["sourceAddresses"] = ["*"]
                else: rule["protocols"] = [{"protocolType": "Http", "port": 80}]
                group["properties"]["ruleCollections"][0]["rules"].append(rule)
            with self.subTest(change=change), self.assertRaises(ValueError):
                publish.merge_firewall(ok(group), self.plan)

    def test_execute_reads_all_prerequisites_then_updates_exact_rule_with_etag_and_apim_children_only(self):
        transport = Transport(self.inventory)
        result = publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
        writes = [call for call in transport.calls if call[0] != "GET"]
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0][1].split("?")[0], self.group["id"])
        self.assertEqual(writes[0][3], {"If-Match": '"fixture-etag"'})
        self.assertEqual(writes[1][2]["properties"]["mode"], "Incremental")
        self.assertEqual(writes[1][2]["properties"]["template"], self.template)
        self.assertFalse(result["runtimeVerified"])
        self.assertTrue(result["cleanupIdsPreserved"])
        self.assertNotIn("DELETE", [call[0] for call in transport.calls])

    def test_api_only_publication_finishes_catalog_metadata_after_successful_import(self):
        transport = Transport(self.inventory)
        metadata = {"enabled": True, "checked": 1, "updated": 1, "apiIds": [self.plan["apimId"] + "/apis/sample"]}

        def finish(config, outputs, actual_transport):
            self.assertEqual(config, self.config)
            self.assertEqual(outputs, self.state["outputs"])
            self.assertIs(actual_transport, transport)
            self.assertEqual(transport.calls[-1][0], "GET")
            self.assertTrue(any(path.startswith(self.plan["deploymentId"] + "?") for method, path, _, _ in transport.calls if method == "GET"))
            return metadata

        with mock.patch.object(publish, "finalize_descriptions", side_effect=finish) as finalizer:
            result = publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
        finalizer.assert_called_once()
        self.assertEqual(result["catalogDescriptions"], metadata)

    def failed_publication(self):
        """一部成功後の Failed を再現する。成功 outputs は意図的に付けない。"""
        return {"id": self.plan["deploymentId"], "name": self.plan["deploymentId"].rsplit("/", 1)[-1],
                "type": "Microsoft.Resources/deployments", "fixtureApimId": self.plan["apimId"],
                "tags": {"azd-env-name": self.config["environmentName"], "azd-project-name": publish.PROJECT_NAME},
                "properties": {"provisioningState": "Failed", "mode": "Incremental",
                               "parameters": copy.deepcopy(self.plan["parameters"])}}

    def test_owned_failed_publication_can_reapply_fixed_definitions_to_the_same_deployment(self):
        """同じ環境のFailed記録を照合し、outputsがなくても修正したAPIを再公開する。"""
        prior = self.failed_publication()
        prior["properties"]["parameters"]["apiDefinitions"]["value"][1]["backendUrl"] = "https://previous.example.com"
        self.inventory[self.plan["deploymentId"]] = ok(prior)
        transport = Transport(self.inventory)
        result = publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
        writes = [call for call in transport.calls if call[0] != "GET"]
        self.assertEqual(result["status"], "management-plane-published")
        self.assertEqual([call[0] for call in writes], ["PUT", "PUT"])
        self.assertEqual(writes[1][1].split("?", 1)[0], self.plan["deploymentId"])
        self.assertEqual(writes[1][2]["properties"]["mode"], "Incremental")
        self.assertEqual(writes[1][2]["properties"]["parameters"], self.plan["parameters"])

    def test_failed_publication_rejects_unowned_or_unknown_state_before_any_write(self):
        """他環境・実行中・未知入力の配備を上書きしない。"""
        for reason in ("id", "name", "type", "tags", "mode", "names", "header", "disabled", "missing-input",
                       "api-traversal", "api-duplicate", "foreign-output", "unknown-output", "foreign-resource", "running", "unknown"):
            prior = self.failed_publication()
            props, saved = prior["properties"], prior["properties"]["parameters"]
            if reason in ("id", "name", "type"):
                prior[reason] = "FOREIGN_PRIVATE_FIXTURE"
            elif reason == "tags": prior["tags"]["azd-project-name"] = "other"
            elif reason == "mode": props["mode"] = "Complete"
            elif reason == "names": saved["names"]["value"]["apim"] = "apim-other-prod-123abc"
            elif reason == "header": saved["subscriptionKeyHeader"]["value"] = "other-key"
            elif reason == "disabled": saved["publishApis"]["value"] = False
            elif reason == "missing-input": saved.pop("apiDefinitions")
            elif reason == "api-traversal": saved["apiDefinitions"]["value"][0]["name"] = "../../FOREIGN_PRIVATE_FIXTURE"
            elif reason == "api-duplicate": saved["apiDefinitions"]["value"].append({"name": "MODEL"})
            elif reason == "foreign-output": props["outputs"] = {"publishedApiResourceIds": {"value": [self.plan["apimId"] + "-foreign/apis/model"]}}
            elif reason == "unknown-output": props["outputs"] = []
            elif reason == "foreign-resource": props["outputResources"] = [{"id": self.plan["apimId"] + "-foreign/apis/model"}]
            elif reason == "running": props["provisioningState"] = "Running"
            else: props["provisioningState"] = "Unknown"
            inventory = copy.deepcopy(self.inventory)
            inventory[self.plan["deploymentId"]] = ok(prior)
            transport = Transport(inventory)
            with self.subTest(reason=reason), self.assertRaises(ValueError) as error:
                publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
            self.assertNotIn("FOREIGN_PRIVATE_FIXTURE", str(error.exception))
            self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_failed_publication_retains_generated_mcp_and_metadata_ids(self):
        """REST 元 API だけでなく生成 MCP と補助 API も、失敗時の保持対象へ数える。"""
        prior = self.failed_publication()
        prior["properties"]["parameters"]["apiDefinitions"]["value"][1]["mcp"] = {"name": "sample-mcp"}
        previous = publish._prior_published_ids(prior, self.config, self.plan)
        self.assertEqual(set(previous), {self.plan["apimId"] + "/apis/" + name
                                        for name in ("model", "sample", "sample-mcp")})

    def test_symbolic_resources_allow_children_and_only_the_compiled_existing_apim_reference(self):
        """Bicep2の参照はAPIM本体の更新ではない。辞書の値を検査して公開だけを行う。"""
        template = {"languageVersion": "2.0", "resources": {
            "apim": {"existing": True, "type": "Microsoft.ApiManagement/service",
                     "apiVersion": publish.APIM_API, "name": "[parameters('names').apim]"},
            "apis": {"type": "Microsoft.ApiManagement/service/apis"},
            "policies": {"type": "Microsoft.ApiManagement/service/apis/operations/policies"},
        }}
        transport = Transport(self.inventory)
        result = publish.execute_plan(self.config, self.state, self.plan, template, transport, sleep=lambda _: None)
        writes = [call for call in transport.calls if call[0] != "GET"]
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[1][2]["properties"]["template"], template)
        self.assertEqual(result["status"], "management-plane-published")

    def test_empty_malformed_or_non_apim_resources_fail_before_any_arm_request(self):
        reference = {"existing": True, "type": "Microsoft.ApiManagement/service",
                     "apiVersion": publish.APIM_API, "name": "[parameters('names').apim]"}
        bad_references = []
        for change in ({"existing": False}, {"existing": "true"}, {"properties": {}},
                       {"name": "another-apim"}, {"name": "[parameters('other').apim]"}):
            bad_references.append({"languageVersion": "2.0", "resources": {
                "apim": {**reference, **change}, "api": self.template["resources"][0]}})
        invalid = [None, [], {}, {"resources": []}, {"resources": {}}, {"resources": None},
                   {"resources": [None]}, {"resources": ["invalid"]}, {"resources": [{"type": None}]},
                   {"resources": [{"type": "Microsoft.ApiManagement/service"}]},
                   {"resources": [{"type": "Microsoft.ApiManagement/service/"}]},
                   {"resources": [{"type": "Microsoft.Compute/virtualMachines"}]},
                   {"resources": [{"type": "Microsoft.ApiManagement/service/apis", "resources": []}]},
                   {"resources": {"apis": self.template["resources"][0]}},
                   {"languageVersion": "2.0", "resources": {"api": None}},
                   {"languageVersion": "2.0", "resources": {"": self.template["resources"][0]}},
                   {"languageVersion": "2.0", "resources": {"apim": reference}},
                   {"languageVersion": "2.0", "resources": {
                       "api": self.template["resources"][0], "vm": {"type": "Microsoft.Compute/virtualMachines"}}},
                   *bad_references]
        for template in invalid:
            transport = Transport(copy.deepcopy(self.inventory))
            with self.subTest(template=template), self.assertRaisesRegex(ValueError, "Only the API publication module"):
                publish.execute_plan(self.config, self.state, self.plan, template, transport)
            self.assertEqual(transport.calls, [])

    def test_foreign_live_tag_or_unprepared_key_prevents_all_writes(self):
        for reason in ("tag", "key"):
            inventory = copy.deepcopy(self.inventory)
            if reason == "tag": inventory[self.plan["apimId"]]["body"]["tags"]["product"] = "other"
            else: inventory[self.plan["apimId"] + "/namedValues/core-realtime-ticket-key"]["body"]["properties"]["keyVault"]["lastStatus"]["code"] = "Denied"
            transport = Transport(inventory)
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                publish.execute_plan(self.config, self.state, self.plan, self.template, transport)
            self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_incremental_registration_preserves_apis_outside_the_requested_set(self):
        """既存API一覧の再提出を要求せず、追加・変更分だけをIncrementalで送る。"""
        self.inventory[self.plan["deploymentId"]]["body"]["properties"]["outputs"]["publishedApiResourceIds"]["value"].append(
            self.plan["apimId"] + "/apis/gui-managed-api")
        transport = Transport(self.inventory)
        result = publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
        self.assertEqual(result["status"], "management-plane-published")
        writes = [call for call in transport.calls if call[0] != "GET"]
        self.assertTrue(all(call[0] == "PUT" for call in writes))
        self.assertEqual(writes[-1][2]["properties"]["mode"], "Incremental")
        self.assertNotIn("gui-managed-api", json.dumps(writes[-1][2]["properties"]["parameters"]))
        forbidden = copy.deepcopy(self.template)
        forbidden["resources"].append({"type": "Microsoft.ApiManagement/service/policies"})
        with self.assertRaisesRegex(ValueError, "global policy"):
            publish.validate_publication_template(forbidden)

    def test_publication_template_cannot_update_foundation_authentication_named_values(self):
        for name in self.plan["commonAuthentication"]:
            template = copy.deepcopy(self.template)
            template["resources"].append({"type": "Microsoft.ApiManagement/service/namedValues",
                "name": "apim-test-fixture-abc123/" + name, "properties": {"displayName": name, "secret": False, "value": "other"}})
            transport = Transport(copy.deepcopy(self.inventory))
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "authentication NamedValues"):
                publish.execute_plan(self.config, self.state, self.plan, template, transport)
            self.assertEqual(transport.calls, [])

    def test_first_registration_accepts_missing_history_but_not_unreadable_history(self):
        """基盤構築直後の404を許可し、権限不足を新規配備と誤認しない。"""
        for status in (404, 403):
            inventory = copy.deepcopy(self.inventory)
            inventory[self.plan["deploymentId"]] = ok({"fixtureApimId": self.plan["apimId"]}, status)
            transport = Transport(inventory)
            with self.subTest(status=status):
                if status == 404:
                    result = publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
                    self.assertEqual(result["status"], "management-plane-published")
                    self.assertEqual([call[0] for call in transport.calls if call[0] != "GET"], ["PUT", "PUT"])
                else:
                    with self.assertRaises(publish.DeploymentError):
                        publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
                    self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_missing_system_identity_or_old_user_assigned_secret_reference_prevents_all_writes(self):
        """SAMI の不足と、成功済みでも旧 UAMI の KV 参照を Azure 変更前に拒否する。"""
        for reason in ("principal", "type", "old-secret-identity"):
            inventory = copy.deepcopy(self.inventory)
            apim = inventory[self.plan["apimId"]]["body"]
            if reason == "principal":
                apim["identity"].pop("principalId")
            elif reason == "type":
                apim["identity"]["type"] = "UserAssigned"
            else:
                named = inventory[self.plan["apimId"] + "/namedValues/core-l7-ingress-secret"]["body"]
                named["properties"]["keyVault"]["identityClientId"] = self.state["outputs"]["certificateIdentity"]["clientId"]
            transport = Transport(inventory)
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                publish.execute_plan(self.config, self.state, self.plan, self.template, transport)
            self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_model_deployment_and_scoped_role_prerequisites_are_checked_before_firewall_changes(self):
        config = copy.deepcopy(self.config)
        model = {"name": "gpt-6.1-sol", "modelName": "gpt-6.1-sol", "modelVersion": "1",
                 "modelFormat": "OpenAI", "skuName": "GlobalStandard", "capacity": 1}
        config["services"] = {**config["services"], "speechEnabled": True, "grantApimFoundryModelsAccess": True, "modelDeployments": [model]}
        plan = publish.prepare_plan(config, self.state)
        for failure in (None, "model", "role", "localAuth"):
            inventory = copy.deepcopy(self.inventory)
            principal = "44444444-4444-4444-8444-444444444444"
            inventory[plan["apimId"]]["body"]["identity"]["principalId"] = principal
            foundry_id = self.state["serviceResourceIds"]["foundry"]
            inventory[foundry_id] = ok({"id": foundry_id, "tags": self.tags, "properties": {"disableLocalAuth": failure != "localAuth"}})
            roles = ["f2dc8367-1007-4938-bd23-fe263f013447", "a97b65f3-24c7-4388-baec-2e87135dc908"]
            if failure == "role": roles.pop()
            inventory[foundry_id + "/providers/Microsoft.Authorization/roleAssignments"] = ok({"value": [
                {"properties": {"scope": foundry_id, "principalId": principal,
                    "roleDefinitionId": "/providers/Microsoft.Authorization/roleDefinitions/" + role}} for role in roles]})
            rid = foundry_id + "/deployments/gpt-6.1-sol"
            inventory[rid] = ok({"id": rid, "sku": {"name": "GlobalStandard", "capacity": 2 if failure == "model" else 1},
                "properties": {"provisioningState": "Succeeded", "model": {"name": "gpt-6.1-sol", "format": "OpenAI", "version": "1"}}})
            transport = Transport(inventory)
            with self.subTest(failure=failure):
                if failure:
                    with self.assertRaises(ValueError):
                        publish.execute_plan(config, self.state, plan, self.template, transport)
                    self.assertTrue(all(call[0] == "GET" for call in transport.calls))
                else:
                    result = publish.execute_plan(config, self.state, plan, self.template, transport)
                    self.assertEqual(result["status"], "management-plane-published")


    def test_default_sample_cleanup_follows_successful_publication_and_is_recorded(self):
        transport = Transport(self.inventory)
        phases = []
        summary = {"status": "complete", "deletedApiIds": [], "deletedProductIds": [],
                   "deletedSubscriptionIds": [], "skipped": []}

        def finalize(*args):
            phases.append("catalog")
            self.assertTrue(any(method == "PUT" and path.startswith(self.plan["deploymentId"] + "?")
                                for method, path, _, _ in transport.calls))
            return {"enabled": False}

        def cleanup(apim_id, apis, actual_transport, **kwargs):
            self.assertEqual(phases, ["catalog"])
            self.assertEqual(apim_id, self.plan["apimId"])
            self.assertEqual(apis, self.plan["parameters"]["apiDefinitions"]["value"])
            self.assertIs(actual_transport, transport)
            phases.append("cleanup")
            return summary

        with mock.patch.object(publish, "finalize_descriptions", side_effect=finalize), \
                mock.patch.object(publish, "cleanup_default_samples", side_effect=cleanup):
            result = publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
        self.assertEqual(phases, ["catalog", "cleanup"])
        self.assertEqual(result["defaultSampleCleanup"], summary)

    def test_failed_publication_or_finishing_never_starts_sample_deletion(self):
        for failure in ("deployment", "policy", "catalog"):
            with self.subTest(failure=failure):
                transport = Transport(copy.deepcopy(self.inventory))
                request = transport.request

                def failing_request(method, path, body=None, headers=None):
                    response = request(method, path, body, headers)
                    if failure == "deployment" and method == "PUT" and path.startswith(self.plan["deploymentId"] + "?"):
                        transport.inventory[self.plan["deploymentId"]]["body"]["properties"]["provisioningState"] = "Failed"
                    return response

                transport.request = failing_request
                with mock.patch.object(publish, "cleanup_default_samples") as cleanup, \
                        mock.patch.object(publish, "install_common_authentication", side_effect=publish.DeploymentError("policy failed") if failure == "policy" else None), \
                        mock.patch.object(publish, "finalize_descriptions", side_effect=publish.DeploymentError("catalog failed") if failure == "catalog" else None):
                    with self.assertRaises(publish.DeploymentError):
                        publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)
                    cleanup.assert_not_called()

    def test_sample_cleanup_failure_identifies_that_api_publication_already_succeeded(self):
        transport = Transport(self.inventory)
        with mock.patch.object(publish, "cleanup_default_samples", side_effect=ValueError("HTTP 403")):
            with self.assertRaisesRegex(publish.DeploymentError, "APIs were published.*HTTP 403"):
                publish.execute_plan(self.config, self.state, self.plan, self.template, transport, sleep=lambda _: None)

    def test_disabled_publication_can_render_but_cannot_execute(self):
        config = copy.deepcopy(self.config)
        config["publishApis"] = False
        plan = publish.prepare_plan(config, self.state)
        transport = Transport(copy.deepcopy(self.inventory))
        with self.assertRaisesRegex(ValueError, "publishApis=true"):
            publish.execute_plan(config, self.state, plan, self.template, transport)
        self.assertEqual(transport.calls, [])


    def test_formal_cohere_deployment_name_with_dot_and_uppercase_is_accepted(self):
        config = copy.deepcopy(self.config)
        name = "Cohere-rerank-v4.0-fast"
        config["services"] = {**config["services"], "modelDeployments": [{"name": name, "modelName": name,
            "modelVersion": "1", "modelFormat": "Cohere", "skuName": "GlobalStandard", "capacity": 1}]}
        plan = publish.prepare_plan(config, self.state)
        inventory = copy.deepcopy(self.inventory)
        foundry_id = self.state["serviceResourceIds"]["foundry"]
        inventory[foundry_id] = ok({"id": foundry_id, "tags": self.tags, "properties": {"disableLocalAuth": True}})
        rid = foundry_id + "/deployments/" + name
        inventory[rid] = ok({"id": rid, "sku": {"name": "GlobalStandard", "capacity": 1}, "properties": {
            "provisioningState": "Succeeded", "model": {"name": name, "version": "1", "format": "Cohere"}}})
        transport = Transport(inventory)
        result = publish.execute_plan(config, self.state, plan, self.template, transport, sleep=lambda _: None)
        self.assertEqual(result["status"], "management-plane-published")
        self.assertIn(rid, [path.split("?")[0] for method, path, _, _ in transport.calls if method == "GET"])


if __name__ == "__main__":
    unittest.main()

"""厳格監査の対象操作とリレーの公開前ARM読戻しを合成状態で照合する。"""
import copy
import unittest

import test_publish_apis as publication


class AuditRelayPreflightTests(unittest.TestCase):
    def setUp(self):
        self.config, self.state = publication.publication_fixture()
        self.config["auditEvidence"] = {"enabled": True}
        self.tenant = self.config["entraTenantId"] = self.config["azureTenantId"]
        self.audience = "44444444-4444-4444-4444-444444444444"
        self.principal = "55555555-5555-5555-5555-555555555555"
        self.config["gateway"]["authentication"]["entra"] = {
            "audience": self.audience, "requiredScope": "Gateway.Invoke",
            "userLimits": {"requestRate": None, "tokensPerMinute": None, "tokenQuota": None},
        }
        self.api = {
            **self.config["apiDefinitions"][0], "name": "openai-inference-v1", "path": "models/openai/v1",
            "backendAuthentication": {"mode": "managedIdentity"},
            "operations": [
                {"name": "chat-completions", "method": "POST", "urlTemplate": "/chat/completions"},
                {"name": "responses-create", "method": "POST", "urlTemplate": "/responses"},
            ],
            "authorization": {"mode": "subscriptionKey", "operationConstraints": {
                "chat-completions": {"kind": "bodyModel"}, "responses-create": {"kind": "statelessResponses"},
            }},
        }
        self.config["apiDefinitions"] = [self.api]
        domain = ".".join(("fixture", "test-region", "azurecontainerapps", "io"))
        host = "ca-test-fixture-audit-relay." + domain
        self.state["outputs"]["auditEvidence"] = {"environmentDomain": domain, "relayFqdn": host,
                                                     "apimPrincipalId": self.principal}
        provider = publication.publish._scope(self.config, "svc") + "/providers/Microsoft.App/"
        self.environment = provider + "managedEnvironments/cae-test-fixture-audit"
        self.apps = {role: provider + "containerApps/ca-test-fixture-audit-" + suffix
                     for role, suffix in (("relay", "relay"), ("worker", "store"))}
        self.tags = {"product": self.config["productName"], "environment": self.config["environmentName"]}
        self.inventory = {self.environment: publication.ok({"id": self.environment, "tags": self.tags, "properties": {
            "provisioningState": "Succeeded", "publicNetworkAccess": "Disabled", "defaultDomain": domain,
            "vnetConfiguration": {"internal": True},
        }})}
        apim = self.state["serviceResourceIds"]["apim"]
        self.inventory[apim] = publication.ok({"id": apim, "identity": {"tenantId": self.tenant, "principalId": self.principal}})
        for role, resource_id in self.apps.items():
            self.add_revision(role, resource_id, host)

    def add_revision(self, role, resource_id, host):
        identity = publication.publish._scope(self.config, "id") + "/providers/Microsoft.ManagedIdentity/" + (
            "userAssignedIdentities/id-test-fixture-audit-" + role)
        client_id = "77777777-7777-7777-7777-777777777777" if role == "relay" else "88888888-8888-8888-8888-888888888888"
        revision = resource_id.rsplit("/", 1)[-1] + "--sample"
        configuration = {"activeRevisionsMode": "Single"}
        env = {"AZURE_CLIENT_ID": client_id}
        if role == "relay":
            configuration["ingress"] = {
                "external": True, "allowInsecure": False, "targetPort": 8000, "fqdn": host,
                "ipSecurityRestrictions": [{"name": "allow-apim-subnet", "action": "Allow",
                                            "ipAddressRange": self.config["network"]["subnetPrefixes"]["apim"]}],
            }
            foundry = self.state["outputs"]["names"]["foundry"]
            env.update(AUDIT_TENANT_ID=self.tenant, AUDIT_TOKEN_AUDIENCE=self.audience,
                       AUDIT_APIM_PRINCIPAL_ID=self.principal,
                       AUDIT_FOUNDRY_OPENAI_ENDPOINT="https://" + foundry + ".openai.azure.com")
        registry = "acr" + self.config["resourceStem"].replace("-", "")
        template = {"containers": [{
            "image": registry + ".azurecr.io/audit-relay@sha256:" + "1" * 64,
            "env": [{"name": name, "value": value} for name, value in env.items()],
            "probes": [{"type": "Readiness", "httpGet": {"path": "/ready", "port": 8000}}],
        }]}
        self.inventory[resource_id] = publication.ok({"id": resource_id, "tags": self.tags,
            "identity": {"type": "UserAssigned", "userAssignedIdentities": {identity: {"clientId": client_id}}},
            "properties": {"managedEnvironmentId": self.environment, "configuration": configuration,
                "latestRevisionName": revision, "latestReadyRevisionName": revision,
                "provisioningState": "Succeeded", "template": template}})
        revision_id = resource_id + "/revisions/" + revision
        self.inventory[revision_id] = publication.ok({"id": revision_id, "properties": {"active": True,
            "healthState": "Healthy", "provisioningState": "Provisioned", "runningState": "Running", "template": template}})

    def test_prepares_only_the_fixed_standard_operations(self):
        plan = publication.publish.prepare_plan(self.config, self.state)
        self.assertEqual(plan["parameters"]["auditRelay"]["value"], {
            "url": "https://" + self.state["outputs"]["auditEvidence"]["relayFqdn"], "audience": "api://" + self.audience,
        })
        self.assertEqual(plan["parameters"]["apiDefinitions"]["value"][0]["backendService"], "foundryOpenAi")
        self.api["operations"][1]["backendUrlTemplate"] = "/other"
        with self.assertRaisesRegex(ValueError, "canonical Foundry"):
            publication.publish.prepare_plan(self.config, self.state)
        self.config["auditEvidence"]["enabled"] = False
        self.assertNotIn("auditRelay", publication.publish.prepare_plan(self.config, self.state)["parameters"])

    def test_missing_relay_and_wrong_tenant_cannot_produce_strict_route(self):
        state = copy.deepcopy(self.state)
        del state["outputs"]["auditEvidence"]
        with self.assertRaisesRegex(ValueError, "owned internal"):
            publication.publish.prepare_plan(self.config, state)
        self.config["entraTenantId"] = "99999999-9999-9999-9999-999999999999"
        with self.assertRaisesRegex(ValueError, "APIM identity tenant"):
            publication.publish.prepare_plan(self.config, self.state)

    def test_checks_current_relay_trust_and_healthy_worker_before_publication(self):
        verify = publication.publish.verify_audit_relay
        transport = publication.Transport(self.inventory)
        verify(self.config, self.state, transport)
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls))
        worker = self.apps["worker"]
        worker_revision = worker + "/revisions/" + self.inventory[worker]["body"]["properties"]["latestRevisionName"]
        self.inventory[worker_revision]["body"]["properties"]["healthState"] = "Unhealthy"
        with self.assertRaisesRegex(ValueError, "both audit application revisions"):
            verify(self.config, self.state, transport)
        relay = self.inventory[self.apps["relay"]]["body"]["properties"]
        trust = next(entry for entry in relay["template"]["containers"][0]["env"] if entry["name"] == "AUDIT_APIM_PRINCIPAL_ID")
        trust["value"] = self.audience
        with self.assertRaisesRegex(ValueError, "reviewed identities"):
            verify(self.config, self.state, transport)

    def test_route_readback_rejects_a_changed_operation_policy(self):
        plan = publication.publish.prepare_plan(self.config, self.state)
        apim_id = plan["apimId"]
        relay = plan["parameters"]["auditRelay"]["value"]
        suffixes = ["/apis/openai-inference-v1" + item + "/policies/policy" for item in (
            "", "/operations/chat-completions", "/operations/responses-create")]
        policy = "<policies><inbound><base/></inbound><backend/><outbound/><on-error/></policies>"
        source = {"policies": [{"suffix": suffix, "value": policy} for suffix in suffixes]}

        def add(suffix, properties):
            resource_id = apim_id + suffix
            self.inventory[resource_id] = publication.ok({"id": resource_id, "properties": properties},
                                                        headers={"ETag": '"sample"'})

        add("/backends/core-audit-relay", {"title": "Model audit relay", "protocol": "http", "url": relay["url"],
              "tls": {"validateCertificateChain": True, "validateCertificateName": True}})
        add("/namedValues/core-audit-relay-audience", {"displayName": "core-audit-relay-audience", "secret": False,
                                                       "value": relay["audience"]})
        for suffix in suffixes:
            add(suffix, {"value": policy})
        transport = publication.Transport(self.inventory)
        publication.publish._verify_published_audit_route(plan, source, transport)
        modified = policy.replace("<base/>", '<base/><set-backend-service backend-id="different-backend"/>')
        add(suffixes[-1], {"value": modified})
        with self.assertRaisesRegex(ValueError, "differs from the generated source"):
            publication.publish._verify_published_audit_route(plan, source, transport)

    def locked_payload_policy(self):
        self.config["auditEvidence"].update(payloadImmutabilityMode="Locked", payloadRetentionDays=2557)
        storage_id = publication.publish._scope(self.config, "monitor") + "/providers/Microsoft.Storage/storageAccounts/sttestfixtureevid"
        container_id = storage_id + "/blobServices/default/containers/payload"
        policy_id = container_id + "/immutabilityPolicies/default"
        self.state["outputs"]["auditEvidence"].update(evidenceStorageId=storage_id, evidenceContainerId=container_id,
            payloadImmutability={"mode": "Locked", "retentionInDays": 2557, "policyId": policy_id})
        self.inventory[policy_id] = publication.ok({"id": policy_id, "properties": {
            "state": "Locked", "immutabilityPeriodSinceCreationInDays": 2558,
        }})
        return policy_id

    def test_locked_payload_preflight_reads_only_the_owned_policy_and_accepts_longer_retention(self):
        policy_id = self.locked_payload_policy()
        transport = publication.Transport(self.inventory)
        publication.publish.verify_audit_payload_retention(self.config, self.state, transport)
        self.assertEqual(transport.calls, [("GET", policy_id + "?api-version=2023-05-01", None, None)])
        self.config["auditEvidence"]["payloadImmutabilityMode"] = "Disabled"
        transport.calls.clear()
        publication.publish.verify_audit_payload_retention(self.config, self.state, transport)
        self.assertEqual(transport.calls, [])

    def test_payload_policy_must_match_saved_core_outputs_before_using_arm(self):
        policy_id = self.locked_payload_policy()
        transport = publication.Transport(self.inventory)
        evidence = self.state["outputs"]["auditEvidence"]
        for key, replacement in (("policyId", policy_id.replace("/payload/", "/checkpoints/")), ("retentionInDays", 30)):
            state = copy.deepcopy(self.state)
            state["outputs"]["auditEvidence"]["payloadImmutability"][key] = replacement
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Core audit evidence outputs"):
                publication.publish.verify_audit_payload_retention(self.config, state, transport)
        self.assertEqual(evidence["payloadImmutability"]["policyId"], policy_id)
        self.assertEqual(transport.calls, [])

    def test_runtime_readiness_rejects_unlocked_shortened_or_wrong_policy_before_opening_relay(self):
        policy_id = self.locked_payload_policy()
        body = self.inventory[policy_id]["body"]
        for key, value in (("state", "Unlocked"), ("immutabilityPeriodSinceCreationInDays", 2556),
                           ("allowProtectedAppendWrites", True), ("allowProtectedAppendWritesAll", True)):
            with self.subTest(key=key):
                inventory = copy.deepcopy(self.inventory)
                inventory[policy_id]["body"]["properties"][key] = value
                transport = publication.Transport(inventory)
                with self.assertRaisesRegex(ValueError, "at least the configured days"):
                    publication.publish.verify_audit_relay(self.config, self.state, transport)
                self.assertEqual(len(transport.calls), 1)
        body["id"] = policy_id.replace("/payload/", "/checkpoints/")
        with self.assertRaisesRegex(ValueError, "does not belong to the selected environment"):
            publication.publish.verify_audit_relay(self.config, self.state, publication.Transport(self.inventory))

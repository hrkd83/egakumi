"""初回active公開の閉じたstage、source検査、checkpoint、所属追加の実行順。"""
import copy
import unittest
from contextlib import ExitStack
from unittest import mock

import test_publish_apis as legacy
from test_entra_publication import SyntheticTransport, response, xml
import entra_publication as publication

publish = legacy.publish


def transaction_checkpoint(plan, source, global_policy, *, entra_authentication=None, browser_source=None):
    """このtransaction試験のmode XMLを保つ。実認証source検査は専用試験で行う。"""
    result = {"schemaVersion": 1, "apimId": plan["apimId"], "deploymentId": plan["deploymentId"],
              "source": copy.deepcopy(source)}
    if browser_source is not None:
        result["browserSource"] = copy.deepcopy(browser_source)
    return result


class InitialTransport(SyntheticTransport):
    def __init__(self, base, plan, source):
        super().__init__(copy.deepcopy(base))
        self.plan, self.source, self.failed_stage = plan, source, False
        self.apim = plan["apimId"]
        self.global_id = self.apim + "/policies/policy"
        self.open_id = self.apim + "/products/" + publication.ENTRA_OPEN_PRODUCT_ID

    def sync_members(self):
        self.refresh(self.open_id, self.inventory[self.open_id]["body"]["properties"])
        self.refresh(self.open_id + "/apis", value=[{"id": api_id} for api_id in sorted(self.members)])
        for api_id in tuple(self.inventory):
            if not api_id.startswith(self.apim + "/apis/") or "/" in api_id[len(self.apim + "/apis/"):]:
                continue
            values = [{"id": self.apim + "/products/tool-users"}]
            if api_id in self.members:
                values.append({"id": self.open_id})
            if self.inventory[api_id + "/products"]["body"]["value"] != values:
                self.refresh(api_id + "/products", value=values)

    def install_stage(self):
        self.refresh(self.open_id, publication.OPEN_PRODUCT_PROPERTIES)
        self.refresh(self.open_id + "/policies/policy", {"format": "rawxml", "value": publication.OPEN_PRODUCT_POLICY})
        self.refresh(self.open_id + "/apis", value=[])
        self.refresh(self.apim + "/products/tool-users", {"subscriptionRequired": True})
        for api in self.source["apis"]:
            api_id = self.apim + "/apis/" + api["name"]
            self.refresh(api_id, api["properties"])
            self.refresh(api_id + "/products", value=[{"id": self.apim + "/products/tool-users"}])
            self.refresh(api_id + "/operations", value=[{"id": api_id + "/operations/" + name} for name in api["operations"]])
            self.refresh(api_id + "/revisions", value=[{"apiId": "/apis/" + api["name"] + ";rev=1", "apiRevision": "1", "isCurrent": True, "isOnline": True}])
            for kind in ("tools", "schemas"):
                self.refresh(api_id + "/" + kind, value=[])
        for entry in self.source["policies"]:
            self.refresh(self.apim + entry["suffix"], {"format": "rawxml", "value": entry["value"]})
        for entry in self.source.get("apiChildren", []):
            self.refresh(self.apim + entry["suffix"], entry["properties"])
        prefix = self.apim + "/apis/"
        self.refresh(self.apim + "/apis", value=[{"id": resource_id} for resource_id in sorted(self.inventory)
                     if resource_id.startswith(prefix) and "/" not in resource_id[len(prefix):]])

    def request(self, method, path, body=None, headers=None):
        resource_id = path.split("?", 1)[0]
        if method == "PUT" and resource_id == self.plan["deploymentId"]:
            self.calls.append((method, resource_id, copy.deepcopy(body), copy.deepcopy(headers)))
            if self.failed_stage:
                return {"statusCode": 500, "body": {}, "headers": {}}
            self.install_stage()
            self.refresh(resource_id, {"provisioningState": "Succeeded", "outputs": {
                "publishedApiResourceIds": {"value": self.plan["publishedApiIds"]},
                "entraPublicationSource": {"value": self.source}}})
            return copy.deepcopy(self.inventory[resource_id])
        if method == "PUT" and resource_id == self.global_id:
            self.calls.append((method, resource_id, copy.deepcopy(body), copy.deepcopy(headers)))
            if resource_id not in self.inventory:
                if headers != {"If-None-Match": "*"}:
                    return {"statusCode": 412, "body": {}, "headers": {}}
            elif headers != {"If-Match": self.inventory[resource_id]["body"]["etag"]}:
                return {"statusCode": 412, "body": {}, "headers": {}}
            self.refresh(resource_id, body["properties"])
            return copy.deepcopy(self.inventory[resource_id])
        if resource_id.startswith(self.open_id + "/apis/") and method in ("PUT", "DELETE"):
            self.calls.append((method, resource_id, copy.deepcopy(body), copy.deepcopy(headers)))
            api_id = self.apim + "/apis/" + resource_id.rsplit("/", 1)[-1]
            self.members.add(api_id) if method == "PUT" else self.members.discard(api_id)
            self.sync_members()
            return response(resource_id, {}, status=202)
        return super().request(method, path, body, headers)


def controller_fixture():
    base = legacy.PublicationTests("runTest")
    base.setUp()
    base.config["gateway"]["authentication"]["entra"] = {
        "audience": "11111111-1111-4111-8111-111111111111", "requiredScope": "Gateway.Invoke",
        "userLimits": {"requestRate": None, "tokensPerMinute": None, "tokenQuota": None}}
    plan = copy.deepcopy(base.plan)
    apim = plan["apimId"]
    plan["entraAuthentication"] = {"enabled": True, "apiNames": ["model", "sample"]}
    plan["parameters"]["entraAuthentication"] = {"value": copy.deepcopy(plan["entraAuthentication"])}
    # Exercise the publication transaction without an unrelated Firewall change.
    plan["firewall"]["externalFqdns"] = []
    prior_global = xml("closed")
    authentication = {"path": apim + "/policies/policy?api-version=" + publish.APIM_API,
                      "value": xml("active"), "previousValue": prior_global, "previousEntra": None, "changed": True}
    inventory = copy.deepcopy(base.inventory)
    inventory.pop(plan["deploymentId"])
    inventory[apim + "/policies/policy"] = response(apim + "/policies/policy", {"format": "rawxml", "value": prior_global})
    for kind in ("apis", "policyFragments", "backends", "products", "namedValues", "subscriptions", "loggers", "diagnostics"):
        inventory[apim + "/" + kind] = response(apim + "/" + kind, value=[])
    source = {"schemaVersion": 2, "apis": [], "policies": [], "backends": [], "namedValues": [], "apiChildren": []}
    for api in plan["parameters"]["apiDefinitions"]["value"]:
        source["apis"].append({"name": api["name"], "operations": ["invoke"], "products": ["tool-users"],
                               "properties": {"type": "http", "path": api["path"], "protocols": ["http", "https"], "subscriptionRequired": True,
                                              "subscriptionKeyParameterNames": {"header": "api-key", "query": "subscription-key"}}})
        source["policies"] += [{"suffix": "/apis/" + api["name"] + suffix, "value": publication.OPEN_PRODUCT_POLICY}
                               for suffix in ("/policies/policy", "/operations/invoke/policies/policy")]
        source["apiChildren"].append({"suffix": "/apis/" + api["name"] + "/operations/invoke",
                                      "properties": {"method": "POST", "urlTemplate": "/invoke"}})
    transport = InitialTransport(inventory, plan, source)
    return base, plan, authentication, transport


class BootstrapControllerTests(unittest.TestCase):
    def execute(self, base, plan, authentication, transport, checkpoints):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(publish, "prepare_plan", return_value=plan))
            stack.enter_context(mock.patch.object(publish, "verify_common_authentication", return_value=authentication))
            stack.enter_context(mock.patch.object(publish, "source_checkpoint", side_effect=transaction_checkpoint))
            stack.enter_context(mock.patch.object(publish, "merge_firewall", return_value=({}, False, '"unchanged"')))
            for name in ("verify_subscription_products", "verify_credential_login", "verify_websocket_diagnostics", "initialize_profile_settings"):
                stack.enter_context(mock.patch.object(publish, name))
            for name in ("reconcile_unreleased_apis", "enable_websocket_diagnostics", "finalize_descriptions", "cleanup_default_samples"):
                stack.enter_context(mock.patch.object(publish, name, return_value={}))
            stack.enter_context(mock.patch.object(publish, "state_from_outputs", return_value={"serviceResourceIds": base.state["serviceResourceIds"]}))
            def checkpoint(value):
                transport.calls.append(("CHECKPOINT", "source", None, None))
                checkpoints.append(copy.deepcopy(value))
            return publish.execute_plan(base.config, base.state, plan, base.template, transport, sleep=lambda _: None, checkpoint=checkpoint)

    def test_fresh_active_config_stages_closed_then_checkpoints_and_opens_membership_last(self):
        base, plan, authentication, transport = controller_fixture()
        checkpoints = []
        result = self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(result["entraAuthentication"]["employeePathOpen"])
        self.assertEqual(len(checkpoints), 1)
        self.assertTrue(plan["entraAuthentication"]["enabled"])
        self.assertNotIn("enabled", base.config["gateway"]["authentication"]["entra"])
        writes = [(index, method, resource_id) for index, (method, resource_id, _, _) in enumerate(transport.calls) if method != "GET"]
        stage = next(index for index, method, resource_id in writes if resource_id == plan["deploymentId"])
        checkpoint = next(index for index, method, _ in writes if method == "CHECKPOINT")
        global_write = next(index for index, method, resource_id in writes if resource_id == transport.global_id)
        memberships = [index for index, method, resource_id in writes if resource_id.startswith(transport.open_id + "/apis/")]
        self.assertLess(stage, checkpoint)
        self.assertLess(checkpoint, global_write)
        self.assertTrue(all(global_write < index for index in memberships))

    def test_missing_global_gets_only_a_conditional_closed_baseline_before_stage(self):
        base, plan, authentication, transport = controller_fixture()
        transport.inventory.pop(transport.global_id)
        authentication["previousValue"] = None
        # This transaction fixture isolates the initial creation and ordering.
        # The fixed inline renderer avoids references to fragments not staged yet;
        # its real authentication source is checked in the fragment tests.
        with mock.patch("deployment.helpers.apim_authentication._render_inline_common_authentication_service_policy",
                        return_value=xml("closed")):
            self.execute(base, plan, authentication, transport, [])
        creation = next(call for call in transport.calls if call[0] == "PUT" and call[1] == transport.global_id)
        self.assertEqual(creation[3], {"If-None-Match": "*"})
        self.assertEqual(creation[2]["properties"]["value"], xml("closed"))

    def test_fresh_stage_rejects_owned_case_variant_inventory_before_any_write(self):
        for kind, name in (
                ("apis", "MODEL;REV=2"),
                ("products", "TOOL-USERS"),
                ("products", publication.ENTRA_OPEN_PRODUCT_ID.upper()),
                ("namedValues", "MODEL-BACKEND-AUDIENCE"),
                ("namedValues", "MODEL-BACKEND-SECRET"),
                ("namedValues", "OP-ALIAS-BACKEND-SECRET"),
                ("subscriptions", "EXAMPLE"),
                ("loggers", "AZUREMONITOR"),
                ("diagnostics", "AZUREMONITOR")):
            for uppercase_scope in (False, True):
                with self.subTest(kind=kind, uppercase_scope=uppercase_scope):
                    base, plan, authentication, transport = controller_fixture()
                    if kind == "subscriptions":
                        plan["parameters"]["apiDefinitions"]["value"][0]["keySubscriptions"] = [
                            {"subscriptionId": "example"}]
                    resource_id = plan["apimId"] + "/" + kind + "/" + name
                    if uppercase_scope:
                        resource_id = resource_id.upper()
                    transport.inventory[plan["apimId"] + "/" + kind]["body"]["value"].append(
                        {"id": resource_id})
                    checkpoints = []
                    with self.assertRaises(ValueError):
                        self.execute(base, plan, authentication, transport, checkpoints)
                    self.assertTrue(all(call[0] == "GET" for call in transport.calls))
                    self.assertEqual(checkpoints, [])

    def test_fresh_stage_rejects_duplicate_case_aliases_in_every_inventory(self):
        for kind in ("apis", "policyFragments", "backends", "products", "namedValues",
                     "subscriptions", "loggers", "diagnostics"):
            with self.subTest(kind=kind):
                base, plan, authentication, transport = controller_fixture()
                resource_id = plan["apimId"] + "/" + kind + "/unrelated-fixture"
                transport.inventory[plan["apimId"] + "/" + kind]["body"]["value"].extend(
                    [{"id": resource_id}, {"id": resource_id.upper()}])
                checkpoints = []
                with self.assertRaisesRegex(ValueError, "unique canonical"):
                    self.execute(base, plan, authentication, transport, checkpoints)
                self.assertTrue(all(call[0] == "GET" for call in transport.calls))
                self.assertEqual(checkpoints, [])

    def test_fresh_read_accepts_single_unrelated_resource_with_case_variant_scope(self):
        _, plan, _, transport = controller_fixture()
        resource_id = (plan["apimId"] + "/products/unrelated-fixture").upper()
        transport.inventory[plan["apimId"] + "/products"]["body"]["value"].append(
            {"id": resource_id})
        publication.read_fresh_publication(plan, transport)
        self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_stage_failure_stays_closed_and_never_records_source(self):
        base, plan, authentication, transport = controller_fixture()
        transport.failed_stage = True
        checkpoints = []
        with self.assertRaises(publication.TransitionError) as caught:
            self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(caught.exception.employee_path_closed)
        self.assertFalse(caught.exception.recovery_complete)
        self.assertEqual(checkpoints, [])
        self.assertFalse(any(call[0] == "PUT" and call[1].startswith(transport.open_id) for call in transport.calls))

    def test_existing_deployment_or_bad_checkpoint_never_uses_fresh_bootstrap(self):
        for mode in ("existing-deployment", "null-checkpoint", "incomplete-checkpoint"):
            with self.subTest(mode=mode):
                base, plan, authentication, transport = controller_fixture()
                if mode == "existing-deployment":
                    transport.inventory[plan["deploymentId"]] = legacy.ok({"properties": {"provisioningState": "Succeeded", "outputs": {
                        "publishedApiResourceIds": {"value": plan["publishedApiIds"]}}}})
                elif mode == "null-checkpoint":
                    base.state["apiPublicationSourceCheckpoint"] = None
                else:
                    base.state["apiPublicationSourceCheckpoint"] = {"schemaVersion": 1, "apimId": plan["apimId"], "deploymentId": plan["deploymentId"]}
                with self.assertRaises(ValueError):
                    self.execute(base, plan, authentication, transport, [])
                self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_global_drift_during_stage_does_not_install_active_policy_or_checkpoint(self):
        base, plan, authentication, transport = controller_fixture()
        original_stage = transport.install_stage
        def drifted_stage():
            original_stage()
            transport.refresh(transport.global_id, {"value": xml("concurrent")})
        transport.install_stage = drifted_stage
        checkpoints = []
        with self.assertRaises(publication.TransitionError):
            self.execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(checkpoints, [])
        self.assertFalse(any(call[0] == "PUT" and call[1] == transport.global_id for call in transport.calls))

    def test_partial_republication_preserves_the_prior_source_and_employee_memberships(self):
        base, plan, authentication, transport = controller_fixture()
        checkpoints = []
        self.execute(base, plan, authentication, transport, checkpoints)
        base.state["apiPublicationSourceCheckpoint"] = checkpoints[-1]
        untouched = copy.deepcopy(transport.inventory[plan["apimId"] + "/apis/sample"])
        plan = copy.deepcopy(plan)
        plan["parameters"]["apiDefinitions"]["value"] = plan["parameters"]["apiDefinitions"]["value"][:1]
        plan["publishedApiIds"] = [plan["apimId"] + "/apis/model"]
        plan["entraAuthentication"]["apiNames"] = ["model"]
        transport.plan = plan
        transport.source = copy.deepcopy(transport.source)
        transport.source["apis"] = transport.source["apis"][:1]
        for key in ("policies", "apiChildren"):
            transport.source[key] = [entry for entry in transport.source[key] if "/apis/sample/" not in entry["suffix"]]
        authentication = {**authentication, "previousValue": xml("active"), "previousEntra": {"apiNames": ["model", "sample"]}}
        with mock.patch.object(publish, "transition_common_authentication_policy", return_value=(xml("active"), False)) as merge:
            self.execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(merge.call_args.kwargs["entra"]["apiNames"], ["model", "sample"])
        self.assertEqual(transport.members, {plan["apimId"] + "/apis/model", plan["apimId"] + "/apis/sample"})
        self.assertEqual(transport.inventory[plan["apimId"] + "/apis/sample"], untouched)
        self.assertEqual({entry["name"] for entry in checkpoints[-1]["source"]["apis"]}, {"model", "sample"})

    def addition_fixture(self):
        base, plan, authentication, transport = controller_fixture()
        checkpoints = []
        self.execute(base, plan, authentication, transport, checkpoints)
        base.state["apiPublicationSourceCheckpoint"] = checkpoints[-1]
        plan = copy.deepcopy(plan)
        api = copy.deepcopy(plan["parameters"]["apiDefinitions"]["value"][0])
        api.update(name="added", path="added")
        plan["parameters"]["apiDefinitions"]["value"] = [api]
        plan["publishedApiIds"] = [plan["apimId"] + "/apis/added"]
        plan["entraAuthentication"]["apiNames"] = ["added"]
        transport.plan = plan
        source = copy.deepcopy(transport.source)
        source["apis"] = source["apis"][:1]
        source["apis"][0]["name"] = "added"
        source["apis"][0]["properties"]["path"] = "added"
        for key in ("policies", "apiChildren"):
            source[key] = [entry for entry in source[key] if "/apis/model/" in entry["suffix"]]
            for entry in source[key]:
                entry["suffix"] = entry["suffix"].replace("/apis/model/", "/apis/added/")
        transport.source = source
        authentication = {**authentication, "previousValue": xml("active"), "previousEntra": {"apiNames": ["model", "sample"]}}
        transport.calls.clear()
        return base, plan, authentication, transport, checkpoints

    def test_true_api_addition_proves_absence_then_preserves_prior_source_and_memberships(self):
        base, plan, authentication, transport, checkpoints = self.addition_fixture()
        unchanged = copy.deepcopy(transport.inventory[plan["apimId"] + "/apis/sample"])
        with mock.patch.object(publish, "transition_common_authentication_policy", return_value=(xml("active"), False)) as merge:
            result = self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(result["entraAuthentication"]["employeePathOpen"])
        self.assertEqual(merge.call_args.kwargs["entra"]["apiNames"], ["added", "model", "sample"])
        self.assertEqual(transport.members, {plan["apimId"] + "/apis/" + name for name in ("added", "model", "sample")})
        self.assertEqual(transport.inventory[plan["apimId"] + "/apis/sample"], unchanged)
        self.assertEqual({entry["name"] for entry in checkpoints[-1]["source"]["apis"]}, {"added", "model", "sample"})
        first_write = next(index for index, call in enumerate(transport.calls) if call[0] != "GET")
        stage = next(index for index, call in enumerate(transport.calls) if call[0] == "PUT" and call[1] == plan["deploymentId"])
        absence_reads = [index for index, call in enumerate(transport.calls)
                         if call[0] == "GET" and call[1] == plan["apimId"] + "/apis/added"]
        self.assertGreaterEqual(len([index for index in absence_reads if index < first_write]), 2)
        self.assertTrue(any(first_write < index < stage for index in absence_reads))

    def test_existing_api_missing_source_or_revision_collision_cannot_be_an_addition(self):
        for mode in ("existing", "old-source-missing", "current-inventory", "revision", "revision-uppercase", "incomplete-inventory", "unselected"):
            with self.subTest(mode=mode):
                base, plan, authentication, transport, checkpoints = self.addition_fixture()
                added = plan["apimId"] + "/apis/added"
                if mode == "existing":
                    transport.refresh(added, transport.source["apis"][0]["properties"])
                elif mode == "old-source-missing":
                    base.state["apiPublicationSourceCheckpoint"]["source"]["apis"] = (
                        base.state["apiPublicationSourceCheckpoint"]["source"]["apis"][:1])
                elif mode in ("current-inventory", "revision", "revision-uppercase"):
                    transport.inventory[plan["apimId"] + "/apis"]["body"]["value"].append(
                        {"id": (added + (";rev=2" if mode.startswith("revision") else "")).upper() if mode == "revision-uppercase"
                         else added + (";rev=2" if mode == "revision" else "")})
                elif mode == "incomplete-inventory":
                    transport.inventory[plan["apimId"] + "/apis"]["body"]["nextLink"] = "https://management.azure.com/next"
                else:
                    plan["publishedApiIds"] = []
                with mock.patch.object(publish, "transition_common_authentication_policy", return_value=(xml("active"), False)):
                    with self.assertRaises(ValueError):
                        self.execute(base, plan, authentication, transport, checkpoints)
                self.assertTrue(all(call[0] == "GET" for call in transport.calls))
                self.assertEqual(len(checkpoints), 1)

    def test_new_api_appearing_before_first_write_or_stage_prevents_staging(self):
        for phase in ("first-write", "stage"):
            with self.subTest(phase=phase):
                base, plan, authentication, transport, checkpoints = self.addition_fixture()
                added = plan["apimId"] + "/apis/added"
                reads = 0
                def race(method, resource_id, *_):
                    nonlocal reads
                    if method == "GET" and resource_id == added:
                        reads += 1
                        if (phase == "first-write" and reads == 2
                                or phase == "stage" and not transport.members):
                            transport.refresh(added, transport.source["apis"][0]["properties"])
                transport.hook = race
                with mock.patch.object(publish, "transition_common_authentication_policy", return_value=(xml("active"), False)):
                    with self.assertRaises((ValueError, publication.TransitionError)):
                        self.execute(base, plan, authentication, transport, checkpoints)
                self.assertFalse(any(call[0] == "PUT" and call[1] == plan["deploymentId"] for call in transport.calls))
                if phase == "first-write":
                    self.assertTrue(all(call[0] == "GET" for call in transport.calls))
                else:
                    self.assertTrue(any(call[0] == "DELETE" for call in transport.calls))
                self.assertEqual(len(checkpoints), 1)

    def test_staged_source_cannot_add_an_api_outside_the_reviewed_absence_set(self):
        base, plan, authentication, transport, checkpoints = self.addition_fixture()
        extra = copy.deepcopy(transport.source["apis"][0])
        extra["name"] = "unreviewed"
        transport.source["apis"].append(extra)
        with mock.patch.object(publish, "transition_common_authentication_policy", return_value=(xml("active"), False)):
            with self.assertRaises(publication.TransitionError) as caught:
                self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(caught.exception.employee_path_closed)
        self.assertEqual(len(checkpoints), 1)
        self.assertFalse(any(call[0] == "PUT" and call[1].startswith(transport.open_id + "/apis/") for call in transport.calls))

    def test_prior_source_cannot_hide_incomplete_duplicate_or_legacy_stage_source(self):
        for mode in ('missing-api', 'duplicate-api', 'legacy-version'):
            with self.subTest(mode=mode):
                base, plan, authentication, transport = controller_fixture()
                checkpoints = []
                self.execute(base, plan, authentication, transport, checkpoints)
                base.state['apiPublicationSourceCheckpoint'] = checkpoints[-1]
                authentication = {**authentication, 'previousValue': xml('active'), 'previousEntra': {'apiNames': ['model', 'sample']}}
                transport.calls.clear()
                transport.source = copy.deepcopy(transport.source)
                if mode == 'missing-api':
                    transport.source['apis'] = transport.source['apis'][:1]
                elif mode == 'duplicate-api':
                    transport.source['apis'].append(copy.deepcopy(transport.source['apis'][0]))
                else:
                    transport.source['schemaVersion'] = 1
                    transport.source.pop('apiChildren')
                with self.assertRaises(publication.TransitionError) as caught:
                    self.execute(base, plan, authentication, transport, checkpoints)
                self.assertTrue(caught.exception.employee_path_closed)
                self.assertEqual(len(checkpoints), 1)
                self.assertFalse(any(call[0] == 'PUT' and call[1].startswith(transport.open_id + '/apis/') for call in transport.calls))

    def test_source_v1_partial_migration_stops_before_membership_or_stage_writes(self):
        base, plan, authentication, transport, checkpoints = self.addition_fixture()
        source = base.state['apiPublicationSourceCheckpoint']['source']
        source['schemaVersion'] = 1
        source.pop('apiChildren')
        with mock.patch.object(publish, 'transition_common_authentication_policy', return_value=(xml('active'), False)):
            with self.assertRaisesRegex(ValueError, 'republish every prior API'):
                self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_source_v1_complete_republication_upgrades_only_after_verified_closed_stage(self):
        base, plan, authentication, transport = controller_fixture()
        checkpoints = []
        self.execute(base, plan, authentication, transport, checkpoints)
        prior = copy.deepcopy(checkpoints[-1])
        prior['source']['schemaVersion'] = 1
        prior['source'].pop('apiChildren')
        for api in prior['source']['apis']:
            api['properties'].pop('protocols')
        base.state['apiPublicationSourceCheckpoint'] = prior
        authentication = {**authentication, 'previousValue': xml('active'), 'previousEntra': {'apiNames': ['model', 'sample']}}
        transport.calls.clear()
        self.execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(checkpoints[-1]['source']['schemaVersion'], 2)
        self.assertEqual({entry['name'] for entry in checkpoints[-1]['source']['apis']}, {'model', 'sample'})
        self.assertEqual(transport.members, {plan['apimId'] + '/apis/' + name for name in ('model', 'sample')})

    def test_stage_shared_resource_without_source_or_with_drift_is_never_overwritten(self):
        for kind, name, protected in (
                ('backends', 'added-backend', False),
                ('namedValues', 'added-backend-audience', False),
                ('namedValues', 'added-backend-secret', False),
                ('backends', 'op-unrepresented-backend', False),
                ('namedValues', 'op-unrepresented-backend-secret', False),
                ('backends', 'OP-UNREPRESENTED-BACKEND', False),
                ('namedValues', 'OP-UNREPRESENTED-BACKEND-SECRET', False),
                ('policyFragments', 'core-added', False),
                ('policyFragments', 'core-added', True)):
            with self.subTest(kind=kind, name=name, protected=protected):
                base, plan, authentication, transport, checkpoints = self.addition_fixture()
                resource_id = plan['apimId'] + '/' + kind + '/' + name
                properties = {'value': '<fragment><set-variable name="mode" value="unowned" /></fragment>'} if kind == 'policyFragments' else {'url': 'https://provider.example.com'}
                if name == 'added-backend-secret':
                    plan['parameters']['apiDefinitions']['value'][0].setdefault('backendAuthentication', {})['mode'] = 'keyVaultHeader'
                if name.casefold().startswith('op-'):
                    plan['parameters']['apiDefinitions']['value'][0]['operations'][0]['backendOverride'] = {}
                if kind == 'policyFragments':
                    base.template['resources'].append({'type': 'Microsoft.ApiManagement/service/policyFragments', 'name': 'core-added'})
                    if protected:
                        base.state['apiPublicationSourceCheckpoint']['source']['policies'].append(
                            {'suffix': '/policyFragments/core-added', 'value': '<fragment><set-variable name="mode" value="owned" /></fragment>'})
                transport.refresh(resource_id, properties)
                transport.inventory[plan['apimId'] + '/' + kind]['body']['value'].append({'id': resource_id})
                with mock.patch.object(publish, 'transition_common_authentication_policy', return_value=(xml('active'), False)):
                    with self.assertRaises(ValueError):
                        self.execute(base, plan, authentication, transport, checkpoints)
                self.assertTrue(all(call[0] == 'GET' for call in transport.calls))
                self.assertEqual(transport.inventory[resource_id]['body']['properties'], properties)
                self.assertEqual(len(checkpoints), 1)

    def test_stage_shared_resource_inventory_or_absence_race_stops_before_stage(self):
        for mode in ('inventory-only', 'inventory-uppercase', 'inventory-case-aliases', 'source-case-aliases', 'first-write', 'stage'):
            with self.subTest(mode=mode):
                base, plan, authentication, transport, checkpoints = self.addition_fixture()
                resource_id = plan['apimId'] + '/backends/added-backend'
                if mode in ('inventory-only', 'inventory-uppercase'):
                    transport.inventory[plan['apimId'] + '/backends']['body']['value'].append(
                        {'id': resource_id.upper() if mode == 'inventory-uppercase' else resource_id})
                elif mode == 'inventory-case-aliases':
                    transport.inventory[plan['apimId'] + '/backends']['body']['value'].extend(
                        [{'id': resource_id}, {'id': resource_id.upper()}])
                elif mode == 'source-case-aliases':
                    base.state['apiPublicationSourceCheckpoint']['source']['backends'].extend(
                        [{'suffix': '/backends/added-backend', 'properties': {}},
                         {'suffix': '/backends/ADDED-BACKEND', 'properties': {}}])
                reads = 0
                def race(method, observed_id, *_):
                    nonlocal reads
                    if method == 'GET' and observed_id == resource_id:
                        reads += 1
                        if (mode == 'first-write' and reads == 2 or mode == 'stage' and not transport.members):
                            transport.refresh(resource_id, {'url': 'https://provider.example.com'})
                transport.hook = race
                with mock.patch.object(publish, 'transition_common_authentication_policy', return_value=(xml('active'), False)):
                    with self.assertRaises((ValueError, publication.TransitionError)):
                        self.execute(base, plan, authentication, transport, checkpoints)
                self.assertFalse(any(call[0] == 'PUT' and call[1] == plan['deploymentId'] for call in transport.calls))
                if mode != 'stage':
                    self.assertTrue(all(call[0] == 'GET' for call in transport.calls))

    def test_staged_shared_resources_with_exact_prior_source_remain_usable(self):
        base, plan, authentication, transport, checkpoints = self.addition_fixture()
        fragment = '<fragment><set-variable name="mode" value="owned" /></fragment>'
        base.template['resources'].append({'type': 'Microsoft.ApiManagement/service/policyFragments', 'name': 'core-added'})
        prior = base.state['apiPublicationSourceCheckpoint']['source']
        prior['policies'].append({'suffix': '/policyFragments/core-added', 'value': fragment})
        backend = {'protocol': 'http', 'url': 'https://provider.example.com', 'tls': {'validateCertificateName': True, 'validateCertificateChain': True}}
        prior['backends'].append({'suffix': '/backends/added-backend', 'properties': backend})
        named = {'displayName': 'added-backend-audience', 'secret': False, 'value': 'https://audience.example.com'}
        prior['namedValues'].append({'suffix': '/namedValues/added-backend-audience', 'properties': named})
        for kind, name, properties in (('policyFragments', 'core-added', {'value': fragment}),
                ('backends', 'added-backend', backend), ('namedValues', 'added-backend-audience', named)):
            resource_id = plan['apimId'] + '/' + kind + '/' + name
            transport.refresh(resource_id, properties)
            transport.inventory[plan['apimId'] + '/' + kind]['body']['value'].append({'id': resource_id})
        with mock.patch.object(publish, 'transition_common_authentication_policy', return_value=(xml('active'), False)):
            result = self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(result['entraAuthentication']['employeePathOpen'])
        self.assertEqual(transport.inventory[plan['apimId'] + '/backends/added-backend']['body']['properties'], backend)

"""生成専用ARMと実配備の結び付け・再利用・失敗時の停止を合成応答で確認する。"""
import copy
import unittest
from unittest import mock

import tests.gateway.publication.test_publication_templates as fixture
import tests.gateway.publication.test_entra_publication_bootstrap as bootstrap
from tests.gateway.publication.test_publish_apis import ok, publish
from deployment.lifecycle import DeploymentError
from publication_templates import publication_parameters


PRINCIPAL = "44444444-4444-4444-8444-444444444444"
OTHER_PRINCIPAL = "55555555-5555-4555-8555-555555555555"
CONFIG = {"environmentName": "synthetic-test"}


class GenerationTransport:
    """既存資源に触れず、生成記録・export・型付き入力を返す偽の管理プレーン。"""
    def __init__(self, config=None, principal=PRINCIPAL):
        self.config = CONFIG if config is None else config
        self.principal = principal
        self.records, self.templates, self.calls = {}, {}, []
        self.export_mutation = None
        self.confirmation_drift = None
        self.exported = set()
        self.fail_create = False

    def record(self, identifier, template, parameters, *, status="Succeeded"):
        # Azureが入力にtypeを付けて返す形を用意する。設定値は独立した合成値だけ。
        inputs = publication_parameters(template, parameters)
        for name, wrapper in inputs.items():
            wrapper["type"] = template.get("parameters", {}).get(name, {}).get("type", "object")
        outputs = {}
        for name, entry in template.get("outputs", {}).items():
            value = self.principal if name == publish.SOURCE_PRINCIPAL_OUTPUT else copy.deepcopy(entry["value"])
            outputs[name] = {"type": entry["type"], "value": value}
        self.records[identifier] = {
            "id": identifier, "type": "Microsoft.Resources/deployments",
            "tags": {"azd-env-name": self.config["environmentName"], "azd-project-name": publish.PROJECT_NAME},
            "properties": {"mode": "Incremental", "provisioningState": status, "parameters": inputs,
                           "outputs": outputs, "outputResources": [], "templateHash": "synthetic-template-hash",
                           "correlationId": "synthetic-unchanged-generation"},
        }
        self.templates[identifier] = copy.deepcopy(template)

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, copy.deepcopy(body), copy.deepcopy(headers)))
        identifier = path.split("?", 1)[0]
        if method == "POST" and identifier.endswith("/exportTemplate"):
            deployment = identifier.removesuffix("/exportTemplate")
            if deployment not in self.templates:
                return ok({}, 404)
            template = copy.deepcopy(self.templates[deployment])
            if self.export_mutation is not None:
                self.export_mutation(deployment, template)
            self.exported.add(deployment)
            return ok({"template": template})
        if method == "GET":
            if identifier not in self.records:
                return ok({}, 404)
            if identifier in self.exported and self.confirmation_drift is not None:
                self.confirmation_drift(identifier, self.records[identifier])
            return ok(self.records[identifier])
        if method == "PUT":
            # 生成単体の試験で実API・実配備への書込みが紛れたら成功させない。
            if "-src-" not in identifier or any(value.get("existing") is not True
                                                for value in body["properties"]["template"]["resources"].values()):
                raise AssertionError("Source generation must not deploy API resources")
            if self.fail_create:
                return ok({}, 500)
            self.record(identifier, body["properties"]["template"], body["properties"]["parameters"])
            return ok(self.records[identifier], 202)
        raise AssertionError("Unexpected source-generation request")


class SourceGenerationTests(unittest.TestCase):
    def setUp(self):
        self.template, self.plan = fixture.template(), fixture.plan()
        self.transport = GenerationTransport()

    def generate(self, **kwargs):
        return publish.generate_publication_source(CONFIG, self.plan, self.template, self.transport,
                                                   identity_principal_id=PRINCIPAL, sleep=lambda _: None, **kwargs)

    def test_create_writes_only_a_source_job_and_keeps_publication_resources_unchanged(self):
        original = copy.deepcopy(self.template)
        publication, source, browser, identifier = self.generate()
        self.assertEqual(self.template, original)
        self.assertEqual(publication["resources"], original["resources"])
        self.assertEqual(source, original["outputs"]["entraPublicationSource"]["value"])
        self.assertEqual(browser, {})
        writes = [call for call in self.transport.calls if call[0] == "PUT"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1].split("?", 1)[0], identifier)
        self.assertEqual(set(writes[0][2]["properties"]["template"]["resources"]), {"apim"})
        self.assertEqual(publication["outputs"][publish.SOURCE_GENERATION_OUTPUT]["value"], identifier)
        self.assertNotIn("entraPublicationSource", publication["outputs"])
        self.assertTrue(any(call[0] == "POST" and "/exportTemplate?" in call[1] for call in self.transport.calls))

    def test_function_limit_counts_members_across_namespaces_before_any_request(self):
        def group(namespace, count):
            return {"namespace": namespace, "members": {
                "function" + str(index): {"parameters": [], "output": {"type": "string", "value": "synthetic"}}
                for index in range(count)}}

        for count in (64, 65):
            self.transport = GenerationTransport()
            self.template["functions"] = [group("synthetic_one", 32), group("synthetic_two", count - 32)]
            original = copy.deepcopy(self.template)
            with self.subTest(function_count=count):
                if count == 64:
                    self.generate()
                    self.assertTrue(self.transport.calls)
                else:
                    with self.assertRaisesRegex(ValueError, "ARM template function limit"):
                        self.generate()
                    self.assertEqual(self.transport.calls, [])
                self.assertEqual(self.template, original)

        for malformed in (None, {}, [None], [{"namespace": "synthetic", "members": []}]):
            self.transport = GenerationTransport()
            self.template["functions"] = malformed
            with self.subTest(malformed=malformed), self.assertRaisesRegex(ValueError, "ARM function groups"):
                self.generate()
            self.assertEqual(self.transport.calls, [])

    def test_matching_successful_generation_is_reused_after_code_verification_without_redeployment(self):
        first = self.generate()
        self.transport.calls.clear()
        self.assertEqual(self.generate(), first)
        self.assertFalse(any(call[0] == "PUT" for call in self.transport.calls))
        self.assertEqual(sum(call[0] == "POST" for call in self.transport.calls), 1)

    def test_arm_type_metadata_and_omitted_literal_default_preserve_the_same_generation(self):
        self.template["parameters"]["optional"] = {"type": "string", "defaultValue": "synthetic-default"}
        first = self.generate()
        self.assertEqual(self.transport.records[first[3]]["properties"]["parameters"]["optional"]["value"], "synthetic-default")
        self.plan["parameters"]["optional"] = {"type": "string", "value": "synthetic-default"}
        self.transport.calls.clear()
        self.assertEqual(self.generate(), first)
        self.assertFalse(any(call[0] == "PUT" for call in self.transport.calls))

    def test_existing_owner_input_resource_write_or_malformed_output_cannot_be_reused(self):
        mutations = (
            lambda body: body["tags"].update({"azd-env-name": "foreign-owner"}),
            lambda body: body.update(type="Microsoft.ApiManagement/service"),
            lambda body: body["properties"].update(mode="Complete"),
            lambda body: body["properties"]["parameters"]["names"].update(value={"apim": "foreign"}),
            lambda body: body["properties"].update(outputResources=[{"id": self.plan["apimId"] + "/apis/model"}]),
            lambda body: body["properties"]["outputs"].update({publish.SOURCE_GENERATION_OUTPUT: "malformed"}),
            lambda body: body["properties"]["outputs"].update(entraPublicationSource=[]),
            lambda body: body["properties"].update(provisioningState="Unknown"),
        )
        for mutate in mutations:
            self.transport = GenerationTransport()
            identifier = self.generate()[3]
            mutate(self.transport.records[identifier])
            self.transport.calls.clear()
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                self.generate()
            self.assertFalse(any(call[0] == "PUT" for call in self.transport.calls))

    def test_exported_code_mismatch_is_rejected_without_overwriting_the_existing_generation(self):
        self.generate()
        self.transport.export_mutation = lambda _, value: value["variables"].update(value="unreviewed-code")
        self.transport.calls.clear()
        with self.assertRaisesRegex(ValueError, "reviewed code"):
            self.generate()
        self.assertFalse(any(call[0] == "PUT" for call in self.transport.calls))

    def test_recreated_identity_and_record_change_during_export_are_detected(self):
        for reason in ("identity", "record"):
            self.transport = GenerationTransport(principal=OTHER_PRINCIPAL if reason == "identity" else PRINCIPAL)
            if reason == "record":
                self.transport.confirmation_drift = lambda _, body: body["properties"].update(correlationId="synthetic-new-write")
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                self.generate()
            self.assertTrue(all("-src-" in call[1] for call in self.transport.calls))

    def test_generation_failure_and_owned_failed_retry_do_not_write_api_resources(self):
        self.transport.fail_create = True
        with self.assertRaises(DeploymentError):
            self.generate()
        self.assertTrue(all("-src-" in call[1] for call in self.transport.calls))
        self.transport.fail_create = False
        first = self.generate()
        self.transport.records[first[3]]["properties"]["provisioningState"] = "Failed"
        self.transport.calls.clear()
        self.assertEqual(self.generate(), first)
        self.assertEqual(sum(call[0] == "PUT" for call in self.transport.calls), 1)


class AppliedSourceBindingTests(unittest.TestCase):
    setUp = SourceGenerationTests.setUp
    generate = SourceGenerationTests.generate

    def prepare_successful_publication(self):
        publication, source, _, identifier = self.generate()
        self.transport.record(self.plan["deploymentId"], publication, self.plan["parameters"])
        prior = ok(self.transport.records[self.plan["deploymentId"]])
        self.transport.calls.clear()
        return prior, source, identifier

    def test_only_the_source_bound_to_a_successful_publication_is_read(self):
        prior, source, identifier = self.prepare_successful_publication()
        self.assertEqual(publish._prior_publication_source(prior, CONFIG, self.plan, self.transport), source)
        exports = {call[1].split("/exportTemplate", 1)[0] for call in self.transport.calls if call[0] == "POST"}
        self.assertEqual(exports, {identifier, self.plan["deploymentId"]})
        self.assertFalse(any(call[0] == "PUT" for call in self.transport.calls))

    def test_azure_export_schema_casing_and_root_metadata_omission_preserve_both_read_paths(self):
        self.template["metadata"] = {"_generator": {"name": "synthetic", "version": "0.0"}}
        self.template["functions"] = [{"namespace": "synthetic", "members": {
            "echo": {"parameters": [{"name": "value", "type": "string"}],
                     "output": {"type": "string", "value": "[parameters('value')]"}},
        }}]
        prior, source, _ = self.prepare_successful_publication()

        def azure_export(_, value):
            # 実コードを変更せず、Azure exportが付けるschema表記だけを再現する。
            value.pop("metadata", None)
            for section in ("parameters", "outputs"):
                for declaration in value[section].values():
                    declaration["type"] = declaration["type"].capitalize()
            for function in value["functions"]:
                for member in function["members"].values():
                    for declaration in member["parameters"]:
                        declaration["type"] = declaration["type"].capitalize()
                    member["output"]["type"] = member["output"]["type"].capitalize()

        self.transport.export_mutation = azure_export
        self.assertEqual(self.generate()[1], source)
        self.assertEqual(publish._prior_publication_source(prior, CONFIG, self.plan, self.transport), source)
        self.assertFalse(any(call[0] == "PUT" for call in self.transport.calls))

    def test_a_newer_unapplied_generation_is_not_discovered_or_adopted(self):
        prior, source, identifier = self.prepare_successful_publication()
        newer = copy.deepcopy(self.template)
        newer["outputs"]["entraPublicationSource"]["value"]["unapplied"] = True
        _, _, _, newer_id = publish.generate_publication_source(CONFIG, self.plan, newer, self.transport,
                                                               identity_principal_id=PRINCIPAL, sleep=lambda _: None)
        self.transport.calls.clear()
        self.assertEqual(publish._prior_publication_source(prior, CONFIG, self.plan, self.transport), source)
        self.assertNotEqual(identifier, newer_id)
        self.assertFalse(any(newer_id in call[1] for call in self.transport.calls))

    def test_failed_publication_does_not_adopt_a_successful_generation(self):
        prior, _, _ = self.prepare_successful_publication()
        prior["body"]["properties"]["provisioningState"] = "Failed"
        with self.assertRaisesRegex(ValueError, "successful publication"):
            publish._prior_publication_source(prior, CONFIG, self.plan, self.transport)
        self.assertFalse(self.transport.calls)

    def test_code_mismatch_in_either_export_and_publication_record_drift_are_rejected(self):
        for target in ("source", "publication", "record"):
            self.transport = GenerationTransport()
            prior, _, identifier = self.prepare_successful_publication()
            if target == "record":
                self.transport.confirmation_drift = lambda rid, body: body["properties"].update(correlationId="synthetic-new-publication") if rid == self.plan["deploymentId"] else None
            else:
                changed_id = identifier if target == "source" else self.plan["deploymentId"]
                self.transport.export_mutation = lambda rid, value, changed_id=changed_id: value["variables"].update(value="different-code") if rid == changed_id else None
            with self.subTest(target=target), self.assertRaises(ValueError):
                publish._prior_publication_source(prior, CONFIG, self.plan, self.transport)
            self.assertFalse(any(call[0] == "PUT" for call in self.transport.calls))

    def test_matching_owner_and_inputs_do_not_make_an_incorrect_generation_digest_valid(self):
        prior, _, identifier = self.prepare_successful_publication()
        forged = identifier[:-1] + ("0" if identifier[-1] != "0" else "1")
        self.transport.records[forged] = self.transport.records.pop(identifier)
        self.transport.templates[forged] = self.transport.templates.pop(identifier)
        self.transport.records[forged]["id"] = forged
        self.transport.records[forged]["properties"]["outputs"][publish.SOURCE_GENERATION_OUTPUT]["value"] = forged
        self.transport.templates[forged]["outputs"][publish.SOURCE_GENERATION_OUTPUT]["value"] = forged
        self.transport.templates[self.plan["deploymentId"]]["outputs"][publish.SOURCE_GENERATION_OUTPUT]["value"] = forged
        self.transport.records[self.plan["deploymentId"]]["properties"]["outputs"][publish.SOURCE_GENERATION_OUTPUT]["value"] = forged
        prior = ok(self.transport.records[self.plan["deploymentId"]])
        with self.assertRaisesRegex(ValueError, "same recorded code"):
            publish._prior_publication_source(prior, CONFIG, self.plan, self.transport)


class SplitInitialTransport(bootstrap.InitialTransport):
    def __init__(self, original):
        super().__init__(original.inventory, original.plan, original.source)
        principal = self.inventory[self.apim]["body"]["identity"]["principalId"]
        self.generation = GenerationTransport({"environmentName": "test"}, principal)
        self.generation.calls = self.calls

    def request(self, method, path, body=None, headers=None):
        identifier = path.split("?", 1)[0]
        if "-src-" in identifier:
            return self.generation.request(method, path, body, headers)
        result = super().request(method, path, body, headers)
        if method == "PUT" and identifier == self.plan["deploymentId"] and result["statusCode"] < 400:
            # 実配備は巨大sourceを返さず、生成IDだけを適用成功の記録へ保存する。
            output = body["properties"]["template"]["outputs"][publish.SOURCE_GENERATION_OUTPUT]
            properties = self.inventory[identifier]["body"]["properties"]
            properties["outputs"].pop("entraPublicationSource", None)
            properties["outputs"][publish.SOURCE_GENERATION_OUTPUT] = copy.deepcopy(output)
            result = copy.deepcopy(self.inventory[identifier])
        return result


class SplitPublicationOrderingTests(unittest.TestCase):
    def setup_split(self):
        base, plan, authentication, original = bootstrap.controller_fixture()
        base.template = fixture.template()
        base.template["parameters"] = {name: {"type": "object"} for name in plan["parameters"]}
        base.template["outputs"]["entraPublicationSource"]["value"] = copy.deepcopy(original.source)
        base.template["outputs"]["publishedApiResourceIds"]["value"] = plan["publishedApiIds"]
        return base, plan, authentication, SplitInitialTransport(original)

    def execute(self, base, plan, authentication, transport, checkpoints, **options):
        saved = bootstrap.transaction_checkpoint
        def checkpoint(*args, generation_id=None, **kwargs):
            value = saved(*args, **kwargs)
            if generation_id is not None:
                value["sourceGenerationId"] = generation_id
            return value
        controller = bootstrap.BootstrapControllerTests("runTest")
        with mock.patch.object(bootstrap, "transaction_checkpoint", side_effect=checkpoint):
            return controller.execute(base, plan, authentication, transport, checkpoints, **options)

    def test_source_then_resource_graph_checkpoint_and_membership_preserve_the_existing_order(self):
        base, plan, authentication, transport = self.setup_split()
        checkpoints = []
        result = self.execute(base, plan, authentication, transport, checkpoints)
        self.assertTrue(result["entraAuthentication"]["employeePathOpen"])
        source = next(i for i, call in enumerate(transport.calls) if call[0] == "PUT" and "-src-" in call[1])
        stage = next(i for i, call in enumerate(transport.calls) if call[0] == "PUT" and call[1] == plan["deploymentId"])
        checkpoint = next(i for i, call in enumerate(transport.calls) if call[0] == "CHECKPOINT")
        membership = [i for i, call in enumerate(transport.calls) if call[0] == "PUT" and call[1].startswith(transport.open_id + "/apis/")]
        graph_reads = [i for i, call in enumerate(transport.calls) if call[0] == "GET" and call[1].split("?", 1)[0] == transport.apim + "/apis/model"]
        self.assertLess(source, stage)
        self.assertTrue(any(stage < i < checkpoint for i in graph_reads))
        self.assertLess(stage, checkpoint)
        self.assertTrue(all(checkpoint < i for i in membership))
        self.assertEqual(checkpoints[-1]["sourceGenerationId"], result["sourceGenerationId"])

    def test_generation_failure_does_not_close_memberships_or_change_any_apim_resource(self):
        base, plan, authentication, transport = self.setup_split()
        transport.generation.fail_create = True
        before = copy.deepcopy(transport.inventory)
        checkpoints = []
        with self.assertRaises(DeploymentError):
            self.execute(base, plan, authentication, transport, checkpoints)
        self.assertEqual(transport.inventory, before)
        self.assertFalse(checkpoints)
        self.assertTrue(all("-src-" in call[1] for call in transport.calls if call[0] not in ("GET", "POST")))

    def test_recorded_same_scope_stage_checks_mixed_readback_then_resumes_and_opens_last(self):
        base, plan, authentication, transport = self.setup_split()
        checkpoints, stage_records, jobs = [], [], []
        deployment_id = plan['deploymentId']
        original = transport.request
        state = {'fail': False}
        target = plan['apimId'] + '/apis/model/operations/invoke/policies/policy'

        def request(method, path, body=None, headers=None):
            resource_id = path.split('?', 1)[0]
            if method == 'POST' and resource_id == deployment_id + '/exportTemplate':
                transport.calls.append((method, path, body, headers))
                return ok({'template': copy.deepcopy(jobs[-1]['template'])})
            if method != 'PUT' or resource_id != deployment_id:
                return original(method, path, body, headers)
            job = copy.deepcopy(body['properties'])
            jobs.append(job)
            if state['fail']:
                transport.calls.append((method, path, body, headers))
                self.assertFalse(transport.members)
                self.assertEqual(len(stage_records), 1)
                # One of two generated policies reached APIM; the failed API
                # operation kept the policy from its previously opened stage.
                applied = next(entry for entry in transport.source['policies'] if '/apis/sample/operations/' in entry['suffix'])
                transport.refresh(plan['apimId'] + applied['suffix'], {'format': 'rawxml', 'value': applied['value']})
                transport.inventory[deployment_id + '/operations'] = ok({'value': [{
                    'id': deployment_id + '/operations/synthetic-policy-operation',
                    'properties': {'provisioningState': 'Failed', 'provisioningOperation': 'Create', 'targetResource': {
                        'id': target, 'resourceType': 'Microsoft.ApiManagement/service/apis/operations/policies'}},
                }]})
                properties = {'provisioningState': 'Failed', 'outputs': {}}
            else:
                response = original(method, path, body, headers)
                properties = response['body']['properties']
            properties.update(mode='Incremental', parameters=job['parameters'], correlationId='synthetic-job-' + str(len(jobs)))
            transport.inventory[deployment_id] = ok({'id': deployment_id, 'name': deployment_id.rsplit('/', 1)[-1],
                'type': 'Microsoft.Resources/deployments', 'tags': body['tags'], 'properties': properties})
            return ok({}, 202)

        with mock.patch.object(transport, 'request', side_effect=request):
            self.execute(base, plan, authentication, transport, checkpoints)
            base.state['apiPublicationSourceCheckpoint'] = checkpoints[-1]
            authentication.update(previousValue=bootstrap.xml('active'), previousEntra={'apiNames': ['model', 'sample']}, changed=False)
            transport.source = copy.deepcopy(transport.source)
            for policy in transport.source['policies']:
                if '/operations/' in policy['suffix']:
                    policy['value'] = policy['value'].replace('<outbound><base /></outbound>', '<outbound><base />'
                        '<set-header name="x-audit-phase" exists-action="override"><value>synthetic</value></set-header></outbound>')
            base.template['outputs']['entraPublicationSource']['value'] = copy.deepcopy(transport.source)
            base.template['resources']['synthetic_policy'] = {'type': 'Microsoft.ApiManagement/service/apis/operations/policies',
                'apiVersion': publish.APIM_API, 'name': 'synthetic/model/invoke/policy'}
            state['fail'] = True
            with self.assertRaises(publish.entra_publication.TransitionError):
                self.execute(base, plan, authentication, transport, checkpoints, record_stage=stage_records.append)
            self.assertEqual(len(checkpoints), 1)
            self.assertFalse(transport.members)
            state['fail'] = False
            transport.calls.clear()
            before = copy.deepcopy(transport.inventory[target]['body']['properties'])
            transport.refresh(target, {'format': 'rawxml', 'value': bootstrap.xml('unreviewed')})
            with self.assertRaisesRegex(ValueError, 'both recorded old and attempted'):
                self.execute(base, plan, authentication, transport, checkpoints, stage_record=stage_records[0])
            self.assertTrue(all(call[0] in ('GET', 'POST') for call in transport.calls))
            transport.refresh(target, before)
            transport.calls.clear()
            result = self.execute(base, plan, authentication, transport, checkpoints, stage_record=stage_records[0])

        self.assertTrue(result['entraAuthentication']['employeePathOpen'])
        writes = [(method, rid) for method, rid, *_ in transport.calls if method not in ('GET', 'POST')]
        self.assertEqual(writes, [('PUT', deployment_id), ('CHECKPOINT', 'source'),
            ('PUT', transport.open_id + '/apis/model'), ('PUT', transport.open_id + '/apis/sample')])


if __name__ == "__main__":
    unittest.main()

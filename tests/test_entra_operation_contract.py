"""Operationの空既定値・未指定sample補完だけを許容し、契約差分を拒否する。"""
import copy
import unittest

from test_entra_publication import APIM
from test_entra_publication_protocols import protocol_fixture
import entra_publication as publication


def contract():
    return {"method": "DELETE", "urlTemplate": "/calls/{callId}", "templateParameters": [{
        "name": "callId", "description": "Explicit approved identifier", "type": "string", "required": True,
    }]}


class OperationContractTests(unittest.TestCase):
    def test_missing_and_empty_parameter_values_are_equivalent_in_both_directions(self):
        missing = contract()
        empty = copy.deepcopy(missing)
        empty["templateParameters"][0]["values"] = []
        self.assertTrue(publication._operation_routing_matches(empty, missing))
        self.assertTrue(publication._operation_routing_matches(missing, empty))
        self.assertFalse(publication._contains(empty, missing))
        self.assertNotIn("values", missing["templateParameters"][0])
        self.assertEqual(empty["templateParameters"][0]["values"], [])

    def test_routing_parameter_shape_and_nonempty_values_still_match_exactly(self):
        changes = {
            "method": lambda value: value.update(method="POST"),
            "url": lambda value: value.update(urlTemplate="/other/{callId}"),
            "name": lambda value: value["templateParameters"][0].update(name="anotherId"),
            "required": lambda value: value["templateParameters"][0].update(required=False),
            "type": lambda value: value["templateParameters"][0].update(type="integer"),
            "description": lambda value: value["templateParameters"][0].update(description="Changed"),
            "nonempty-values": lambda value: value["templateParameters"][0].update(values=["restricted"]),
            "null-values": lambda value: value["templateParameters"][0].update(values=None),
            "malformed-values": lambda value: value["templateParameters"][0].update(values={}),
            "extra-parameter": lambda value: value["templateParameters"].append({"name": "other", "type": "string"}),
            "duplicate-parameter": lambda value: value["templateParameters"].append(copy.deepcopy(value["templateParameters"][0])),
            "missing-parameter": lambda value: value.update(templateParameters=[]),
            "malformed-parameter": lambda value: value.update(templateParameters=["callId"]),
            "null-parameters": lambda value: value.update(templateParameters=None),
            "extra-parameter-field": lambda value: value["templateParameters"][0].update(unexpected=True),
        }
        for label, change in changes.items():
            with self.subTest(change=label):
                actual = contract()
                actual["templateParameters"][0]["values"] = []
                change(actual)
                self.assertFalse(publication._operation_routing_matches(actual, contract()))
        selected = contract()
        selected["templateParameters"][0]["values"] = ["approved"]
        self.assertTrue(publication._operation_routing_matches(selected, copy.deepcopy(selected)))
        self.assertFalse(publication._operation_routing_matches(contract(), selected))

    def test_other_parameter_lists_do_not_receive_the_parameter_contract_default(self):
        expected = {"request": {"arbitraryParameters": [{"name": "query", "type": "string"}]}}
        actual = copy.deepcopy(expected)
        actual["request"]["arbitraryParameters"][0]["values"] = []
        self.assertFalse(publication._operation_routing_matches(actual, expected))

    def test_request_and_response_parameter_defaults_keep_nonempty_values_strict(self):
        for scope in ("request-header", "query", "response-header"):
            expected = contract()
            parameter = {"name": "synthetic-header", "type": "string", "required": True}
            if scope == "response-header":
                expected["responses"] = [{"statusCode": 200, "headers": [parameter]}]
                path = lambda value: value["responses"][0]["headers"][0]
            else:
                name = "headers" if scope == "request-header" else "queryParameters"
                expected["request"] = {name: [parameter]}
                path = lambda value: value["request"][name][0]
            actual = copy.deepcopy(expected)
            path(actual)["values"] = []
            with self.subTest(scope=scope):
                self.assertTrue(publication._operation_routing_matches(actual, expected))
                path(actual)["values"] = ["restricted"]
                self.assertFalse(publication._operation_routing_matches(actual, expected))

    def test_only_empty_response_header_default_is_equivalent_to_omission(self):
        expected = {"responses": [{"statusCode": 200, "description": "Accepted"}]}
        actual = copy.deepcopy(expected)
        actual["responses"][0]["headers"] = []
        self.assertTrue(publication._operation_routing_matches(actual, expected))
        self.assertTrue(publication._operation_routing_matches(expected, actual))
        actual["responses"][0]["headers"] = [{"name": "extra", "type": "string", "values": []}]
        self.assertFalse(publication._operation_routing_matches(actual, expected))
        actual = copy.deepcopy(expected)
        actual["responses"][0]["statusCode"] = 201
        self.assertFalse(publication._operation_routing_matches(actual, expected))

    def test_only_source_unspecified_representation_samples_are_ignored(self):
        for scope in ("request", "response"):
            representation = {"contentType": "application/json", "schemaId": "synthetic-contract", "typeName": "Input"}
            expected = {"request": {"representations": [representation]}} if scope == "request" else {
                "responses": [{"statusCode": 200, "representations": [representation]}]}
            at = (lambda value: value["request"]["representations"][0]) if scope == "request" else (
                lambda value: value["responses"][0]["representations"][0])
            actual = copy.deepcopy(expected)
            at(actual).update(sample='{"synthetic":true}', generatedSample="generated example",
                              examples={"default": {"value": {"synthetic": True}}})
            with self.subTest(scope=scope):
                self.assertTrue(publication._operation_routing_matches(actual, expected))
                for field, value in (("contentType", "text/plain"), ("schemaId", "another-contract"),
                                     ("typeName", "AnotherInput"), ("unexpected", True)):
                    changed = copy.deepcopy(actual)
                    at(changed)[field] = value
                    self.assertFalse(publication._operation_routing_matches(changed, expected), field)
                for field in ("sample", "generatedSample", "examples"):
                    explicit = copy.deepcopy(expected)
                    at(explicit)[field] = copy.deepcopy(at(actual)[field])
                    self.assertTrue(publication._operation_routing_matches(actual, explicit), field)
                    changed = copy.deepcopy(actual)
                    at(changed)[field] = "changed source-defined example"
                    self.assertFalse(publication._operation_routing_matches(changed, explicit), field)

    def test_representation_and_response_array_lengths_and_order_remain_strict(self):
        expected = {"responses": [
            {"statusCode": 200, "representations": [{"contentType": "application/json"}, {"contentType": "text/plain"}]},
            {"statusCode": 400, "representations": []},
        ]}
        for change in (
            lambda value: value["responses"].reverse(),
            lambda value: value["responses"].pop(),
            lambda value: value["responses"][0]["representations"].reverse(),
            lambda value: value["responses"][0]["representations"].append({"contentType": "application/xml"}),
        ):
            actual = copy.deepcopy(expected)
            change(actual)
            self.assertFalse(publication._operation_routing_matches(actual, expected))

    def test_full_generated_graph_accepts_only_the_documented_empty_default(self):
        for observed_values, accepted in (([], True), (["restricted"], False), (None, False)):
            with self.subTest(values=observed_values):
                manifest, transport = protocol_fixture()
                operation_id = APIM + "/apis/model-a/operations/responses"
                expected = contract()
                manifest.api_children[operation_id] = expected
                actual = copy.deepcopy(expected)
                actual["templateParameters"][0]["values"] = observed_values
                transport.inventory[operation_id]["body"]["properties"] = actual
                if accepted:
                    snapshot = publication.read_policy_graph(manifest, transport)
                    publication.verify_snapshot(snapshot, transport)
                else:
                    with self.assertRaisesRegex(ValueError, "Operation routing contract differs"):
                        publication.read_policy_graph(manifest, transport)


if __name__ == "__main__":
    unittest.main()

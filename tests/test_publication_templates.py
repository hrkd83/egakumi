"""同一ARMの無変更分離、生成専用の参照範囲、不変の世代IDを合成入力で検証する。"""
import copy
import unittest

from publication_templates import (
    SOURCE_GENERATION_OUTPUT, SOURCE_OUTPUT_NAMES, canonical_publication_template,
    publication_parameters, source_generation_id,
    split_publication_templates, validate_source_generation_id,
)
from test_entra_publication import APIM


def template():
    return {
        "languageVersion": "2.0",
        "parameters": {"names": {"type": "object"}},
        "variables": {"value": "synthetic"},
        "functions": [],
        "resources": {
            "apim": {"existing": True, "type": "Microsoft.ApiManagement/service", "apiVersion": "2024-05-01",
                     "name": "[parameters('names').apim]"},
            "api": {"type": "Microsoft.ApiManagement/service/apis", "name": "synthetic/model", "apiVersion": "2024-05-01"},
        },
        "outputs": {
            "entraPublicationSource": {"type": "object", "value": {"schemaVersion": 2}},
            "credentialBrowserPublicationSource": {"type": "object", "value": {}},
            "publishedApiResourceIds": {"type": "array", "value": []},
        },
    }


def plan(stem="test-fixture"):
    return {"deploymentId": APIM.split("/providers/", 1)[0] + "/providers/Microsoft.Resources/deployments/" + stem + "-api-publication",
            "apimId": APIM, "parameters": {"names": {"value": {"apim": "synthetic"}}}}


class PublicationTemplateTests(unittest.TestCase):
    def test_split_keeps_resources_and_source_expressions_without_aliasing_or_binding(self):
        original = template()
        saved = copy.deepcopy(original)
        publication, source = split_publication_templates(original)
        self.assertEqual(original, saved)
        self.assertEqual(publication["resources"], original["resources"])
        self.assertEqual(set(source["resources"]), {"apim"})
        self.assertEqual(set(source["outputs"]), set(SOURCE_OUTPUT_NAMES))
        self.assertEqual(set(publication["outputs"]), {"publishedApiResourceIds"})
        self.assertNotIn(SOURCE_GENERATION_OUTPUT, publication["outputs"])
        for name in ("variables", "functions", "parameters"):
            self.assertEqual(source[name], original[name])
        for name in SOURCE_OUTPUT_NAMES:
            self.assertEqual(source["outputs"][name], original["outputs"][name])
        publication["resources"]["api"]["name"] = "changed"
        source["variables"]["value"] = "changed"
        self.assertEqual(original, saved)

    def test_old_fixture_without_both_source_outputs_is_independently_copied(self):
        original = {"resources": [{"type": "synthetic"}], "outputs": {"publishedApiResourceIds": {"value": []}}}
        publication, source = split_publication_templates(original)
        self.assertIsNone(source)
        self.assertEqual(publication, original)
        publication["resources"].clear()
        self.assertEqual(len(original["resources"]), 1)

    def test_partial_source_outputs_or_modified_existing_declaration_are_rejected(self):
        for mutate in (
            lambda value: value["outputs"].pop(SOURCE_OUTPUT_NAMES[0]),
            lambda value: value["outputs"].pop(SOURCE_OUTPUT_NAMES[1]),
            lambda value: value["resources"]["apim"].update(existing=False),
            lambda value: value["resources"]["apim"].update(existing=1),
            lambda value: value["resources"]["apim"].update(properties={}),
            lambda value: value["resources"]["apim"].update(apiVersion="2025-01-01"),
            lambda value: value.update(languageVersion="1.0"),
        ):
            value = template()
            mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                split_publication_templates(value)

    def test_only_exact_apim_reference_is_allowed_even_through_variables_or_functions(self):
        allowed = template()
        allowed["variables"]["identity"] = "[reference('apim', '2024-05-01', 'full').identity.principalId]"
        split_publication_templates(allowed)
        for expression in ("[reference('api', '2024-05-01', 'full')]", "[reference(parameters('target'))]",
                           "[references('apis', 'full')]", "[listKeys('synthetic', '2024-05-01')]",
                           "[resourceInfo('api')]", "[deployment().name]"):
            for location in ("variables", "output", "functions"):
                value = template()
                if location == "variables": value["variables"]["identity"] = expression
                elif location == "functions": value["functions"] = [{"namespace": "fixture", "members": {"value": {"output": {"value": expression}}}}]
                else: value["outputs"][SOURCE_OUTPUT_NAMES[0]]["value"] = expression
                with self.subTest(location=location, expression=expression), self.assertRaises(ValueError):
                    split_publication_templates(value)

    def test_xml_csharp_and_quoted_arm_literals_are_not_treated_as_live_references(self):
        value = template()
        value["variables"]["xml"] = '<fragment>var note = "reference(another) listKeys(another)";</fragment>'
        value["variables"]["literal"] = "[createObject('note', 'reference(''api'') listKeys(''api'')')]"
        value["variables"]["escaped"] = "[[reference('api')]"
        split_publication_templates(value)


class SourceGenerationIdTests(unittest.TestCase):
    def test_generation_depends_on_code_parameters_apim_and_identity_and_has_bounded_name(self):
        original, inputs = template(), plan()
        saved = copy.deepcopy((original, inputs))
        expected = source_generation_id(inputs, original, identity_principal_id="synthetic-principal")
        self.assertEqual(source_generation_id(copy.deepcopy(inputs), copy.deepcopy(original), identity_principal_id="synthetic-principal"), expected)
        self.assertEqual((original, inputs), saved)
        alternatives = []
        code = copy.deepcopy(original); code["variables"]["value"] = "different"
        alternatives.append(source_generation_id(inputs, code, identity_principal_id="synthetic-principal"))
        config = copy.deepcopy(inputs); config["parameters"]["names"]["value"]["apim"] = "different"
        alternatives.append(source_generation_id(config, original, identity_principal_id="synthetic-principal"))
        config = copy.deepcopy(inputs); config["apimId"] += "-different"
        alternatives.append(source_generation_id(config, original, identity_principal_id="synthetic-principal"))
        alternatives.append(source_generation_id(inputs, original, identity_principal_id="different-principal"))
        self.assertTrue(all(value != expected for value in alternatives))
        long = plan("synthetic-" + "x" * 35)
        value = source_generation_id(long, original)
        self.assertLessEqual(len(value.rsplit("/", 1)[-1]), 64)
        self.assertTrue(value.rsplit("/", 1)[-1].startswith("synthetic-"))
        self.assertEqual(validate_source_generation_id(long, value), value)

    def test_mapping_order_is_stable_and_invalid_json_inputs_do_not_leak_values(self):
        original, inputs = template(), plan()
        reordered = {key: original[key] for key in reversed(original)}
        self.assertEqual(source_generation_id(inputs, original), source_generation_id(inputs, reordered))
        for bad in (float("nan"), b"not-json"):
            value = copy.deepcopy(original); value["variables"]["value"] = bad
            with self.assertRaisesRegex(ValueError, "valid JSON values"):
                source_generation_id(inputs, value)

    def test_binding_accepts_only_its_owned_parent_and_source_generation_name(self):
        inputs = plan()
        expected = source_generation_id(inputs, template())
        self.assertEqual(validate_source_generation_id(inputs, expected), expected)
        for value in (inputs["deploymentId"], expected + "?api-version=1", expected + "#fragment",
                      expected + "/../different", expected.replace("/resourceGroups/", "/otherGroups/"),
                      expected.replace("test-fixtur-src-", "unrelated-src-"), expected[:-1] + "z"):
            with self.subTest(binding=value), self.assertRaises(ValueError):
                validate_source_generation_id(inputs, value)


class PublicationParameterTests(unittest.TestCase):
    def test_defaults_and_arm_type_metadata_produce_the_same_full_parameters_and_generation(self):
        original, inputs = template(), plan()
        original["parameters"].update(
            enabled={"type": "bool", "defaultValue": False},
            options={"type": "object", "defaultValue": {"models": ["synthetic"], "count": 0}},
            note={"type": "string", "defaultValue": "[[synthetic]"},
        )
        saved = copy.deepcopy((original, inputs))
        full = publication_parameters(original, inputs["parameters"])
        self.assertEqual(full, {"names": inputs["parameters"]["names"], "enabled": {"value": False},
                                "options": {"value": {"models": ["synthetic"], "count": 0}},
                                "note": {"value": "[synthetic]"}})
        readback = {name: {"value": copy.deepcopy(wrapper["value"]), "type": original["parameters"][name]["type"]}
                    for name, wrapper in full.items()}
        self.assertEqual(publication_parameters(original, readback), full)
        recorded = copy.deepcopy(inputs)
        recorded["parameters"] = readback
        self.assertEqual(source_generation_id(inputs, original), source_generation_id(recorded, original))
        full["options"]["value"]["models"].append("changed")
        self.assertEqual((original, inputs), saved)

    def test_legacy_without_declarations_keeps_independent_value_wrappers_and_strips_type(self):
        inputs = {"synthetic": {"value": {"list": [1]}, "type": "Object"}}
        result = publication_parameters({}, inputs)
        self.assertEqual(result, {"synthetic": {"value": {"list": [1]}}})
        result["synthetic"]["value"]["list"].clear()
        self.assertEqual(inputs["synthetic"]["value"]["list"], [1])

    def test_unknown_required_and_non_value_wrappers_are_rejected(self):
        original = template()
        for inputs in ({}, {"unknown": {"value": "synthetic"}}, {"names": "synthetic"},
                       {"names": {"type": "Object"}}, {"names": {"value": {}, "reference": {}}},
                       {"names": {"value": {}, "type": None}}, {"names": {"value": float("nan")}},
                       {"names": {"value": b"not-json"}}):
            with self.subTest(parameters=inputs), self.assertRaises(ValueError):
                publication_parameters(original, inputs)
        for declarations in (None, [], {"names": "invalid"}):
            with self.subTest(declarations=declarations), self.assertRaises(ValueError):
                publication_parameters({"parameters": declarations}, plan()["parameters"])
        with self.assertRaises(ValueError):
            publication_parameters({"parameters": {}}, plan()["parameters"])

    def test_defaults_containing_arm_expressions_are_rejected_but_explicit_values_are_literal(self):
        for value in ("[parameters('names')]", {"nested": ["[reference('apim')]"]}):
            original = template()
            original["parameters"]["note"] = {"type": "string", "defaultValue": value}
            with self.subTest(default=value), self.assertRaisesRegex(ValueError, "literal JSON values"):
                publication_parameters(original, plan()["parameters"])
            inputs = copy.deepcopy(plan()["parameters"])
            inputs["note"] = {"value": value}
            self.assertEqual(publication_parameters(original, inputs)["note"]["value"], value)


class CanonicalPublicationTemplateTests(unittest.TestCase):
    def test_export_schema_casing_and_omitted_top_metadata_keep_the_same_generation(self):
        original = template()
        original["metadata"] = {"_generator": {"name": "synthetic", "version": "1.0"}, "description": "fixture"}
        original["functions"] = [{"namespace": "fixture", "members": {"value": {
            "parameters": [{"name": "input", "type": "string"}],
            "output": {"type": "bool", "value": "[equals(parameters('input'), 'synthetic')]"},
        }}}]
        exported = copy.deepcopy(original)
        exported.pop("metadata")
        for section in ("parameters", "outputs"):
            for declaration in exported[section].values():
                declaration["type"] = declaration["type"].capitalize()
        member = exported["functions"][0]["members"]["value"]
        member["parameters"][0]["type"] = "String"
        member["output"]["type"] = "Bool"
        saved = copy.deepcopy((original, exported))
        self.assertEqual(canonical_publication_template(original), canonical_publication_template(exported))
        self.assertEqual(source_generation_id(plan(), original), source_generation_id(plan(), exported))
        self.assertEqual((original, exported), saved)

    def test_actual_expressions_and_csharp_text_changes_still_change_the_generation(self):
        original = template()
        original["variables"]["expression"] = "[createObject('value', 'synthetic')]"
        original["variables"]["policy"] = '<fragment>var type = "Object"; return "synthetic";</fragment>'
        expected = source_generation_id(plan(), original)
        for key, value in (("expression", "[createObject('value', 'different')]"),
                           ("policy", '<fragment>var type = "object"; return "synthetic";</fragment>')):
            changed = copy.deepcopy(original)
            changed["variables"][key] = value
            with self.subTest(variable=key):
                self.assertNotEqual(source_generation_id(plan(), changed), expected)

    def test_type_normalization_is_limited_to_known_types_at_the_declared_schema_positions(self):
        original = template()
        original["parameters"].update(
            custom={"type": "Synthetic", "defaultValue": "literal"},
            secure={"type": "SecureString", "defaultValue": "literal"},
            config={"type": "Object", "defaultValue": {"type": "Object", "metadata": {"type": "String"}}},
        )
        original["outputs"]["publishedApiResourceIds"]["type"] = "Array"
        original["outputs"]["custom"] = {"type": "Synthetic", "value": "literal"}
        original["variables"] = {"type": "Object", "metadata": {"type": "String"}}
        original["resources"]["api"]["properties"] = {"type": "Object", "metadata": {"type": "String"}}
        original["functions"] = [{"namespace": "fixture", "members": {"value": {
            "type": "Object", "metadata": {"type": "String"},
            "parameters": [{"name": "input", "type": "SecureObject", "metadata": {"type": "String"}}],
            "output": {"type": "Int", "value": {"type": "Object"}},
        }}}]
        result = canonical_publication_template(original)
        self.assertEqual(result["parameters"]["custom"]["type"], "Synthetic")
        self.assertEqual(result["outputs"]["custom"]["type"], "Synthetic")
        self.assertEqual(result["parameters"]["secure"]["type"], "securestring")
        self.assertEqual(result["parameters"]["config"]["type"], "object")
        self.assertEqual(result["parameters"]["config"]["defaultValue"], original["parameters"]["config"]["defaultValue"])
        self.assertEqual(result["outputs"]["publishedApiResourceIds"]["type"], "array")
        for section in ("resources", "variables"):
            self.assertEqual(result[section], original[section])
        member = result["functions"][0]["members"]["value"]
        self.assertEqual(member["type"], "Object")
        self.assertEqual(member["metadata"], {"type": "String"})
        self.assertEqual(member["parameters"][0]["type"], "secureobject")
        self.assertEqual(member["parameters"][0]["metadata"], {"type": "String"})
        self.assertEqual(member["output"], {"type": "int", "value": {"type": "Object"}})
        result["resources"]["api"]["properties"]["type"] = "changed"
        self.assertEqual(original["resources"]["api"]["properties"]["type"], "Object")


if __name__ == "__main__":
    unittest.main()

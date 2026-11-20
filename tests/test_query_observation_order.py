"""queryを呼ぶAPIだけ、認証完了確認を先行する配備契約を検証する。"""
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_api_request import API_REQUEST_PREPARATION_ID, render_api_request_preparation_fragment
from deployment.helpers.apim_query_observation import validate_query_observation_scope
from test_api_request_preparation import APIM, CALL, policy
from test_entra_publication import publication


QUERY = '<include-fragment fragment-id="core-query-observation" />'
PREPARATION = APIM + "/policyFragments/" + API_REQUEST_PREPARATION_ID
API = APIM + "/apis/model/policies/policy"


def graph(value):
    return {API: value, PREPARATION: render_api_request_preparation_fragment()}


class QueryGuardOrderTests(unittest.TestCase):
    def test_current_api_templates_run_query_once_after_the_fixed_preparation(self):
        from configuration_fixtures import CORE
        for filename, name in (("gateway.xml", "model"), ("mcp.xml", "native-mcp")):
            with self.subTest(template=filename):
                value = (CORE / "apps/gateway/apim/policies/api-templates" / filename).read_text(encoding="utf-8")
                validate_query_observation_scope({APIM + "/apis/" + name + "/policies/policy": value,
                                           PREPARATION: render_api_request_preparation_fragment()}, APIM)

    def test_other_api_processing_may_remain_between_preparation_and_query(self):
        value = policy(after='<set-variable name="gatewayControlSettings" value="{}" />' + QUERY)
        validate_query_observation_scope(graph(value), APIM, api_properties={APIM + "/apis/model": {"type": "http"}})

    def test_legacy_policies_without_query_do_not_require_the_new_preparation(self):
        for value in (policy(""), policy('<set-variable name="legacy" value="unchanged" />')):
            with self.subTest(value=value):
                validate_query_observation_scope({API: value}, APIM)

    def test_missing_late_duplicate_or_nested_preparation_or_query_is_rejected(self):
        values = (
            policy("", after=QUERY),
            policy("", after=QUERY + CALL),
            policy(after=QUERY + QUERY),
            policy(after='<choose><when condition="@(true)">' + QUERY + '</when></choose>'),
            policy('<choose><when condition="@(true)">' + CALL + '</when></choose>', after=QUERY),
            policy(CALL + CALL, after=QUERY),
            policy('<set-variable name="unexpected" value="before-preparation" />' + CALL, after=QUERY),
            policy(after="").replace('<outbound><base />', '<outbound><base />' + QUERY),
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_query_observation_scope(graph(value), APIM)

    def test_missing_or_changed_preparation_leaf_cannot_supply_the_authentication_boundary(self):
        value = policy(after=QUERY)
        with self.assertRaises(ValueError):
            validate_query_observation_scope({API: value}, APIM)
        changed = ET.fromstring(render_api_request_preparation_fragment())
        changed.find("choose/when").set("condition", "@(false)")
        with self.assertRaises(ValueError):
            validate_query_observation_scope({API: value, PREPARATION: ET.tostring(changed, encoding="unicode")}, APIM)

    def test_unrelated_fragment_global_or_operation_cannot_invoke_query_with_a_fake_preparation(self):
        for suffix, value in (
            ("/policies/policy", policy(after=QUERY)),
            ("/apis/model/operations/responses/policies/policy", policy(after=QUERY)),
            ("/policyFragments/unrelated", "<fragment>" + CALL + QUERY + "</fragment>"),
        ):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                validate_query_observation_scope({APIM + suffix: value, PREPARATION: render_api_request_preparation_fragment()}, APIM)

    def test_formal_publication_scope_uses_the_same_order_contract(self):
        for value in (policy("", after=QUERY), policy(after=QUERY + QUERY)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                publication._audit_fragment_scope(graph(value), APIM)


if __name__ == "__main__":
    unittest.main()

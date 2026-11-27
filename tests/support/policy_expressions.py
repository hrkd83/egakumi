"""Read executable APIM C# from the repository policy sources."""

import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import expand_audit_fragments, render_audit_policy
from deployment.helpers.apim_ingress import expand_trusted_ingress_fragment
from tests import REPOSITORY
from tests.support.policy_fixture_paths import policy_fixture_path


def expression(path, xpath, attribute=None):
    """XML内の指定位置からAPIMの実C#式を取り出す。想定外の形式なら試験を止める。"""
    rendered = expand_audit_fragments(expand_trusted_ingress_fragment(render_audit_policy(path.read_text(encoding="utf-8"))))
    element = ET.fromstring(rendered).find(xpath)
    if element is None:
        raise AssertionError("Expected the generated APIM expression at its original execution position")
    value = element.get(attribute) if attribute else element.text
    if not value or not value.strip().startswith("@{") or not value.strip().endswith("}"):
        raise AssertionError("Expected an APIM multi-statement expression")
    return value.strip()[2:-1]



def single_expression(value):
    """呼出元XMLの単一式を取り出し、判定を試験側へ書き写さずに実行する。"""
    if not value or not value.startswith("@(") or not value.endswith(")"):
        raise AssertionError("Expected a single-statement APIM expression")
    return value[2:-1]



POLICIES = REPOSITORY / "apps/api-gateway/policies"
LIMIT_EXPRESSION = expression(policy_fixture_path(POLICIES, "core-model-limits.xml"), "./set-variable", "value")

"""新しい固定bool部品と先頭分岐だけを旧inline形へ射影し、残りの厳密比較を可能にする。"""
import copy
import xml.etree.ElementTree as ET

from deployment.helpers.apim_authentication import _render_inline_trusted_ingress_service_policy, _structure
from deployment.helpers.apim_browser_exception import (
    BROWSER_EXCEPTION_ID, BROWSER_EXCEPTION_VARIABLE,
    expand_browser_exception_fragment, render_browser_exception_fragment,
)
from deployment.helpers.apim_common_authentication import (
    expand_common_authentication_fragments, render_common_authentication_fragments,
)
from test_credential_login import settings as login_settings
from test_telemetry_source_explanations import executable_tokens


BOOLEAN_BRANCH = ('<when condition="@((bool)context.Variables[&quot;gatewayCredentialBrowserException&quot;])">'
                  '<set-variable name="gatewayCommonAuthenticationAnonymous" value="@(true)" /></when>')
FALSE_TEMPLATE_BRANCH = '<when condition="@(false)"><set-variable name="gatewayCommonAuthenticationAnonymous" value="@(true)" /></when>'


def project_browser_exception_to_previous(xml, login=None):
    """固定writer・呼出位置・旧predicateとの一致を確認し、その1部品と先頭whenだけを戻す。"""
    enabled = ET.fromstring(xml).find('.//include-fragment[@fragment-id="core-entra-authentication-error"]') is not None
    xml = expand_common_authentication_fragments(xml,
        render_common_authentication_fragments(entra_enabled=enabled), entra_enabled=enabled)
    root = ET.fromstring(expand_browser_exception_fragment(xml, login=login))
    inbound = root.find("inbound")
    writers = [node for node in root.iter() if node.get("id") == BROWSER_EXCEPTION_ID
               or node.tag == "set-variable" and node.get("name") == BROWSER_EXCEPTION_VARIABLE]
    expected_writer = ET.fromstring(render_browser_exception_fragment(login))[0]
    if (inbound is None or len(writers) != 1 or writers[0] not in list(inbound)
            or _structure(writers[0]) != _structure(expected_writer)):
        raise AssertionError("Expected one exact browser exception Boolean writer")
    writer = writers[0]
    if [node.get("id") for node in list(inbound)[:4]] != [
            "core-request-context", "core-trusted-ingress", BROWSER_EXCEPTION_ID, "core-common-authentication"]:
        raise AssertionError("Expected the browser exception directly between ingress and authentication")
    authentication = inbound.find("choose[@id='core-common-authentication']")
    branches = list(authentication)
    if not branches or _structure(branches[0]) != _structure(ET.fromstring(BOOLEAN_BRANCH)):
        raise AssertionError("Expected only the fixed Boolean-reference first branch")

    # The configured frozen renderer supplies the old four-operation predicate.
    # Even when CM is absent, the new source must preserve that exact predicate
    # behind its false configuration guard; no general comment/code normalization is used.
    prior = ET.fromstring(_render_inline_trusted_ingress_service_policy(login or login_settings()))
    prior_branches = list(prior.find("inbound/choose[@id='core-common-authentication']"))
    condition = prior_branches[0].get("condition", "")
    if not condition.startswith("@(") or not condition.endswith(")"):
        raise AssertionError("Expected the fixed previous browser predicate")
    expected_expression = "@{ return " + ("true" if login else "false") + " && " + condition[2:-1] + "; }"
    if executable_tokens(writer.get("value")) != executable_tokens(expected_expression):
        raise AssertionError("Browser exception differs from the fixed previous predicate")
    if any(branch.tag == "when" and branch.get("condition") == "@(false)" for branch in branches):
        raise AssertionError("The current authentication must not contain the retired false template branch")
    dummy = ET.fromstring(FALSE_TEMPLATE_BRANCH)
    if len(prior_branches) < 2 or _structure(prior_branches[1]) != _structure(dummy):
        raise AssertionError("Expected the fixed false template only in the previous authentication source")

    inbound.remove(writer)
    authentication.insert(1, copy.deepcopy(dummy))
    if login:
        branches[0].set("condition", condition)
    else:
        authentication.remove(branches[0])
    return ET.tostring(root, encoding="unicode")

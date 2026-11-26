"""独立した合成説明を、現在の正本から生成した認証sourceへ加える。"""
import xml.etree.ElementTree as ET

from deployment.helpers import apim_common_authentication as fragments
from deployment.helpers.apim_authentication import render_service_policy


PREVIOUS_EXPLANATION = ' /* synthetic previous explanation: "not a literal" #if UNUSED */ '


def annotated_authentication_source(value):
    """コードや文字列を変更せず、式の先頭へ不変の合成コメントだけを加える。"""
    root = ET.fromstring(value)
    for node in root.iter():
        for name, text in list(node.attrib.items()):
            if text.lstrip().startswith(("@{", "@(")):
                prefix = "@{" if text.lstrip().startswith("@{") else "@("
                node.set(name, text.replace(prefix, prefix + PREVIOUS_EXPLANATION, 1))
        if node.text and node.text.lstrip().startswith(("@{", "@(")):
            prefix = "@{" if node.text.lstrip().startswith("@{") else "@("
            node.text = node.text.replace(prefix, prefix + PREVIOUS_EXPLANATION, 1)
    return ET.tostring(root, encoding="unicode")


def previous_authentication_fixture(enabled=True):
    # Publication suites import this helper too; defer their fixture import to
    # avoid a module cycle. No legacy XML or retired producer is used.
    from test_publish_apis import publication_fixture, publish
    from test_entra_gateway_authentication import AUDIENCE, OTHER

    config, state = publication_fixture()
    config["entraTenantId"] = OTHER
    if enabled:
        config["gateway"]["authentication"]["entra"] = {
            "audience": AUDIENCE, "requiredScope": "Gateway.Invoke",
            "userLimits": {"requestRate": None, "tokensPerMinute": None, "tokenQuota": None},
        }
    plan = publish.prepare_plan(config, state)
    settings = plan["entraAuthentication"] if enabled else None
    current = fragments.render_common_authentication_fragments(entra_enabled=enabled)
    previous = {name: annotated_authentication_source(value) for name, value in current.items()}
    global_xml = annotated_authentication_source(render_service_policy(entra=settings))
    return config, state, plan, settings, current, previous, global_xml


def credential_broker_inline_fixture(login, *, entra=None):
    """固定の匿名Broker移行候補だけを、現在のinline正本から合成する。"""
    from deployment.helpers import credential_login
    from deployment.helpers.apim_authentication import _render_inline_common_authentication_service_policy
    from deployment.helpers.apim_browser_exception import BROWSER_EXCEPTION_ID

    root = ET.fromstring(_render_inline_common_authentication_service_policy(login, entra=entra))
    inbound = root.find("inbound")
    include = inbound.find("include-fragment[@id='" + BROWSER_EXCEPTION_ID + "']")
    inbound.remove(include)
    authentication = inbound.find("choose[@id='core-common-authentication']")
    authentication.find("when").set("condition", ET.fromstring(credential_login.anonymous_policy(login)).get("condition"))
    authentication.insert(1, ET.fromstring('<when condition="@(false)"><set-variable name="gatewayCommonAuthenticationAnonymous" value="@(true)" /></when>'))
    return ET.tostring(root, encoding="unicode")

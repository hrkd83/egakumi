"""API固有の設定を、baseより前で展開する固定leaf fragmentとして扱う。"""
from __future__ import annotations

import base64
import binascii
import copy
import json
import xml.etree.ElementTree as ET

from .apim_audit import CORE_API_SETTINGS_MARKER, render_audit_policy
from .paths import REPOSITORY


API_SETTINGS_ID = "core-api-settings"
API_SETTINGS_SOURCE_VARIABLE = "gatewayApiSettingsSource"
API_SETTINGS_VARIABLES = ("gatewayApiSettings", "gatewayAuditSettings", "gatewayControlSettings")
_INCLUDE = {"id": API_SETTINGS_ID, "fragment-id": API_SETTINGS_ID}
_DIRECTORY = REPOSITORY / "apps/apim/policies/fragments/settings" / API_SETTINGS_ID


def _xml(value: str) -> ET.Element:
    if not isinstance(value, str) or any(mark in value.upper() for mark in ("<!DOCTYPE", "<!ENTITY")):
        raise ValueError("API settings require safe policy XML")
    try:
        return ET.fromstring(value, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError("API settings require valid policy XML") from None


def _structure(root):
    def content(value):
        return value if value and value.strip() else ""
    return (root.tag, tuple(sorted(root.attrib.items())), content(root.text),
            tuple((_structure(child), content(child.tail)) for child in root if isinstance(child.tag, str)))


def _fragment_node(value: str) -> ET.Element:
    root = _xml(value)
    children = [node for node in root if isinstance(node.tag, str)]
    if (root.tag != "fragment" or root.attrib or len(children) != 3
            or tuple(node.get("name") for node in children) != API_SETTINGS_VARIABLES
            or any(node.tag != "set-variable" or set(node.attrib) != {"name", "value"} or list(node)
                   for node in children)
            or len(value.encode("utf-8")) > 512 * 1024):
        raise ValueError("API settings fragment must contain only its three fixed variable writers")
    return root


def render_api_settings_fragment() -> str:
    """単一の監査選択式を正本から展開し、説明コメントもAzureへ渡す。"""
    template = (_DIRECTORY / (API_SETTINGS_ID + ".xml")).read_text(encoding="utf-8")
    if template.count(CORE_API_SETTINGS_MARKER) != 1:
        raise ValueError("API settings require exactly one direct C# source marker")
    rendered = render_audit_policy(template)
    _fragment_node(rendered)
    return rendered


def _source_settings(node: ET.Element) -> dict:
    """設定は生成済みBase64の文字列そのもの。要求値や実行時式をseedとして受けない。"""
    if node.tag != "set-variable" or set(node.attrib) != {"name", "value"} or list(node):
        raise ValueError("API settings require a literal generated source variable")
    try:
        value = node.get("value", "")
        decoded = base64.b64decode(value, validate=True)
        if base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError("API settings source Base64 must be canonical")
        lines = decoded.decode("utf-8").split("\n")
    except (ValueError, UnicodeError, binascii.Error):
        raise ValueError("API settings source must be a generated Base64 three-line value") from None
    if len(lines) != 3 or lines[0] not in ("http", "websocket", "mcp"):
        raise ValueError("API settings source must contain its supported API type and two original JSON lines")
    settings = dict(zip(("apiType", "audit", "control"), lines))
    try:
        if any(not isinstance(json.loads(settings[name]), dict) for name in ("audit", "control")):
            raise ValueError("API settings JSON strings must encode objects")
    except ValueError:
        raise ValueError("API settings audit/control strings must contain their generated JSON objects") from None
    return settings


def validate_api_settings_inheritance(root: ET.Element) -> bool:
    """新しいseed/callは1組でbase前に実行する。旧2変数だけの生成元はそのまま照合する。"""
    seeds = [node for node in root.iter("set-variable") if node.get("name") == API_SETTINGS_SOURCE_VARIABLE]
    includes = [node for node in root.iter("include-fragment")
                if node.get("id") == API_SETTINGS_ID or node.get("fragment-id") == API_SETTINGS_ID]
    if not seeds and not includes:
        return False
    inbound = root.find("inbound")
    nodes = list(inbound) if inbound is not None else []
    bases = [node for node in nodes if node.tag == "base"]
    if (root.tag != "policies" or len(seeds) != 1 or len(includes) != 1 or len(bases) != 1
            or seeds[0] not in nodes or includes[0] not in nodes
            or includes[0].attrib != _INCLUDE or list(includes[0])
            or includes[0].text and includes[0].text.strip()
            or nodes.index(includes[0]) != nodes.index(seeds[0]) + 1
            or nodes.index(includes[0]) >= nodes.index(bases[0])
            or any(node.tag == "set-variable" and node.get("name") in API_SETTINGS_VARIABLES
                   for node in nodes[:nodes.index(bases[0])])):
        raise ValueError("API settings require exactly one fixed seed/call pair before inbound base")
    _source_settings(seeds[0])
    return True


def validate_api_settings_scope(policies: dict[str, str], apim_id: str, *, api_properties: dict | None = None) -> None:
    """API入口またはWSの握手だけが設定を渡す。共通認証・CM・別fragmentから呼び出さない。"""
    fragment_id = apim_id + "/policyFragments/" + API_SETTINGS_ID
    referenced = False
    configured_apis = set()
    for resource_id, value in policies.items():
        root = _xml(value)
        if resource_id == fragment_id:
            if _structure(_fragment_node(value)) != _structure(_fragment_node(render_api_settings_fragment())):
                raise ValueError("API settings fragment differs from its complete fixed source")
            continue
        seed_or_call = any(node.tag == "set-variable" and node.get("name") == API_SETTINGS_SOURCE_VARIABLE
                           or node.tag == "include-fragment" and (
                               node.get("id") == API_SETTINGS_ID or node.get("fragment-id") == API_SETTINGS_ID)
                           for node in root.iter())
        if seed_or_call:
            suffix = resource_id.removeprefix(apim_id).split("/")
            api_policy = len(suffix) == 5 and suffix[3:] == ["policies", "policy"]
            handshake = len(suffix) == 7 and suffix[3:] == ["operations", "onHandshake", "policies", "policy"]
            if not resource_id.startswith(apim_id + "/apis/") or not (api_policy or handshake):
                raise ValueError("Only API policy or its system handshake may invoke API settings")
            validate_api_settings_inheritance(root)
            api_id = apim_id + "/apis/" + suffix[2]
            seed = root.find("inbound/set-variable[@name='" + API_SETTINGS_SOURCE_VARIABLE + "']")
            api_type = _source_settings(seed)["apiType"]
            if api_id in configured_apis or handshake != (api_type == "websocket"):
                raise ValueError("Each API must initialize its settings exactly once at its protocol entry")
            if api_properties is not None and (api_id not in api_properties
                    or api_type != (api_properties[api_id].get("type") or "http")):
                raise ValueError("API settings type must match the complete generated API source")
            configured_apis.add(api_id)
            referenced = True
        if any(node.tag == "set-variable" and node.get("name") == API_SETTINGS_VARIABLES[0]
               for node in root.iter()):
            raise ValueError("Only the API settings fragment may write the decoded settings")
    if referenced and fragment_id not in policies:
        raise ValueError("API settings calls require their complete generated fragment source")


def expand_api_settings_fragment(value: str, fragment: str | None = None) -> str:
    """実式試験では確認済みのleafだけをAPI側の呼出位置へ展開する。"""
    root = _xml(value)
    if not validate_api_settings_inheritance(root):
        return value
    expected = _fragment_node(render_api_settings_fragment())
    parsed = expected if fragment is None else _fragment_node(fragment)
    if _structure(parsed) != _structure(expected):
        raise ValueError("API settings fragment differs from its complete fixed source")
    inbound = root.find("inbound")
    include = inbound.find("include-fragment[@id='" + API_SETTINGS_ID + "']")
    position = list(inbound).index(include)
    replacement = [copy.deepcopy(node) for node in parsed]
    inbound.remove(include)
    for offset, node in enumerate(replacement):
        inbound.insert(position + offset, node)
    replacement[-1].tail = include.tail
    return ET.tostring(root, encoding="unicode")

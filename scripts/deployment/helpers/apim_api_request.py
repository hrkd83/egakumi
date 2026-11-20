"""共通認証の直後で、API要求のヘッダーと認証完了を準備するleaf部品。"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

from .paths import REPOSITORY


API_REQUEST_PREPARATION_ID = "core-api-request-preparation"
_INCLUDE = {"id": API_REQUEST_PREPARATION_ID, "fragment-id": API_REQUEST_PREPARATION_ID}
_DIRECTORY = REPOSITORY / "apps/gateway/apim/policies/fragments/request" / API_REQUEST_PREPARATION_ID


def _xml(value: str) -> ET.Element:
    if not isinstance(value, str) or any(mark in value.upper() for mark in ("<!DOCTYPE", "<!ENTITY")):
        raise ValueError("API request preparation requires safe XML")
    try:
        return ET.fromstring(value, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError("API request preparation requires valid XML") from None


def _nodes(root):
    return [node for node in root if isinstance(node.tag, str)]


def _structure(root):
    def content(value):
        return value if value and value.strip() else ""
    return (root.tag, tuple(sorted(root.attrib.items())), content(root.text),
            tuple((_structure(child), content(child.tail)) for child in _nodes(root)))


def _fragment(value: str) -> ET.Element:
    root = _xml(value)
    nodes = _nodes(root)
    if (root.tag != "fragment" or root.attrib or len(nodes) != 4
            or [node.tag for node in nodes] != ["set-header", "set-header", "set-header", "choose"]
            or [node.get("name") for node in nodes[:3]] != ["X-Core-Caller-Id", "X-Core-Subscription-Id", "x-ms-client-request-id"]
            or any(node.tag in ("include-fragment", "base") for node in root.iter())
            or len(value.encode("utf-8")) > 512 * 1024):
        raise ValueError("API request preparation must contain only its fixed headers and completion check")
    return root


def render_api_request_preparation_fragment() -> str:
    """式の展開が不要なXMLをそのまま読み、Azureへ説明コメントも登録する。"""
    value = (_DIRECTORY / (API_REQUEST_PREPARATION_ID + ".xml")).read_text(encoding="utf-8")
    _fragment(value)
    return value


def _call(root: ET.Element) -> ET.Element | None:
    calls = [node for node in root.iter("include-fragment")
             if node.get("id") == API_REQUEST_PREPARATION_ID or node.get("fragment-id") == API_REQUEST_PREPARATION_ID]
    if not calls:
        # 移行前の生成元は、従来のインライン処理のままで読戻し照合します。
        return None
    inbound = root.find("inbound")
    nodes = _nodes(inbound) if inbound is not None else []
    bases = [node for node in nodes if node.tag == "base"]
    if (root.tag != "policies" or len(calls) != 1 or len(bases) != 1
            or calls[0] not in nodes or calls[0].attrib != _INCLUDE or list(calls[0])
            or (calls[0].text and calls[0].text.strip())
            or nodes.index(calls[0]) != nodes.index(bases[0]) + 1):
        raise ValueError("API request preparation must run exactly once immediately after inbound base")
    return calls[0]


def validate_api_request_preparation_scope(policies: dict[str, str], apim_id: str, *, api_properties: dict | None = None) -> None:
    """呼出位置・部品の全文を確認し、別API操作や入れ子からの呼出しを受けない。"""
    fragment_id = apim_id + "/policyFragments/" + API_REQUEST_PREPARATION_ID
    expected = _structure(_fragment(render_api_request_preparation_fragment()))
    referenced = False
    for resource_id, value in policies.items():
        if resource_id == fragment_id:
            if _structure(_fragment(value)) != expected:
                raise ValueError("API request preparation fragment differs from its fixed source")
            continue
        call = _call(_xml(value))
        if call is None:
            continue
        suffix = resource_id.removeprefix(apim_id).split("/")
        api_policy = len(suffix) == 5 and suffix[3:] == ["policies", "policy"]
        handshake = len(suffix) == 7 and suffix[3:] == ["operations", "onHandshake", "policies", "policy"]
        if not resource_id.startswith(apim_id + "/apis/") or not (api_policy or handshake):
            raise ValueError("Only an API entry or its system handshake may prepare the request")
        if api_properties is not None:
            api_id = apim_id + "/apis/" + suffix[2]
            properties = api_properties.get(api_id)
            if properties is None or handshake != (properties.get("type") == "websocket"):
                raise ValueError("API request preparation must match its generated protocol entry")
        referenced = True
    if referenced and fragment_id not in policies:
        raise ValueError("API request preparation calls require their complete generated fragment source")


def expand_api_request_preparation_fragment(value: str, fragment: str | None = None) -> str:
    """実式試験で、確認済みの同じ部品をAPI側の呼出位置へ展開する。"""
    root = _xml(value)
    include = _call(root)
    if include is None:
        return value
    expected = _fragment(render_api_request_preparation_fragment())
    parsed = expected if fragment is None else _fragment(fragment)
    if _structure(parsed) != _structure(expected):
        raise ValueError("API request preparation fragment differs from its fixed source")
    inbound = root.find("inbound")
    position = list(inbound).index(include)
    replacement = [copy.deepcopy(node) for node in parsed]
    inbound.remove(include)
    for offset, node in enumerate(replacement):
        inbound.insert(position + offset, node)
    replacement[-1].tail = include.tail
    return ET.tostring(root, encoding="unicode")

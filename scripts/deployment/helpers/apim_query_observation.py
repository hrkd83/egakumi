"""クエリ観測部品を正本から生成し、要求準備後の呼出し順を検査する。"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .apim_audit import CORE_QUERY_OBSERVATION_MARKER, render_audit_policy
from .apim_api_request import API_REQUEST_PREPARATION_ID, validate_api_request_preparation_scope
from .paths import REPOSITORY


QUERY_OBSERVATION_FRAGMENT_ID = "core-query-observation"
_DIRECTORY = REPOSITORY / "apps/apim/policies/fragments/request" / QUERY_OBSERVATION_FRAGMENT_ID


def render_query_observation_fragment() -> str:
    """同名XMLとC#から、観測結果を書き込むだけのleaf XMLを生成する。"""
    template = (_DIRECTORY / (QUERY_OBSERVATION_FRAGMENT_ID + ".xml")).read_text(encoding="utf-8")
    if template.count(CORE_QUERY_OBSERVATION_MARKER) != 1:
        raise ValueError("Query observation must contain exactly one direct C# source marker")
    rendered = render_audit_policy(template)
    root = ET.fromstring(rendered)
    nodes = list(root)
    if (root.tag != "fragment" or root.attrib or len(nodes) != 1
            or any(node.tag != "set-variable" or set(node.attrib) != {"name", "value"} or list(node) for node in nodes)
            or nodes[0].get("name") != "gatewayQueryObservation"
            or not nodes[0].get("value", "").strip().startswith("@{")
            or not nodes[0].get("value", "").strip().endswith("}")
            or CORE_QUERY_OBSERVATION_MARKER in rendered or len(rendered.encode("utf-8")) > 512 * 1024):
        raise ValueError("Query observation must contain only its observation writer")
    return rendered


def validate_query_observation_scope(policies: dict[str, str], apim_id: str, *, api_properties: dict | None = None) -> None:
    """観測部品の呼出しがあるAPIで、認証確認済みの要求準備が先行することを検査する。"""
    selected = {}
    for resource_id, value in policies.items():
        if not isinstance(value, str) or any(mark in value.upper() for mark in ("<!DOCTYPE", "<!ENTITY")):
            raise ValueError("Query observation order requires safe policy XML")
        try:
            root = ET.fromstring(value)
        except ET.ParseError:
            raise ValueError("Query observation order requires valid policy XML") from None
        calls = [node for node in root.iter("include-fragment") if node.get("fragment-id") == QUERY_OBSERVATION_FRAGMENT_ID]
        if not calls:
            continue
        inbound = root.find("inbound")
        nodes = list(inbound) if inbound is not None else []
        preparation = [node for node in nodes if node.tag == "include-fragment" and node.get("fragment-id") == API_REQUEST_PREPARATION_ID]
        if (root.tag != "policies" or len(calls) != 1 or calls[0] not in nodes
                or len(preparation) != 1 or nodes.index(preparation[0]) >= nodes.index(calls[0])):
            raise ValueError("Query observation must run once after the API request preparation")
        selected[resource_id] = value
    if selected:
        identifier = apim_id + "/policyFragments/" + API_REQUEST_PREPARATION_ID
        if identifier in policies:
            selected[identifier] = policies[identifier]
        validate_api_request_preparation_scope(selected, apim_id, api_properties=api_properties)

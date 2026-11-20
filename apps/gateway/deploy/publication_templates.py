"""同じコンパイル済みARMを、期待値生成とAPIM子資源の配備に分ける。"""
from __future__ import annotations

import copy
import hashlib
import json
import re


SOURCE_OUTPUT_NAMES = ("entraPublicationSource", "credentialBrowserPublicationSource")
SOURCE_GENERATION_OUTPUT = "publicationSourceGenerationId"
_APIM = {"existing": True, "type": "Microsoft.ApiManagement/service", "apiVersion": "2024-05-01",
         "name": "[parameters('names').apim]"}
_ARM_TYPES = {"string", "securestring", "int", "bool", "object", "secureobject", "array"}
_ARM_TOKENS = re.compile(r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9.]*|[^\s]")
_DEPLOYMENT_ID = re.compile(
    r"(/subscriptions/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    r"/resourceGroups/[^/?#\s]+/providers/Microsoft\.Resources/deployments)/([A-Za-z0-9][A-Za-z0-9._()-]{0,63})",
    re.IGNORECASE,
)


def _check_source_references(value) -> None:
    """ARM式だけを調べる。引用されたXML/C#内の文字列はresource呼出しとして扱わない。"""
    if isinstance(value, dict):
        for item in value.values():
            _check_source_references(item)
    elif isinstance(value, list):
        for item in value:
            _check_source_references(item)
    elif isinstance(value, str) and value.startswith("[") and not value.startswith("[["):
        tokens = _ARM_TOKENS.findall(value[1:-1])
        for index, token in enumerate(tokens[:-1]):
            if tokens[index + 1] != "(" or token.startswith("'"):
                continue
            name = token.casefold()
            if name == "reference":
                if tokens[index + 1:index + 8] != ["(", "'apim'", ",", "'2024-05-01'", ",", "'full'", ")"]:
                    raise ValueError("Source generation may reference only the exact existing APIM declaration")
            elif name in ("references", "resourceinfo", "deployment") or name.startswith("list"):
                raise ValueError("Source generation contains an unsupported resource or deployment dependency")


def split_publication_templates(template: dict) -> tuple[dict, dict | None]:
    """元の式と資源を変更せず、巨大な期待値outputだけを独立したARM要求へ移す。"""
    if not isinstance(template, dict) or not isinstance(template.get("outputs", {}), dict):
        raise ValueError("The compiled publication template must be an object")
    outputs = template.get("outputs", {})
    present = [name in outputs for name in SOURCE_OUTPUT_NAMES]
    if not any(present):
        return copy.deepcopy(template), None
    if not all(present):
        raise ValueError("Both publication source outputs must be present together")
    resources = template.get("resources")
    if (template.get("languageVersion") != "2.0" or not isinstance(resources, dict)
            or not isinstance(resources.get("apim"), dict) or resources["apim"].get("existing") is not True
            or resources["apim"] != _APIM
            or any(not isinstance(outputs[name], dict) or outputs[name].get("type") != "object"
                   or "value" not in outputs[name] for name in SOURCE_OUTPUT_NAMES)):
        raise ValueError("Source generation requires the complete symbolic template and its exact existing APIM")
    publication = copy.deepcopy(template)
    source = copy.deepcopy(template)
    source["resources"] = {"apim": copy.deepcopy(resources["apim"])}
    source["outputs"] = {name: copy.deepcopy(outputs[name]) for name in SOURCE_OUTPUT_NAMES}
    for name in SOURCE_OUTPUT_NAMES:
        del publication["outputs"][name]
    _check_source_references(source)
    return publication, source


def _source_scope(plan: dict) -> tuple[str, str]:
    value = plan.get("deploymentId") if isinstance(plan, dict) else None
    match = _DEPLOYMENT_ID.fullmatch(value) if isinstance(value, str) else None
    if match is None or any(part in (".", "..") for part in value.split("/")):
        raise ValueError("The publication deployment must have a complete owned resource ID")
    parent, name = match.groups()
    stem = name.removesuffix("-api-publication")
    return parent, stem[:11]


def _literal_default(value):
    """ARMの式を評価せず、literalとエスケープ済み文字列だけを入力へ展開する。"""
    if isinstance(value, dict):
        return {key: _literal_default(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_literal_default(item) for item in value]
    if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
        if value.startswith("[["):
            return value[1:]
        raise ValueError("Publication parameter defaults must be literal JSON values")
    return copy.deepcopy(value)


def publication_parameters(template: dict, parameters: dict) -> dict:
    """省略したliteral defaultを補完し、ARM読戻しのtype情報を除いた完全入力を作る。"""
    if not isinstance(template, dict) or not isinstance(parameters, dict):
        raise ValueError("Publication parameters must be an object")
    if any(not isinstance(name, str) or not isinstance(wrapper, dict)
           or "value" not in wrapper or set(wrapper) - {"value", "type"}
           or "type" in wrapper and not isinstance(wrapper["type"], str)
           for name, wrapper in parameters.items()):
        raise ValueError("Publication parameters require only value and optional type wrappers")
    declarations = template.get("parameters")
    if "parameters" not in template:
        # 宣言のない古い合成fixtureでも、入力の値wrapperを独立したコピーで返します。
        result = {name: {"value": copy.deepcopy(wrapper["value"])} for name, wrapper in parameters.items()}
    else:
        if (not isinstance(declarations, dict) or any(not isinstance(name, str) or not isinstance(value, dict)
                                                    for name, value in declarations.items())
                or set(parameters) - set(declarations)):
            raise ValueError("Publication parameters must match their template declarations")
        result = {}
        for name, declaration in declarations.items():
            if name in parameters:
                value = copy.deepcopy(parameters[name]["value"])
            elif "defaultValue" in declaration:
                value = _literal_default(declaration["defaultValue"])
            else:
                raise ValueError("Publication parameters are missing a required value")
            result[name] = {"value": value}
    try:
        json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("Publication parameters must contain valid JSON values") from None
    return result


def canonical_publication_template(template: dict) -> dict:
    """ARM exportで変わるschema型の大文字小文字と、最上位の補助metadataだけを揃える。"""
    if not isinstance(template, dict):
        raise ValueError("The compiled publication template must be an object")
    result = copy.deepcopy(template)
    result.pop("metadata", None)

    def schema_type(declaration):
        if isinstance(declaration, dict):
            value = declaration.get("type")
            if isinstance(value, str) and value.casefold() in _ARM_TYPES:
                declaration["type"] = value.casefold()

    for section in ("parameters", "outputs"):
        declarations = result.get(section)
        if isinstance(declarations, dict):
            for declaration in declarations.values():
                schema_type(declaration)
    functions = result.get("functions")
    if isinstance(functions, list):
        for function in functions:
            members = function.get("members") if isinstance(function, dict) else None
            if not isinstance(members, dict):
                continue
            for member in members.values():
                if not isinstance(member, dict):
                    continue
                parameters = member.get("parameters")
                if isinstance(parameters, list):
                    for parameter in parameters:
                        schema_type(parameter)
                schema_type(member.get("output"))
    return result


def source_generation_id(plan: dict, template: dict, *, identity_principal_id: str | None = None) -> str:
    """コード・入力・APIM identityが同じ世代にだけ、同じ生成専用IDを割り当てる。"""
    parent, prefix = _source_scope(plan)
    if (not isinstance(template, dict) or not isinstance(plan.get("parameters"), dict)
            or not isinstance(plan.get("apimId"), str) or not plan["apimId"]
            or identity_principal_id is not None and not isinstance(identity_principal_id, str)):
        raise ValueError("Source generation requires its complete template and publication inputs")
    parameters = publication_parameters(template, plan["parameters"])
    try:
        payload = json.dumps({"template": canonical_publication_template(template), "parameters": parameters,
                              "apimId": plan["apimId"], "identityPrincipalId": identity_principal_id},
                             sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("Source generation inputs must be valid JSON values") from None
    digest = hashlib.sha256(payload).hexdigest()
    # 11文字のstem + "-src-" + 192-bit digestで、Azureの名前上限64文字を守ります。
    return parent + "/" + prefix + "-src-" + digest[:48]


def validate_source_generation_id(plan: dict, value: str) -> str:
    """適用済み配備から読む生成IDも、同じ親scopeと専用の命名形式だけへ限定する。"""
    parent, prefix = _source_scope(plan)
    match = _DEPLOYMENT_ID.fullmatch(value) if isinstance(value, str) else None
    if (match is None or match.group(1).casefold() != parent.casefold()
            or value.casefold() == plan["deploymentId"].casefold()
            or re.fullmatch(re.escape(prefix) + r"-src-[0-9a-f]{48}", match.group(2)) is None
            or any(part in (".", "..") for part in value.split("/"))):
        raise ValueError("The source generation ID is outside its owned publication scope")
    return value

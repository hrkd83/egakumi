"""標準接続の定義と企業の登録情報から、内部用の接続配列を組み立てる。

configは接続先の契約、paramsは登録IDと秘密ファイル参照を受け持つ。
配列の位置や汎用マージでは結び付けず、追加接続の名前を完全一致で照合する。
秘密ファイルを読まず、解決済みの参照文字列も変更しない。
"""
from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any, NoReturn


_GRAPH_NAME = "graph-delegated"
_GRAPH_CLIENT_ID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")
_SECTION_KEYS = {"graphDelegated", "additional"}
_GRAPH_OPTION_KEYS = {"displayName", "scopes", "pool"}
_GRAPH_REGISTRATION_KEYS = {"clientId", "clientSecretFile"}
_ADDITIONAL_OPTION_KEYS = {
    "name", "displayName", "identityProvider", "scopes", "resourceUri",
    "authorizationUrl", "tokenUrl", "refreshUrl", "connectionNames",
    "identityEndpoint", "identityIdProperty", "pool",
}
_ADDITIONAL_REGISTRATION_KEYS = {
    "name", "clientId", "clientSecretFile", "oauthTenantId",
}
_POOL_KEYS = {"count", "capacity"}


def _object(
    value: Any,
    path: str,
    allowed: set[str],
    required: set[str],
    fail: Callable[[str, str], NoReturn],
) -> dict:
    """入力の所有区分と必須項目を検査し、入力値をエラーへ含めない。"""
    if not isinstance(value, dict):
        fail(path, "must be an object")
    if set(value) - allowed:
        fail(path, "contains unsupported or foreign-owned field(s)")
    for key in sorted(required):
        if key not in value:
            fail(path + "." + key, "is required")
    return value


def _additional(
    value: Any,
    path: str,
    allowed: set[str],
    required: set[str],
    fail: Callable[[str, str], NoReturn],
) -> list[dict]:
    """追加接続を検査し、大文字小文字だけが違う重複と標準名の衝突も拒否する。"""
    if not isinstance(value, list):
        fail(path, "must be an array")
    names = set()
    for index, source in enumerate(value):
        item_path = f"{path}[{index}]"
        item = _object(source, item_path, allowed, required, fail)
        name = item["name"]
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            fail(item_path + ".name", "must be a non-empty trimmed string")
        folded = name.casefold()
        if folded == _GRAPH_NAME or folded in names:
            fail(item_path + ".name", "must be unique and distinct from the fixed standard provider")
        names.add(folded)
        if "pool" in item:
            _object(item["pool"], item_path + ".pool", _POOL_KEYS, _POOL_KEYS, fail)
    return value


def compose_credential_providers(
    options: dict,
    registrations: dict,
    entra_tenant_id: str,
    *,
    fail: Callable[[str, str], NoReturn],
) -> list[dict]:
    """分離した公開入力を、既存normalizerが検査する内部接続配列へ変換する。

    引数optionsはconfig.credentialProviders、registrationsは
    params.credentialProvidersの内容。標準Graphの名前・接続方式・宛先は固定し、
    テナントは基盤のEntraテナントから取得する。追加接続はconfig側の順序を保つ。
    URLやID、スコープ、プール範囲などの値の契約は既存normalizerで検査する。
    """
    config_path = "config.credentialProviders"
    params_path = "params.credentialProviders"
    options = _object(options, config_path, _SECTION_KEYS, {"graphDelegated"}, fail)
    registrations = _object(registrations, params_path, _SECTION_KEYS, {"graphDelegated"}, fail)
    graph_options = _object(
        options["graphDelegated"], config_path + ".graphDelegated",
        _GRAPH_OPTION_KEYS, _GRAPH_OPTION_KEYS, fail,
    )
    _object(
        graph_options["pool"], config_path + ".graphDelegated.pool",
        _POOL_KEYS, _POOL_KEYS, fail,
    )
    graph_registration = _object(
        registrations["graphDelegated"], params_path + ".graphDelegated",
        _GRAPH_REGISTRATION_KEYS, _GRAPH_REGISTRATION_KEYS, fail,
    )
    # Entraの標準登録はGUID構文だけを受け付ける。未正規化テンプレートの
    # ゼロGUIDはここで値を変更せず、実入力の検査を既存normalizerへ委ねる。
    client_id = graph_registration["clientId"]
    if not isinstance(client_id, str) or not _GRAPH_CLIENT_ID.fullmatch(client_id):
        fail(params_path + ".graphDelegated.clientId", "must use the GUID syntax of an Entra application client ID")
    definitions = _additional(
        options.get("additional", []), config_path + ".additional",
        _ADDITIONAL_OPTION_KEYS, {"name", "displayName", "identityProvider", "scopes"}, fail,
    )
    additional_registrations = _additional(
        registrations.get("additional", []), params_path + ".additional",
        _ADDITIONAL_REGISTRATION_KEYS, {"name", "clientId", "clientSecretFile"}, fail,
    )
    registrations_by_name = {item["name"]: item for item in additional_registrations}
    definition_names = {item["name"] for item in definitions}
    for index, registration in enumerate(additional_registrations):
        if registration["name"] not in definition_names:
            fail(
                f"{params_path}.additional[{index}].name",
                "must exactly match an additional provider definition in config",
            )

    result = [{
        **copy.deepcopy(graph_options),
        **copy.deepcopy(graph_registration),
        "name": _GRAPH_NAME,
        "identityProvider": "aad",
        "oauthTenantId": entra_tenant_id,
        "resourceUri": "https://graph.microsoft.com",
        "identityEndpoint": "https://graph.microsoft.com/v1.0/me",
        "identityIdProperty": "id",
        "connectionNames": [],
    }]
    for index, definition in enumerate(definitions):
        registration = registrations_by_name.get(definition["name"])
        if registration is None:
            fail(
                f"{config_path}.additional[{index}].name",
                "requires a matching additional provider registration in params",
            )
        provider = copy.deepcopy(definition)
        provider.update(copy.deepcopy(registration))
        provider.setdefault("connectionNames", [])
        result.append(provider)
    return result

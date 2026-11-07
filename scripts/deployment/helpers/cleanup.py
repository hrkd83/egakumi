"""azd downの前後に、モデルの先行削除と削除結果の確認を行う。

Azureへの接続自体は呼出元から渡されたTransportに任せる。ここでは設定と
配備記録を照合して、今回所有する5つのRG・Foundry・Key Vault・APIMだけを
処理する。pre_downは監査ログの保持状態を調べ、モデルを親リソースより
先に削除し、設定済みのCredential Manager providerを回収した後、別RGのsubnetを
使用するAPIMも先に削除する。PortalはCA→CAEの順で削除し、ACA subnetの
IP参照とサービス関連付けが解除されたことを確認してからazdへ引き渡す。post_downは
RGが消えたことを確認してから、対象を限定して
サービスの論理削除データを完全削除する。
API Centerの同名予約は読取結果として別記し、完全削除の対象には加えない。
保持ロックやpurge保護を回避しない。既存の保護対象は理由を返して残す。
この処理はAzureの削除・変更を伴う。削除要求の受付だけでは課金停止や
クォータ返却を証明できないため、確認できた範囲を結果として分けて返す。
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from .configuration import API_NAME, load_configuration
from .grpc_lifecycle import apim_name_matches
from .credential_pools import resource_names
from .portal_resources import resource_names as portal_resource_names


class CleanupError(RuntimeError):
    """対象IDと固定の理由コードを持つ撤去例外。HTTP本文や秘密値は含めない。"""

    def __init__(self, code: str, message: str, *, resource_id: str | None = None):
        """機械判定用コードと人向け理由を、対象リソースIDに結び付ける。"""
        super().__init__(message)
        self.code = code
        self.resource_id = resource_id


class Transport(Protocol):
    """呼出元が用意するARM通信の最小インターフェース。"""
    def request(self, method: str, path: str, *, body: dict | None = None,
                headers: dict | None = None) -> Mapping[str, Any]:
        """ARM要求の契約を示す。実際の認証・通信方法は呼出元が実装する。"""
        ...


API = {
    "groups": "2024-03-01",
    "storage": "2023-05-01",
    "diagnostics": "2021-05-01-preview",
    "keyVault": "2023-07-01",
    "foundry": "2025-06-01",
    "apim": "2024-05-01",
    "apiCenter": "2024-06-01-preview",
    "resources": "2021-04-01",
    "deployments": "2022-09-01",
    "compute": "2024-11-01",
    "network": "2024-05-01",
    "containerApps": "2025-01-01",
}
SERVICE_TYPES = {
    "keyVault": ("Microsoft.KeyVault", "vaults"),
    "foundry": ("Microsoft.CognitiveServices", "accounts"),
    "apim": ("Microsoft.ApiManagement", "service"),
}


def _error(code: str, message: str, resource_id: str | None = None) -> None:
    """理由コード・説明・対象IDを持つ撤去エラーを発生させる。"""
    raise CleanupError(code, message, resource_id=resource_id)


def _same(left: Any, right: Any) -> bool:
    """Resource IDや公開IDを、大文字小文字の差を除いて照合する。"""
    return isinstance(left, str) and isinstance(right, str) and left.strip("/").lower() == right.strip("/").lower()


def _request(transport: Transport, method: str, resource_id: str, api: str,
             *, headers: dict | None = None, path: str | None = None) -> dict:
    """指定API版でARM要求し、想定外の状態や結果を撤去エラーへ変換する。"""
    try:
        response = transport.request(method, path or f"{resource_id}?api-version={api}",
                                     body=None, headers=headers)
    except Exception:
        raise CleanupError("transport-failed", "Azureへの撤去操作に失敗しました。再実行前に接続と権限を確認してください。",
                           resource_id=resource_id) from None
    if not isinstance(response, Mapping) or isinstance(response.get("statusCode"), bool) or not isinstance(response.get("statusCode"), int):
        _error("invalid-response", "撤去操作のHTTP結果を確認できません。", resource_id)
    status = response["statusCode"]
    if status not in (200, 201, 202, 204, 404):
        _error("azure-request-failed", f"撤去操作がHTTP {status}で失敗しました。削除済みとは判定していません。", resource_id)
    body = response.get("body")
    if body is not None and not isinstance(body, Mapping):
        _error("invalid-response", "撤去操作の応答形式を確認できません。", resource_id)
    return {"statusCode": status, "body": dict(body or {}), "headers": response.get("headers") or {}}


def _expect_read(response: dict, resource_id: str) -> dict | None:
    """読取結果から存在する資源を返す。404は既に存在しない状態として扱う。"""
    if response["statusCode"] == 404:
        return None
    if response["statusCode"] != 200:
        _error("read-incomplete", "対象資源の現在状態を取得できません。", resource_id)
    return response["body"]


def _parts(resource_id: Any) -> list[str]:
    """ARM IDを分解し、対象サブスクとRGを照合できる形にする。"""
    if not isinstance(resource_id, str) or not resource_id.startswith("/") or any(char in resource_id for char in ("?", "#", "\\")):
        _error("invalid-state", "現在の配備記録に不正なResource IDがあります。")
    parts = resource_id.strip("/").split("/")
    if any(not part or part in (".", "..") for part in parts):
        _error("invalid-state", "現在の配備記録に不正なResource IDがあります。")
    return parts


def _rg_id(configuration: dict, kind: str) -> str:
    """設定された5RGのうち、指定した用途の正確なResource IDを組み立てる。"""
    return f"/subscriptions/{configuration['subscriptionId']}/resourceGroups/{configuration['resourceGroupNames'][kind]}"


def _validate_state(configuration: dict, state: Mapping[str, Any]) -> dict:
    """配備記録を現在の設定と照合し、別環境のIDで削除を始めないようにする。"""
    if not isinstance(state, Mapping):
        _error("invalid-state", "現在の環境の配備記録が必要です。")
    for key in ("subscriptionId", "azureTenantId", "resourceStem"):
        if not _same(state.get(key), configuration[key]):
            _error("state-environment-mismatch", "配備記録が対象の環境・テナント・サブスクリプションと一致しません。")
    if state.get("resourceGroupNames") != configuration["resourceGroupNames"]:
        _error("state-environment-mismatch", "配備記録の5つのResource Groupが対象環境と一致しません。")

    storage = state.get("storage") or {}
    if not isinstance(storage, Mapping):
        _error("invalid-state", "監査Storageの配備出力が不正です。")
    accounts: list[str] = []
    for key in ("collectJapan", "collectFoundry"):
        account = storage.get(key)
        if account:
            account_id = account.get("id") if isinstance(account, Mapping) else None
            parts = _parts(account_id)
            expected = _rg_id(configuration, "monitor") + "/providers/Microsoft.Storage/storageAccounts/"
            if len(parts) != 8 or not account_id.lower().startswith(expected.lower()) or not re.fullmatch(r"[a-z0-9]{3,24}", parts[-1]):
                _error("invalid-state", "監査Storageの配置先が対象のmonitor RGと一致しません。")
            accounts.append(account_id)
    policy_ids = storage.get("immutabilityPolicyIds") or []
    if not isinstance(policy_ids, list) or any(not isinstance(item, str) for item in policy_ids) or len(set(policy_ids)) != len(policy_ids):
        _error("invalid-state", "監査保持ポリシーの配備出力が不正です。")
    for policy_id in policy_ids:
        parts = _parts(policy_id)
        if len(parts) != 14 or not any(_same("/" + "/".join(parts[:8]), account) for account in accounts):
            _error("invalid-state", "監査保持ポリシーが対象の監査Storageに属していません。")
        if [part.lower() for part in parts[8:]] != ["blobservices", "default", "containers", parts[11].lower(), "immutabilitypolicies", "default"] or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", parts[11]):
            _error("invalid-state", "監査保持ポリシーのResource IDが不正です。")

    service_ids = state.get("serviceResourceIds") or {}
    if not isinstance(service_ids, Mapping) or set(service_ids) - SERVICE_TYPES.keys():
        _error("invalid-state", "完全削除の対象は現在のKV・Foundry・APIMだけに限定してください。")
    for key, resource_id in service_ids.items():
        parts = _parts(resource_id)
        provider, resource_type = SERVICE_TYPES[key]
        expected_prefix = _rg_id(configuration, "svc") + f"/providers/{provider}/{resource_type}/"
        if len(parts) != 8 or not resource_id.lower().startswith(expected_prefix.lower()) or not re.fullmatch(r"[A-Za-z0-9-]+", parts[-1]):
            _error("invalid-state", "完全削除の対象が現在のsvc RGの資源と一致しません。")
        if key == "apim" and not apim_name_matches(configuration, parts[-1]):
            _error("apim-owner-mismatch", "APIMの削除先が今回の命名・配置先と一致しません。", resource_id)

    locations = state.get("serviceLocations") or {}
    for key in service_ids:
        expected_location = configuration["foundryLocation"] if key == "foundry" else configuration["location"]
        if locations.get(key) != expected_location or not re.fullmatch(r"[a-z][a-z0-9]+", locations[key]):
            _error("invalid-state", "サービスの実配置region codeが配備記録と環境設定で一致しません。")
    return {"storage": storage, "policyIds": policy_ids, "serviceIds": service_ids,
            "locations": locations}


def _wait_absent(transport: Transport, resource_id: str, api: str, *,
                 max_checks: int, wait_seconds: float, sleep: Callable[[float], None]) -> None:
    """削除要求後に読取を続け、404になるまで対象が消えたとは報告しない。"""
    for attempt in range(max_checks):
        response = _request(transport, "GET", resource_id, api)
        if response["statusCode"] == 404:
            return
        _expect_read(response, resource_id)
        if attempt + 1 < max_checks:
            sleep(wait_seconds)
    _error("cleanup-pending", "削除要求後も資源が残っています。撤去完了とは判定していません。同じ対象の確認を再実行してください。", resource_id)


def _options(max_checks: int, wait_seconds: float) -> None:
    """削除確認の回数と待機時間を検査し、有限の確認処理にする。"""
    if (isinstance(max_checks, bool) or not isinstance(max_checks, int) or not 1 <= max_checks <= 300
        or isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float))
        or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 60):
        _error("invalid-options", "撤去確認の回数・間隔が不正です。")


def _model_page_path(value: Any, collection_id: str) -> str:
    """今回のFoundryだけを指す一覧の継続URLか確認し、別ホスト・別範囲への追跡を拒否する。"""
    try:
        if not isinstance(value, str) or not value:
            raise ValueError()
        parsed = urlsplit(value)
        if (parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.scheme not in ("", "https") or parsed.netloc and parsed.hostname != "management.azure.com"
            or not _same(parsed.path, collection_id)
            or parse_qs(parsed.query).get("api-version") != [API["foundry"]]):
            raise ValueError()
        return parsed.path + "?" + parsed.query
    except (ValueError, AttributeError):
        _error("model-list-unverified", "対象Foundryのモデル一覧の継続先を検証できません。", collection_id)


def _model_inventory(transport: Transport, account_id: str) -> list[str]:
    """Foundryの全ページからモデル配備を読み、対象外IDや循環する継続URLを拒否する。"""
    collection_id = account_id + "/deployments"
    next_page = collection_id + "?api-version=" + API["foundry"]
    visited, deployments = set(), []
    while next_page:
        path = _model_page_path(next_page, collection_id)
        if path in visited:
            _error("model-list-unverified", "モデル一覧の継続先が循環しています。", collection_id)
        visited.add(path)
        page = _expect_read(_request(transport, "GET", collection_id, API["foundry"], path=path), collection_id)
        if page is None or not isinstance(page.get("value"), list):
            _error("model-list-unverified", "対象Foundryのモデル一覧を取得できません。", collection_id)
        for model in page["value"]:
            resource_id = model.get("id") if isinstance(model, Mapping) else None
            parts = _parts(resource_id)
            if (len(parts) != 10 or not _same("/" + "/".join(parts[:8]), account_id)
                or parts[8].lower() != "deployments" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", parts[9])
                or not _same(model.get("name"), parts[9])
                or str(model.get("type", "")).lower() != "microsoft.cognitiveservices/accounts/deployments"
                or resource_id.lower() in {value.lower() for value in deployments}):
                _error("model-owner-mismatch", "モデル一覧に対象Foundry以外または不明な資源が含まれるため削除しません。", collection_id)
            deployments.append(resource_id)
        next_page = page.get("nextLink")
        if next_page is not None and not isinstance(next_page, str):
            _error("model-list-unverified", "モデル一覧の継続先を確認できません。", collection_id)
    return deployments


def _delete_models_first(configuration: dict, current: dict, transport: Transport, *,
                         max_checks: int, wait_seconds: float, sleep: Callable[[float], None]) -> dict:
    """親のFoundryが存在する間に、所有モデルの配備を先に削除して確認する。

    モデルの割当を残したまま親だけを消すと利用枠の確認が難しくなる。
    配備削除の確認とクォータ返却の確認は同じではないため、結果で明確に分ける。
    """
    account_id = current["serviceIds"].get("foundry")
    result = {"status": "not-deployed", "modelDeletionVerified": False,
              "quotaReleaseVerified": False, "quotaStatus": "not-read", "deployments": []}
    if account_id is None:
        return result
    expected = _rg_id(configuration, "svc") + "/providers/Microsoft.CognitiveServices/accounts/aif-" + configuration["resourceStem"]
    if not _same(account_id, expected):
        _error("model-owner-mismatch", "モデル削除先が今回のFoundryの命名・配置先と一致しません。", account_id)
    account = _expect_read(_request(transport, "GET", account_id, API["foundry"]), account_id)
    if account is None:
        result.update(status="account-already-absent", accountId=account_id)
        # 親が既にない場合、モデルを先に消したか後から証明できない。論理削除の処理は
        # down後に行い、利用枠が戻ったかは別途capacityの読取で確認する。
        return result
    tags = account.get("tags") or {}
    if (not _same(account.get("id"), account_id)
        or str(account.get("type", "")).lower() != "microsoft.cognitiveservices/accounts"
        or str(account.get("location", "")).lower() != configuration["foundryLocation"].lower()
        or account.get("kind") != "AIServices" or not isinstance(tags, Mapping)
        or tags.get("product") != configuration["productName"] or tags.get("environment") != configuration["environmentName"]):
        _error("model-owner-mismatch", "対象FoundryのID・種類・region・環境タグを確認できないためモデルを削除しません。", account_id)
    # 削除開始前に一覧の全ページを検査し、途中で対象外IDが見つかる部分削除を避ける。
    deployments = _model_inventory(transport, account_id)
    for resource_id in deployments:
        _request(transport, "DELETE", resource_id, API["foundry"])
        # 202は非同期削除の受付。要求成功だけで削除済みにせず、同じモデルのGETが
        # 404になるまで確認する。
        _wait_absent(transport, resource_id, API["foundry"], max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
        result["deployments"].append({"resourceId": resource_id, "status": "absent", "httpStatus": 404})
    for attempt in range(max_checks):
        if not _model_inventory(transport, account_id):
            result.update(status="models-absent", accountId=account_id, modelDeletionVerified=True)
            return result
        if attempt + 1 < max_checks:
            sleep(wait_seconds)
    _error("model-cleanup-pending", "Foundryのモデル一覧に資源が残っています。本体・RGの削除へ進まず同じ対象の撤去を再確認してください。", account_id)


def _credential_provider_ids(configuration: dict, apim_id: str) -> list[str]:
    """設定した provider 名だけを、所有照合済み APIM の完全な子 ID にする。

    他 APIM や未設定 provider を探索して削除しない。下流 OAuth の grant や Entra
    アプリはこの処理の対象外であり、provider の削除とは別に管理する。
    """
    from .credential_pools import physical_providers
    providers = physical_providers(configuration)
    if not isinstance(providers, list):
        _error("credential-provider-config-invalid", "撤去するOAuth providerの設定を確認できません。", apim_id)
    names = []
    for provider in providers:
        name = provider.get("name") if isinstance(provider, Mapping) else None
        if not isinstance(name, str) or not API_NAME.fullmatch(name) or name.casefold() in names:
            _error("credential-provider-config-invalid", "撤去するOAuth provider名が不正または重複しています。", apim_id)
        names.append(name.casefold())
    return [apim_id + "/authorizationProviders/" + provider["name"] for provider in providers]


def _credential_provider_identity(provider_id: str, provider: Mapping) -> dict:
    """所有対象を照合し、再確認に使う公開識別項目だけを取り出す。

    clientSecret・token・任意の応答はコピーせず、既知の項目だけをメモリー内で
    比較する。この比較用辞書を削除結果やエラーメッセージへ出力しない。
    """
    if (not isinstance(provider.get("id"), str) or provider["id"].casefold() != provider_id.casefold()
        or not isinstance(provider.get("name"), str)
        or provider["name"].casefold() != provider_id.rsplit("/", 1)[-1].casefold()
        or str(provider.get("type", "")).casefold() != "microsoft.apimanagement/service/authorizationproviders"):
        _error("credential-provider-owner-mismatch", "OAuth providerのID・種類・名前が対象APIMと一致しません。", provider_id)
    properties = provider.get("properties", {})
    if not isinstance(properties, Mapping):
        _error("credential-provider-metadata-invalid", "OAuth providerの公開識別情報を確認できません。", provider_id)
    result = {}
    for path in ("identityProvider", "oauth2.redirectUrl", "oauth2.grantTypes.authorizationCode.clientId",
                 "oauth2.grantTypes.authorizationCode.tenantId", "oauth2.grantTypes.authorizationCode.resourceUri",
                 "oauth2.grantTypes.authorizationCode.scopes"):
        current: Any = properties
        for part in path.split("."):
            if not isinstance(current, Mapping):
                _error("credential-provider-metadata-invalid", "OAuth providerの公開識別情報の構造が不正です。", provider_id)
            if part not in current:
                break
            current = current[part]
        else:
            if current is not None and not isinstance(current, str):
                _error("credential-provider-metadata-invalid", "OAuth providerの公開識別情報の形式が不正です。", provider_id)
            result[path] = current
    return result


def _credential_provider_etag(response: dict, provider_id: str) -> str | None:
    """有効なETagを優先し、不正・重複ヘッダーと未提供を区別する。"""
    headers = response["headers"]
    if not isinstance(headers, Mapping):
        _error("credential-provider-etag-missing", "OAuth providerのETagヘッダー形式を確認できません。", provider_id)
    matches = [value for key, value in headers.items() if str(key).casefold() == "etag"]
    if not matches:
        return None
    etag = matches[0]
    if (len(matches) != 1 or not isinstance(etag, str)
        or not re.fullmatch(r'(?:W/)?"[^"\r\n\x00-\x1f\x7f]+"', etag)
        or etag in ('"*"', 'W/"*"') or len(etag) > 8192):
        _error("credential-provider-etag-missing", "OAuth providerのETagが不正または重複しているため削除しません。", provider_id)
    return etag


def _credential_child_identity(resource_id: str, resource: Mapping, kind: str) -> dict:
    """子のID・種類・名前と公開識別項目を照合し、秘密を含まない比較値だけ残す。"""
    expected_type = "microsoft.apimanagement/service/authorizationproviders/authorizations"
    if kind == "accessPolicies":
        expected_type += "/accesspolicies"
    # APIM の access policy は、実際の一覧・個別 GET の両方で種類名の末尾に
    # 「/」を一つ付けることがあります。既知のこの表記だけを受け入れ、ID・名前・
    # 親の所有照合は省略しません。任意の末尾削除や別の種類への置換は行いません。
    accepted_types = {expected_type}
    if kind == "accessPolicies":
        accepted_types.add(expected_type + "/")
    if (not isinstance(resource.get("id"), str) or resource["id"].casefold() != resource_id.casefold()
        or not isinstance(resource.get("name"), str)
        or resource["name"].casefold() != resource_id.rsplit("/", 1)[-1].casefold()
        or str(resource.get("type", "")).casefold() not in accepted_types):
        _error("credential-child-owner-mismatch", "OAuth接続または接続権限のID・種類・名前が所有対象と一致しません。", resource_id)
    properties = resource.get("properties", {})
    if not isinstance(properties, Mapping):
        _error("credential-child-metadata-invalid", "OAuth接続または接続権限の公開情報を確認できません。", resource_id)
    keys = ("objectId", "tenantId") if kind == "accessPolicies" else ("authorizationType", "oauth2grantType")
    identity = {}
    for key in keys:
        if key in properties:
            if properties[key] is not None and not isinstance(properties[key], str):
                _error("credential-child-metadata-invalid", "OAuth接続または接続権限の公開情報の形式が不正です。", resource_id)
            identity[key] = properties[key]
    return identity


def _credential_collection_page(value: str, collection_id: str, *, unfiltered: bool = False) -> str:
    """継続URLを同じARMホスト・同じ子一覧・同じAPI版に限定する。"""
    try:
        if (not isinstance(value, str) or not value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
            raise ValueError()
        parsed = urlsplit(value)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if (parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.scheme not in ("", "https")
            or parsed.netloc and parsed.hostname != "management.azure.com"
            or parsed.scheme and not parsed.netloc
            or parsed.path.casefold() != collection_id.casefold()
            or query.get("api-version") != [API["apim"]]
            or unfiltered and (set(query) - {"api-version", "$skip", "$top", "$skiptoken", "$skipToken"}
                               or any(len(values) != 1 for values in query.values()))):
            # 一覧からの消失を証明するとき、継続先へfilter等が追加されて対象が
            # 除外されないようにする。ページ送り用の値だけを引き継ぐ。
            raise ValueError()
        return parsed.path + "?" + parsed.query
    except (ValueError, AttributeError):
        _error("credential-child-list-unverified", "OAuth接続または接続権限の一覧の継続先を検証できません。", collection_id)


def _credential_child_inventory(parent_id: str, kind: str, transport: Transport) -> list[str]:
    """所有APIM/provider配下の一覧を全ページ読み、直下の正確な子IDだけ返す。

    authorizationProvidersの一覧にも同じホスト・API版・親IDの制約を使う。
    一覧で別provider・別RGのIDを受け取っても、そのIDへ照会や削除を広げない。
    初期一覧だけでなく削除後にも使い、新しい子が残った状態で親を消さない。
    親を200で所有確認した後の一覧404は、空配列へ読み替えず検証不能として止める。
    """
    collection_id = parent_id + "/" + kind
    page_path = collection_id + "?api-version=" + API["apim"]
    visited, seen, result = set(), set(), []
    while page_path:
        path = _credential_collection_page(page_path, collection_id, unfiltered=kind == "authorizationProviders")
        if path in visited or len(visited) >= 1000:
            _error("credential-child-list-unverified", "OAuth接続または接続権限の一覧が循環または過大です。", collection_id)
        visited.add(path)
        page = _expect_read(_request(transport, "GET", collection_id, API["apim"], path=path), collection_id)
        if page is None or not isinstance(page.get("value"), list):
            _error("credential-child-list-unverified", "OAuth接続または接続権限の一覧を確認できません。", collection_id)
        for child in page["value"]:
            child_id = child.get("id") if isinstance(child, Mapping) else None
            prefix = collection_id + "/"
            if (not isinstance(child_id, str) or not child_id.casefold().startswith(prefix.casefold())
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", child_id[len(prefix):])
                or child_id.casefold() in seen):
                _error("credential-child-owner-mismatch", "OAuth接続または接続権限の一覧に対象外・重複の資源が含まれています。", collection_id)
            if kind == "authorizationProviders":
                _credential_provider_identity(child_id, child)
            else:
                _credential_child_identity(child_id, child, kind)
            seen.add(child_id.casefold())
            result.append(child_id)
        page_path = page.get("nextLink")
        # Authorization 一覧の公式例は、最後のページに nextLink:"" を返します。
        # 未指定・null・空文字だけを終端にし、空白や非文字列を空扱いしません。
        if page_path == "":
            page_path = None
        if page_path is not None and not isinstance(page_path, str):
            _error("credential-child-list-unverified", "OAuth接続または接続権限の一覧の継続先が不正です。", collection_id)
    return result


def _credential_child_snapshot(resource_id: str, kind: str, transport: Transport) -> dict:
    """一覧に載る子を個別GETし、最初の削除前に所有情報とETagを確認する。"""
    response = _request(transport, "GET", resource_id, API["apim"])
    resource = _expect_read(response, resource_id)
    if resource is None:
        _error("credential-child-read-unverified", "一覧に載るOAuth接続または接続権限を個別に確認できません。", resource_id)
    return {"id": resource_id, "kind": kind,
            "identity": _credential_child_identity(resource_id, resource, kind),
            "etag": _credential_provider_etag(response, resource_id)}


def _delete_credential_entity(entity: dict, transport: Transport, *, refresh: bool,
                              max_checks: int, wait_seconds: float,
                              sleep: Callable[[float], None]) -> dict:
    """所有確認済みの一資源だけをIf-Match付きで削除し、実際の確認結果を返す。

    接続・権限はGET404が必須。providerの同期削除で個別GETだけが残る場合は、
    所有不変と全ページ一覧での消失を確認し、親の削除確認を要する中間結果にする。

    子を削除すると親のETagも変わり得るため、子のある親は削除直前に再取得する。
    ETagを返さない実サービスでも、公開識別項目を再照合した後だけ、公式APIが
    認めるIf-Match:*を使う。同時更新を版で保護できない点は結果へ明記する。
    """
    resource_id, etag = entity["id"], entity["etag"]
    concurrency = {}
    if refresh or etag is None:
        response = _request(transport, "GET", resource_id, API["apim"])
        resource = _expect_read(response, resource_id)
        if resource is None:
            return {"resourceId": resource_id, "status": "absent", "httpStatus": 404,
                    "deletionVerified": True, "deleteRequested": False}
        identity = (_credential_provider_identity(resource_id, resource) if entity["kind"] == "provider"
                    else _credential_child_identity(resource_id, resource, entity["kind"]))
        if any(key not in identity or identity[key] != value for key, value in entity["identity"].items()):
            _error("credential-provider-metadata-changed" if entity["kind"] == "provider" else "credential-child-metadata-changed",
                   "OAuth資源の公開識別情報が再確認時に変わったため削除しません。", resource_id)
        etag = _credential_provider_etag(response, resource_id)
        concurrency = {"identityRevalidated": True, "versionPreconditionApplied": etag is not None,
                       "ifMatchMode": "etag-after-refresh" if etag is not None else "documented-wildcard-missing-etag"}
    response = _request(transport, "DELETE", resource_id, API["apim"], headers={"If-Match": etag if etag is not None else "*"})
    if response["statusCode"] not in (200, 202, 204, 404):
        _error("credential-provider-delete-unverified", "OAuth資源の削除要求を確認できません。", resource_id)
    if entity["kind"] == "provider":
        # providerの公式DELETEは200/204が同期成功。202は完了とは扱わず、
        # 接続・権限と同じ404確認や親APIM削除へ勝手に進めない。
        if response["statusCode"] == 202:
            _error("credential-provider-delete-unverified", "OAuth providerの同期削除を確認できないためAPIMを削除しません。", resource_id)
        if response["statusCode"] in (200, 204):
            read = _request(transport, "GET", resource_id, API["apim"])
            resource = _expect_read(read, resource_id)
            if resource is not None:
                # 実サービスではDELETE成功後に一覧から消えても、個別GETだけが
                # 元のproviderを200で返すことがある。キャッシュとは断定しない。
                # ID・種類・名前と公開設定を再照合し、別資源への変更なら停止する。
                identity = _credential_provider_identity(resource_id, resource)
                if any(key not in identity or identity[key] != value for key, value in entity["identity"].items()):
                    _error("credential-provider-metadata-changed", "削除後のOAuth providerの公開識別情報が変わったためAPIMを削除しません。", resource_id)
                apim_id = resource_id.rsplit("/", 2)[0]
                listed = _credential_child_inventory(apim_id, "authorizationProviders", transport)
                if resource_id.casefold() in {item.casefold() for item in listed}:
                    _error("cleanup-pending", "同期削除後もOAuth provider一覧に対象が残るためAPIMを削除しません。", resource_id)
                # これは個別GET404の証明ではない。所有APIMの削除へ進むための
                # 中間記録に留め、親のGET404確認後にだけ親の削除確認を追記する。
                return {"resourceId": resource_id, "status": "list-absent-individual-get-unresolved",
                        "httpStatus": 200, "individualGetStatus": 200,
                        "deleteHttpStatus": response["statusCode"], "deletionVerified": False,
                        "listAbsenceVerified": True, "identityRevalidatedAfterDelete": True,
                        "parentDeletionRequired": True, "deleteRequested": True, **concurrency}
            return {"resourceId": resource_id, "status": "absent", "httpStatus": 404,
                    "deletionVerified": True, "deleteRequested": True, **concurrency}
    # 本人接続とaccess policyは、一覧が空でも必ず個別GET404を確認する。
    _wait_absent(transport, resource_id, API["apim"], max_checks=max_checks,
                 wait_seconds=wait_seconds, sleep=sleep)
    return {"resourceId": resource_id, "status": "absent", "httpStatus": 404,
            "deletionVerified": True, "deleteRequested": True, **concurrency}


def _delete_credential_providers(provider_ids: list[str], transport: Transport, *,
                                 max_checks: int, wait_seconds: float,
                                 sleep: Callable[[float], None]) -> list[dict]:
    """接続権限→本人接続→providerの順で削除し、各確認結果を分けて残す。

    接続権限・本人接続はGET404を確認してから次へ進む。providerは同期削除と一覧不在を
    確認できても個別GET200が残れば未解消と記録し、親APIMの削除へ引き渡す。

    全providerと全ての子一覧を変更前に照合する。削除の途中で不明な応答・競合・
    残存があれば親APIMの削除へ進まない。一覧や比較に秘密値を残さず、結果には
    対象ID・不在確認・同時更新保護の有無だけを記録する。
    公開APIで消失を確認できても、Microsoft内部credential-store accountの解放や
    同名再構築までは保証しない。下流OAuth同意やEntraアプリ自体は操作しない。
    https://learn.microsoft.com/rest/api/apimanagement/authorization-access-policy/delete
    https://learn.microsoft.com/rest/api/apimanagement/authorization/delete
    https://learn.microsoft.com/rest/api/apimanagement/authorization-provider/delete
    """
    pending, results = [], []
    for provider_id in provider_ids:
        response = _request(transport, "GET", provider_id, API["apim"])
        provider = _expect_read(response, provider_id)
        if provider is None:
            results.append({"resourceId": provider_id, "status": "absent", "httpStatus": 404,
                            "deletionVerified": True, "deleteRequested": False})
            continue
        entry = {"id": provider_id, "kind": "provider",
                 "identity": _credential_provider_identity(provider_id, provider),
                 "etag": _credential_provider_etag(response, provider_id), "authorizations": []}
        for authorization_id in _credential_child_inventory(provider_id, "authorizations", transport):
            authorization = _credential_child_snapshot(authorization_id, "authorizations", transport)
            authorization["accessPolicies"] = [_credential_child_snapshot(policy_id, "accessPolicies", transport)
                for policy_id in _credential_child_inventory(authorization_id, "accessPolicies", transport)]
            entry["authorizations"].append(authorization)
        pending.append(entry)
    for provider in pending:
        authorization_results = []
        for authorization in provider["authorizations"]:
            policy_results = [_delete_credential_entity(policy, transport, refresh=False,
                max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
                for policy in authorization["accessPolicies"]]
            if _credential_child_inventory(authorization["id"], "accessPolicies", transport):
                _error("credential-children-remain", "OAuth接続権限が残るため本人接続・provider・APIMを削除しません。", authorization["id"])
            result = _delete_credential_entity(authorization, transport, refresh=bool(policy_results),
                max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
            result.update(accessPolicies=policy_results, childrenDeletionVerified=True)
            authorization_results.append(result)
        if _credential_child_inventory(provider["id"], "authorizations", transport):
            _error("credential-children-remain", "OAuth本人接続が残るためprovider・APIMを削除しません。", provider["id"])
        result = _delete_credential_entity(provider, transport, refresh=bool(authorization_results),
            max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
        result.update(authorizations=authorization_results, childrenDeletionVerified=True,
                      internalAccountReleaseVerified=False)
        results.append(result)
    return results


def _resource_group_inventory(group_id: str, transport: Transport) -> set[str] | None:
    """azdと同じRG資源一覧を全ページ読み、別RG・型違い・絞り込みを拒否する。

    個別GET404の直後でも、ARMのRG一覧に古い資源が残る場合がある。azdは
    この一覧を使ってpurge対象の詳細を読むため、先行削除したAPIMが消えるまで確認する。
    RG自身の404を別に確認できた場合だけNoneを返し、一覧404を空配列とは呼ばない。
    """
    collection = group_id + "/resources"
    path = collection + "?api-version=" + API["resources"]
    visited, found = set(), set()
    while path:
        try:
            parsed = urlsplit(path)
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            if (not isinstance(path, str) or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.scheme not in ("", "https") or parsed.scheme and not parsed.netloc
                or parsed.netloc and parsed.hostname != "management.azure.com"
                or parsed.path.casefold() != collection.casefold()
                or query.get("api-version") != [API["resources"]]
                or set(query) - {"api-version", "$skiptoken", "$skip", "$top"}
                or any(len(values) != 1 for values in query.values())):
                raise ValueError()
            request_path = parsed.path + "?" + parsed.query
        except (TypeError, ValueError, AttributeError):
            _error("resource-inventory-unverified", "RG資源一覧の継続先が対象RG・ARMホスト・API版と一致しません。", group_id)
        if request_path in visited or len(visited) >= 1000:
            _error("resource-inventory-unverified", "RG資源一覧が循環または過大です。", group_id)
        visited.add(request_path)
        response = _request(transport, "GET", collection, API["resources"], path=request_path)
        if response["statusCode"] == 404:
            if _request(transport, "GET", group_id, API["groups"])["statusCode"] == 404:
                return None
            _error("resource-inventory-unverified", "RGが存在するのに資源一覧を確認できません。", group_id)
        page = _expect_read(response, collection)
        if not isinstance(page.get("value"), list):
            _error("resource-inventory-unverified", "RG資源一覧の形式を確認できません。", group_id)
        for item in page["value"]:
            resource_id = item.get("id") if isinstance(item, Mapping) else None
            if (not isinstance(resource_id, str) or not resource_id.casefold().startswith((group_id + "/providers/").casefold())
                or any(c in resource_id for c in ("%", "?", "#", "\\"))
                or any(ord(c) < 32 or ord(c) == 127 for c in resource_id)):
                _error("resource-inventory-owner-mismatch", "RG資源一覧に対象外または不正なIDがあります。", group_id)
            parts = _parts(resource_id)
            last_provider = max(i for i, part in enumerate(parts) if part.casefold() == "providers")
            tail = parts[last_provider + 1:]
            expected_type = "/".join([tail[0], *tail[1::2]]) if len(tail) >= 3 and len(tail) % 2 == 1 else ""
            if (not expected_type or str(item.get("type", "")).casefold() != expected_type.casefold()
                or resource_id.casefold() in found):
                _error("resource-inventory-owner-mismatch", "RG資源一覧の種類・IDまたは重複を確認できません。", group_id)
            found.add(resource_id.casefold())
        path = page.get("nextLink")
        if path == "":
            path = None
        if path is not None and (not isinstance(path, str) or not path):
            _error("resource-inventory-unverified", "RG資源一覧の継続先の形式が不正です。", group_id)
    return found


def _wait_resource_inventory_absent(configuration: dict, resource_id: str, transport: Transport, *,
                                    max_checks: int, wait_seconds: float,
                                    sleep: Callable[[float], None]) -> dict:
    """個別404を偽装せず、同じsvc RGの一覧からもexact APIM IDが消えるまで待つ。"""
    group_id = _rg_id(configuration, "svc")
    for attempt in range(max_checks):
        inventory = _resource_group_inventory(group_id, transport)
        if inventory is None or resource_id.casefold() not in inventory:
            return {"resourceGroupId": group_id, "apiVersion": API["resources"],
                    "targetAbsenceVerified": True, "listReadVerified": inventory is not None,
                    "resourceGroupAbsentVerified": inventory is None}
        if attempt + 1 < max_checks:
            sleep(wait_seconds)
    _error("resource-inventory-pending", "個別GET404後もRG一覧にAPIMが残るためazdへ引き渡しません。", resource_id)


def _management_resource_owner(resource_id: str, resource: Mapping, expected_type: str,
                               configuration: dict) -> None:
    """管理VM/NICの完全ID・名前・種類・region・所有タグを、削除の直前にも確認する。"""
    tags = resource.get("tags")
    if (not isinstance(resource.get("id"), str) or resource["id"].casefold() != resource_id.casefold()
        or str(resource.get("name", "")).casefold() != resource_id.rsplit("/", 1)[-1].casefold()
        or str(resource.get("type", "")).casefold() != expected_type.casefold()
        or str(resource.get("location", "")).replace(" ", "").casefold() != configuration["location"].casefold()
        or not isinstance(tags, Mapping) or tags.get("product") != configuration["productName"]
        or tags.get("environment") != configuration["environmentName"]):
        _error("management-owner-mismatch", "管理VMまたはNICの所有ID・種類・region・タグが一致しません。", resource_id)


def _management_snapshot(configuration: dict, targets: dict, transport: Transport) -> dict:
    """VMと専用NICの実参照を照合する。他のNIC・disk・subnetへ削除を広げない。"""
    vm = _expect_read(_request(transport, "GET", targets["vmId"], API["compute"]), targets["vmId"])
    nic = _expect_read(_request(transport, "GET", targets["nicId"], API["network"]), targets["nicId"])
    record = targets["record"]
    if not record and (vm is not None or nic is not None):
        _error("management-state-missing", "管理VM/NICが存在しますが、今回の配備出力を確認できないため削除しません。", targets["vmId"])
    if vm is not None:
        _management_resource_owner(targets["vmId"], vm, "Microsoft.Compute/virtualMachines", configuration)
        properties = vm.get("properties")
        identity = vm.get("identity")
        if (not isinstance(properties, Mapping) or not isinstance(identity, Mapping)
            or not isinstance(properties.get("networkProfile"), Mapping)
            or not isinstance(properties.get("storageProfile"), Mapping)
            or not isinstance(properties["storageProfile"].get("osDisk"), Mapping)):
            _error("management-binding-mismatch", "管理VMのNIC・disk・IDの公開情報を確認できません。", targets["vmId"])
        interfaces = properties["networkProfile"].get("networkInterfaces")
        disk = properties["storageProfile"]["osDisk"]
        # Azure DNSの証明書更新では、システムIDと事前作成したDNS用IDを併用します。
        # Bicepと同じ条件で割り当て先を照合し、共有のDNS用IDは削除対象に含めません。
        certificate = configuration["certificate"]
        dns_identity = (certificate.get("azureDnsManagedIdentityResourceId")
                        if certificate["mode"] == "dnsPlugin" and certificate.get("plugin") == "Azure" else None)
        expected_user_ids = {dns_identity.casefold()} if dns_identity else set()
        expected_types = {"systemassigned", "userassigned"} if dns_identity else {"systemassigned"}
        identity_type = identity.get("type")
        identity_types = [part.strip().casefold() for part in identity_type.split(",")] if isinstance(identity_type, str) else []
        user_ids = identity.get("userAssignedIdentities")
        if user_ids is None:
            user_ids = {}
        identity_matches = (set(identity_types) == expected_types and len(identity_types) == len(expected_types)
                            and isinstance(user_ids, Mapping)
                            and all(isinstance(key, str) and isinstance(value, Mapping) for key, value in user_ids.items())
                            and len(user_ids) == len(expected_user_ids)
                            and {key.casefold() for key in user_ids} == expected_user_ids)
        if (not isinstance(interfaces, list) or len(interfaces) != 1 or not isinstance(interfaces[0], Mapping)
            or not _same(interfaces[0].get("id"), targets["nicId"])
            or not isinstance(interfaces[0].get("properties"), Mapping)
            or interfaces[0]["properties"].get("deleteOption") != "Delete"
            or not isinstance(disk.get("managedDisk"), Mapping)
            or not _same(disk["managedDisk"].get("id"), record["osDiskId"])
            or disk.get("deleteOption") != "Delete" or properties.get("storageProfile", {}).get("dataDisks", []) != []
            or not identity_matches or not _same(identity.get("principalId"), record["principalId"])):
            _error("management-binding-mismatch", "管理VMの専用NIC・OS disk・マネージドIDが配備記録と一致しません。", targets["vmId"])
    if nic is not None:
        _management_resource_owner(targets["nicId"], nic, "Microsoft.Network/networkInterfaces", configuration)
        properties = nic.get("properties")
        if not isinstance(properties, Mapping):
            _error("management-binding-mismatch", "管理NICの公開接続情報を確認できません。", targets["nicId"])
        interfaces = properties.get("ipConfigurations")
        attached = properties.get("virtualMachine")
        if (not isinstance(interfaces, list) or len(interfaces) != 1 or not isinstance(interfaces[0], Mapping)
            or not isinstance(interfaces[0].get("properties"), Mapping)
            or not isinstance(interfaces[0]["properties"].get("subnet"), Mapping)
            or not _same(interfaces[0]["properties"]["subnet"].get("id"), targets["subnetId"])
            or interfaces[0]["properties"].get("publicIPAddress") is not None
            or attached is not None and (not isinstance(attached, Mapping) or not _same(attached.get("id"), targets["vmId"]))):
            _error("management-binding-mismatch", "管理NICが別のVM・subnet・公開IPを参照しているため削除しません。", targets["nicId"])
    return {"vm": vm, "nic": nic}


def _validate_management_record(record: Any, targets: dict) -> None:
    """保存出力も成功済みmodule出力も、同じ固定IDとprincipalの条件で照合する。"""
    if (not isinstance(record, Mapping) or record and (not _same(record.get("id"), targets["vmId"])
        or not _same(record.get("name"), targets["vmId"].rsplit("/", 1)[-1])
        or not _same(record.get("nicId"), targets["nicId"]) or not _same(record.get("osDiskId"), targets["diskId"]))):
        _error("management-state-mismatch", "管理VM・NIC・OS diskの配備出力が今回の命名とmaint RGに一致しません。", targets["vmId"])
    if record:
        try:
            if not UUID(record.get("principalId")).int:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            _error("management-state-mismatch", "管理VMの配備出力に有効なマネージドIDがありません。", targets["vmId"])


def _management_preflight(configuration: dict, state: Mapping, transport: Transport) -> dict:
    """他の資源を一つも変更する前に、配備出力の全IDと管理VM/NICを照合する。

    本体の途中失敗では、管理VMのmoduleだけ成功していてもmainの出力が保存されない。
    その場合だけ同じmaint RGの正確なmoduleを読み、成功した実出力をメモリーへ回復する。
    保存済みの出力が不正な場合は置き換えず停止し、別の配備を探索することもしない。
    """
    group = _rg_id(configuration, "maint")
    stem = configuration["resourceStem"]
    targets = {
        "vmId": group + "/providers/Microsoft.Compute/virtualMachines/vm-" + stem + "-mgmt",
        "nicId": group + "/providers/Microsoft.Network/networkInterfaces/nic-" + stem + "-mgmt",
        "diskId": group + "/providers/Microsoft.Compute/disks/osdisk-" + stem + "-mgmt",
        "subnetId": _rg_id(configuration, "nw") + "/providers/Microsoft.Network/virtualNetworks/vnet-" + stem + "/subnets/snet-" + stem + "-vm",
    }
    outputs = state.get("outputs", {})
    record = outputs.get("managementVm", {}) if isinstance(outputs, Mapping) else None
    _validate_management_record(record, targets)
    targets.update(record=record, recordSource="saved-output" if record else "not-created")
    if not record:
        # 名前だけでは削除しない。まず実在を読み、両方404ならmodule回復も不要。
        vm = _expect_read(_request(transport, "GET", targets["vmId"], API["compute"]), targets["vmId"])
        nic = _expect_read(_request(transport, "GET", targets["nicId"], API["network"]), targets["nicId"])
        if vm is None and nic is None:
            return targets
        module_id = group + "/providers/Microsoft.Resources/deployments/" + stem + "-management"
        module = _expect_read(_request(transport, "GET", module_id, API["deployments"]), module_id)
        properties = module.get("properties", {}) if isinstance(module, Mapping) else {}
        module_outputs = properties.get("outputs", {}) if isinstance(properties, Mapping) else {}
        output = module_outputs.get("managementVm", {}) if isinstance(module_outputs, Mapping) else {}
        if (not isinstance(module, Mapping) or not _same(module.get("id"), module_id)
            or module.get("name") != stem + "-management"
            or str(module.get("type", "")).casefold() != "microsoft.resources/deployments"
            or not isinstance(properties, Mapping) or properties.get("provisioningState") != "Succeeded"
            or not isinstance(output, Mapping) or str(output.get("type", "")).casefold() != "object"
            or not isinstance(output.get("value"), Mapping) or not output["value"]):
            _error("management-output-unverified", "今回の管理VM moduleの成功と実出力を確認できないため削除しません。", module_id)
        record = output["value"]
        _validate_management_record(record, targets)
        targets.update(record=record, recordSource="management-deployment-output", recordSourceId=module_id)
    _management_snapshot(configuration, targets, transport)
    return targets


def _delete_management_before_network(configuration: dict, targets: dict, transport: Transport, *,
                                      max_checks: int, wait_seconds: float,
                                      sleep: Callable[[float], None]) -> dict:
    """VM→専用NICを先に回収し、azdのRG削除順にかかわらずNWを削除できるようにする。"""
    current = _management_snapshot(configuration, targets, transport)
    vm_requested = False
    if current["vm"] is not None:
        if str(current["vm"].get("properties", {}).get("provisioningState", "")).casefold() != "deleting":
            _request(transport, "DELETE", targets["vmId"], API["compute"])
            vm_requested = True
        _wait_absent(transport, targets["vmId"], API["compute"], max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
    # deleteOption:DeleteでNICも消えるのが通常。残っているときだけ、同じ専用NICを
    # 所有・接続先から再検査する。VMの404を確認する前にNICを削除しない。
    current = _management_snapshot(configuration, targets, transport)
    if current["vm"] is not None:
        _error("cleanup-pending", "管理VMの不存在を再確認できないためNICを削除しません。", targets["vmId"])
    nic = current["nic"]
    nic_requested = False
    if nic is not None:
        if str(nic.get("properties", {}).get("provisioningState", "")).casefold() != "deleting":
            _request(transport, "DELETE", targets["nicId"], API["network"])
            nic_requested = True
        _wait_absent(transport, targets["nicId"], API["network"], max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
    return {"vm": {"resourceId": targets["vmId"], "status": "absent", "httpStatus": 404, "deleteRequested": vm_requested},
            "nic": {"resourceId": targets["nicId"], "status": "absent", "httpStatus": 404, "deleteRequested": nic_requested},
            "crossResourceGroupSubnetReferenceRemoved": True,
            "recordSource": targets["recordSource"],
            **({"recordSourceId": targets["recordSourceId"]} if "recordSourceId" in targets else {})}


def _validate_portal_record(record: Any, targets: dict, configuration: dict) -> None:
    """Portalの保存出力を、サービスRGに配置するCA・CAEの固定IDと照合する。"""
    if not isinstance(record, Mapping):
        _error("portal-state-mismatch", "Portalの配備出力を確認できません。", targets["applicationId"])
    if not record:
        return
    expected = {
        "applicationId": targets["applicationId"], "applicationName": "ca-" + configuration["resourceStem"] + "-portal",
        "environmentId": targets["environmentId"], "environmentName": "cae-" + configuration["resourceStem"],
        "resourceGroup": configuration["resourceGroupNames"]["svc"],
        "applicationResourceGroup": configuration["resourceGroupNames"]["svc"],
        "environmentResourceGroup": configuration["resourceGroupNames"]["svc"],
    }
    if any(not _same(record.get(key), value) for key, value in expected.items()):
        _error("portal-state-mismatch", "PortalのCA・CAEの配備出力が今回の命名とsvc RGに一致しません。", targets["applicationId"])


def _portal_resource_owner(configuration: dict, targets: dict, kind: str, resource: Mapping) -> None:
    """CA・CAEの所有情報と接続先を照合し、別環境へ削除を広げない。"""
    resource_id = targets[kind + "Id"]
    expected_type = "Microsoft.App/containerApps" if kind == "application" else "Microsoft.App/managedEnvironments"
    tags = resource.get("tags")
    if (not _same(resource.get("id"), resource_id)
        or not _same(resource.get("name"), resource_id.rsplit("/", 1)[-1])
        or str(resource.get("type", "")).casefold() != expected_type.casefold()
        or str(resource.get("location", "")).replace(" ", "").casefold() != configuration["location"].casefold()
        or not isinstance(tags, Mapping) or tags.get("product") != configuration["productName"]
        or tags.get("environment") != configuration["environmentName"]):
        _error("portal-owner-mismatch", "PortalのID・名前・種類・region・所有タグが一致しません。", resource_id)
    properties = resource.get("properties")
    if not isinstance(properties, Mapping):
        _error("portal-binding-mismatch", "Portalの接続先情報を確認できません。", resource_id)
    if kind == "application":
        # ARMの返却に含まれる環境参照をすべて照合し、今回のCAEに属するCAを削除します。
        references = [properties[key] for key in ("managedEnvironmentId", "environmentId")
                      if key in properties and properties[key] not in (None, "")]
        matches = (all(_same(value, targets["environmentId"]) for value in references)
                   and (bool(references) or _incomplete_app_resource(properties)))
    else:
        network = properties.get("vnetConfiguration")
        subnet = network.get("infrastructureSubnetId") if isinstance(network, Mapping) else None
        matches = (_same(subnet, targets["subnetId"]) or
                   (network is None or isinstance(network, Mapping) and subnet in (None, ""))
                   and _incomplete_app_resource(properties))
    if not matches:
        _error("portal-binding-mismatch", "PortalのCA→CAE、またはCAE→ACA subnetの参照が今回の配備先と一致しません。", resource_id)
    if kind == 'environment' and targets.get('infrastructureFrozen'):
        observed = {'environmentId': targets['environmentId'], 'applicationInfrastructure': []}
        _capture_application_infrastructure(configuration, observed, resource)
        recorded = {item['resourceGroupId'].casefold() for item in targets['applicationInfrastructure']}
        if any(item['resourceGroupId'].casefold() not in recorded for item in observed['applicationInfrastructure']):
            _error('application-infrastructure-changed',
                   'CAEの管理ネットワークRGが事前確認後に変わりました。記録を更新してから撤去を再実行してください。', resource_id)


def _portal_snapshot(configuration: dict, targets: dict, transport: Transport) -> dict:
    """CAとCAEを読み、存在する資源の所有情報・実接続先を照合する。"""
    current = {}
    for kind in ("application", "environment"):
        resource_id = targets[kind + "Id"]
        resource = _expect_read(_request(transport, "GET", resource_id, API["containerApps"]), resource_id)
        if resource is not None:
            _portal_resource_owner(configuration, targets, kind, resource)
        current[kind] = resource
    return current


def _application_infrastructure(configuration: dict, inventory: Any) -> list[dict]:
    """CAEの管理RGを読取対象として検証する。設定した5RGや別サブスクは認めない。"""
    code = 'application-infrastructure-state-mismatch'
    if not isinstance(inventory, list):
        _error(code, 'Container Appsの管理ネットワーク記録が配列ではありません。')
    environment_id = (_rg_id(configuration, 'svc')
                      + '/providers/Microsoft.App/managedEnvironments/cae-' + configuration['resourceStem'])
    own_groups = {_rg_id(configuration, kind).casefold() for kind in ('id', 'nw', 'monitor', 'svc', 'maint')}
    result, seen = [], set()
    for item in inventory:
        if not isinstance(item, Mapping) or set(item) != {'environmentId', 'resourceGroupId'}:
            _error(code, 'Container Appsの管理ネットワーク記録の項目を確認できません。')
        group_id = item['resourceGroupId']
        try:
            parts = _parts(group_id)
        except CleanupError:
            _error(code, 'Container Appsの管理RGのResource IDが不正です。')
        if (not isinstance(item['environmentId'], str) or item['environmentId'].casefold() != environment_id.casefold()
                or len(parts) != 4 or parts[0].casefold() != 'subscriptions'
                or not _same(parts[1], configuration['subscriptionId']) or parts[2].casefold() != 'resourcegroups'
                or not re.fullmatch(r'[\w().-]{1,90}', parts[3]) or parts[3].endswith('.')
                or group_id.casefold() != f"/subscriptions/{configuration['subscriptionId']}/resourceGroups/{parts[3]}".casefold()
                or group_id.casefold() in own_groups):
            _error(code, 'Container Appsの管理RGが今回のCAE・サブスクの外部管理RGに一致しません。')
        canonical = {'environmentId': environment_id,
                     'resourceGroupId': f"/subscriptions/{configuration['subscriptionId']}/resourceGroups/{parts[3]}"}
        if canonical['resourceGroupId'].casefold() not in seen:
            result.append(canonical)
            seen.add(canonical['resourceGroupId'].casefold())
    return result


def _capture_application_infrastructure(configuration: dict, targets: dict, environment: dict | None) -> None:
    """所有・接続先を照合したCAEから管理RG名だけを保存し、消滅後の再実行へ残す。"""
    if environment is None:
        return
    name = environment['properties'].get('infrastructureResourceGroup')
    if name in (None, ''):
        return
    if not isinstance(name, str) or '/' in name:
        _error('application-infrastructure-state-mismatch', 'Container Appsの管理RG名が不正です。')
    item = {'environmentId': targets['environmentId'],
            'resourceGroupId': f"/subscriptions/{configuration['subscriptionId']}/resourceGroups/{name}"}
    targets['applicationInfrastructure'] = _application_infrastructure(
        configuration, [*targets['applicationInfrastructure'], item])


def _portal_preflight(configuration: dict, state: Mapping, transport: Transport) -> dict:
    """他の資源を変更する前に、Portalの所有・CA→CAE→subnetの接続を確認する。

    保存出力を検証し、なければ同じsvc RGの正確なPortal module出力を参照する。
    部分構築で出力が無くても実資源の所有・接続先を確認する。不正な保存出力は拒否する。
    """
    group = _rg_id(configuration, "svc")
    stem = configuration["resourceStem"]
    targets = {
        "applicationId": group + "/providers/Microsoft.App/containerApps/ca-" + stem + "-portal",
        "environmentId": group + "/providers/Microsoft.App/managedEnvironments/cae-" + stem,
        "subnetId": _rg_id(configuration, "nw") + "/providers/Microsoft.Network/virtualNetworks/vnet-" + stem + "/subnets/snet-" + stem + "-aca",
        "applicationInfrastructure": _application_infrastructure(configuration, state.get('applicationInfrastructure', [])),
    }
    outputs = state.get("outputs", {})
    record = outputs.get("portal", {}) if isinstance(outputs, Mapping) else None
    _validate_portal_record(record, targets, configuration)
    targets.update(record=record, recordSource="saved-output" if record else "not-created")
    if not record:
        current = {kind: _expect_read(_request(transport, "GET", targets[kind + "Id"], API["containerApps"]), targets[kind + "Id"])
                   for kind in ("application", "environment")}
        resources_absent = all(resource is None for resource in current.values())
        module_id = group + "/providers/Microsoft.Resources/deployments/" + stem + "-portal"
        module = _expect_read(_request(transport, "GET", module_id, API["deployments"]), module_id)
        properties = module.get("properties", {}) if isinstance(module, Mapping) else {}
        module_outputs = properties.get("outputs", {}) if isinstance(properties, Mapping) else {}
        output = module_outputs.get("platform", {}) if isinstance(module_outputs, Mapping) else {}
        verified_module = (isinstance(module, Mapping) and _same(module.get("id"), module_id)
            and module.get("name") == stem + "-portal"
            and str(module.get("type", "")).casefold() == "microsoft.resources/deployments"
            and isinstance(properties, Mapping) and properties.get("provisioningState") == "Succeeded"
            and isinstance(output, Mapping) and str(output.get("type", "")).casefold() == "object"
            and isinstance(output.get("value"), Mapping) and bool(output["value"]))
        if not verified_module:
            if resources_absent:
                return targets
            # 任意の資源探索へ広げない。今回の固定ID・所有タグ・実接続先で
            # 部分構築を照合し、成功出力がまだ無い環境も撤去できるようにする。
            for kind, resource in current.items():
                if resource is not None:
                    _portal_resource_owner(configuration, targets, kind, resource)
            _capture_application_infrastructure(configuration, targets, current['environment'])
            targets.update(recordSource="verified-current-resources")
            return targets
        # 前の撤去でCA・CAEが消え、subnet参照だけが解除待ちの場合も、成功した
        # module出力を回復して同じsubnetの確認から再開します。
        record = output["value"]
        _validate_portal_record(record, targets, configuration)
        targets.update(record=record, recordSource="portal-deployment-output", recordSourceId=module_id)
    current = _portal_snapshot(configuration, targets, transport)
    _capture_application_infrastructure(configuration, targets, current['environment'])
    return targets


def _wait_portal_absent(configuration: dict, targets: dict, kind: str, transport: Transport, *,
                        max_checks: int, wait_seconds: float, sleep: Callable[[float], None]) -> None:
    """非同期削除中も所有・接続先を照合し、同じCAまたはCAEの404を有限回待つ。"""
    resource_id = targets[kind + "Id"]
    for attempt in range(max_checks):
        resource = _expect_read(_request(transport, "GET", resource_id, API["containerApps"]), resource_id)
        if resource is None:
            return
        _portal_resource_owner(configuration, targets, kind, resource)
        if attempt + 1 < max_checks:
            sleep(wait_seconds)
    _error("cleanup-pending", "Portalの非同期削除が完了していません。同じ環境の撤去処理を再実行して状態を確認してください。", resource_id)


def _wait_portal_subnet_released(targets: dict, transport: Transport, *, max_checks: int,
                                wait_seconds: float, sleep: Callable[[float], None]) -> dict:
    """CAE削除後の管理ネットワークの片付けを待ち、ACA subnetの参照解除を確認する。"""
    resource_id = targets["subnetId"]
    reference_names = ("ipConfigurations", "serviceAssociationLinks", "resourceNavigationLinks", "ipConfigurationProfiles")
    for attempt in range(max_checks):
        subnet = _expect_read(_request(transport, "GET", resource_id, API["network"]), resource_id)
        if subnet is None:
            return {"resourceId": resource_id, "status": "absent", "httpStatus": 404,
                    **dict.fromkeys(reference_names, 0)}
        properties = subnet.get("properties")
        if not _same(subnet.get("id"), resource_id) or not isinstance(properties, Mapping):
            _error("portal-binding-mismatch", "ACA subnetのID・参照情報を確認できません。", resource_id)
        references = {key: properties.get(key, []) for key in reference_names}
        if any(not isinstance(values, list) or any(not isinstance(value, Mapping)
                   or not isinstance(value.get("id"), str) or not value["id"].startswith("/") for value in values)
               for values in references.values()):
            _error("portal-binding-mismatch", "ACA subnetのIP参照・サービス関連付けを確認できません。", resource_id)
        if not any(references.values()):
            return {"resourceId": resource_id, "status": "released", "httpStatus": 200,
                    **dict.fromkeys(reference_names, 0)}
        if attempt + 1 < max_checks:
            sleep(wait_seconds)
    _error("portal-subnet-release-pending", "CAEの削除後もACA subnetにIP参照またはサービス関連付けが残っています。解除完了を確認してから撤去処理を再実行してください。", resource_id)


def _delete_portal_before_network(configuration: dict, targets: dict, transport: Transport, *,
                                  max_checks: int, wait_seconds: float,
                                  sleep: Callable[[float], None], state: Mapping | None = None) -> dict:
    """全ジョブ・Broker→Portal CA→CAE→subnetの順で解放し、azdのNW削除へ渡す。"""
    children = _delete_app_children(configuration, targets, state or {}, transport,
        max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
    current = _portal_snapshot(configuration, targets, transport)
    result = {"recordSource": targets["recordSource"],
              "children": children,
              "applicationInfrastructure": targets['applicationInfrastructure'],
              **({"recordSourceId": targets["recordSourceId"]} if "recordSourceId" in targets else {})}
    for kind in ("application", "environment"):
        # CAの不存在を確認してからCAEの削除へ進む。削除直前にも接続先を再検査する。
        if kind == "environment":
            current = _portal_snapshot(configuration, targets, transport)
            if current["application"] is not None:
                _error("cleanup-pending", "PortalのCAが残っているためCAEの削除へ進めません。", targets["applicationId"])
            if (_credential_apps_preflight(configuration, state or {}, transport)
                    or _portal_jobs_preflight(configuration, targets, transport)):
                _error('cleanup-pending', '共用CAEに属するアプリまたはジョブが再出現しています。', targets['environmentId'])
        resource = current[kind]
        provisioning_state = str(resource.get("properties", {}).get("provisioningState", "")).casefold() if resource is not None else ""
        already_deleting = (resource is not None and
            (provisioning_state == "deleting" or kind == "environment" and provisioning_state == "scheduledfordelete"))
        if resource is not None:
            if not already_deleting:
                _request(transport, "DELETE", targets[kind + "Id"], API["containerApps"])
            _wait_portal_absent(configuration, targets, kind, transport,
                max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
        result[kind] = {"resourceId": targets[kind + "Id"], "status": "absent", "httpStatus": 404,
                        "deleteRequested": resource is not None and not already_deleting, "alreadyDeleting": already_deleting}
    # 出力のない部分構築の再実行でも、既に消えたCAEが残した参照を見落とさない。
    result["subnet"] = _wait_portal_subnet_released(targets, transport,
        max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
    result["crossResourceGroupSubnetReferenceRemoved"] = True
    return result


def _delete_apim_before_network(configuration: dict, current: dict, transport: Transport, *,
                                max_checks: int, wait_seconds: float,
                                sleep: Callable[[float], None]) -> dict:
    """今回のAPIMだけを先に削除し、NW側のsubnetを使い終えたことを確認する。

    azd 1.33の通常のSubscription配備では、RGを依存順に並べ替えず1個ずつ
    削除する。NWのRGが先になると、svcのAPIMが使うsubnetを削除できない。
    そのためモデルの先行削除後に、配備記録と実際の所有情報が一致する
    APIMだけを削除する。設定済みCM providerがあれば、その配下の接続・access policy
    を含めて先に回収する。VNetの設定変更や別APIMの探索・削除は行わない。
    202は受付であり完了ではない。正確な同一IDのGET404を待ってから戻る。
    前回の待機期限後に再実行して既にDeletingなら、削除要求を重ねず待つ。

    根拠: https://github.com/Azure/azure-dev/blob/azure-dev-cli_1.33.0/cli/azd/pkg/azapi/standard_deployments.go#L514
    https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-manage-subnet#delete-a-subnet
    """
    resource_id = current["serviceIds"].get("apim")
    if resource_id is None:
        return {"status": "not-deployed", "apimDeletionVerified": False,
                "deleteRequested": False, "alreadyDeleting": False}
    expected = (_rg_id(configuration, "svc")
                + "/providers/Microsoft.ApiManagement/service/")
    if (not isinstance(resource_id, str) or not resource_id.casefold().startswith(expected.casefold())
            or not apim_name_matches(configuration, resource_id[len(expected):])):
        _error("apim-owner-mismatch", "APIMの削除先が今回の命名・配置先と一致しません。", resource_id)
    provider_ids = _credential_provider_ids(configuration, resource_id)
    service = _expect_read(_request(transport, "GET", resource_id, API["apim"]), resource_id)
    if service is None:
        result = {"resourceId": resource_id, "status": "absent", "httpStatus": 404,
                  "apimDeletionVerified": True, "deleteRequested": False, "alreadyDeleting": False}
        result["resourceInventory"] = _wait_resource_inventory_absent(configuration, resource_id, transport,
            max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
        if provider_ids:
            result["credentialProviders"] = [{"resourceId": provider_id, "status": "parent-absent",
                "deletionVerified": False, "parentDeletionVerified": True, "deleteRequested": False} for provider_id in provider_ids]
        return result
    tags = service.get("tags") or {}
    # ARMはregionをjapaneastでもJapan Eastでも返す。表示上の空白だけを
    # 取り除いて比較し、別region・別ID・別環境の同名資源は受け入れない。
    location = str(service.get("location", "")).replace(" ", "").lower()
    if (not _same(service.get("id"), resource_id)
        or not _same(service.get("name"), resource_id.rsplit("/", 1)[-1])
        or str(service.get("type", "")).lower() != "microsoft.apimanagement/service"
        or location != configuration["location"].lower()
        or not isinstance(tags, Mapping)
        or tags.get("product") != configuration["productName"]
        or tags.get("environment") != configuration["environmentName"]):
        _error("apim-owner-mismatch", "APIMのID・種類・region・所有タグを確認できないため削除しません。", resource_id)
    properties = service.get("properties")
    already_deleting = (isinstance(properties, Mapping)
                        and str(properties.get("provisioningState", "")).lower() == "deleting")
    # 所有照合はDeletingでも省略しない。明確に削除中と読めた場合だけ再DELETEを
    # 避ける。Creating/Failedや状態未返却の部分構築は、通常どおり削除を要求する。
    provider_results = []
    if not already_deleting:
        provider_results = _delete_credential_providers(provider_ids, transport, max_checks=max_checks,
            wait_seconds=wait_seconds, sleep=sleep)
        _request(transport, "DELETE", resource_id, API["apim"])
    _wait_absent(transport, resource_id, API["apim"], max_checks=max_checks,
                 wait_seconds=wait_seconds, sleep=sleep)
    result = {"resourceId": resource_id, "status": "absent", "httpStatus": 404,
              "apimDeletionVerified": True, "deleteRequested": not already_deleting,
              "alreadyDeleting": already_deleting}
    result["resourceInventory"] = _wait_resource_inventory_absent(configuration, resource_id, transport,
        max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
    for provider in provider_results:
        if provider.get("parentDeletionRequired"):
            # 親APIMの404とprovider自身の404は別の観測。後者を成功へ書き換えない。
            provider["parentDeletionVerified"] = True
    if provider_ids:
        # 前の down で親が削除中なら、利用不能な子へ変更を追加しない。子の個別回収を
        # 確認した記録にはせず、今回確認できた親 GET404 と分けて残す。
        result["credentialProviders"] = provider_results if not already_deleting else [
            {"resourceId": provider_id, "status": "not-verified-parent-deleting", "deletionVerified": False,
             "parentDeletionVerified": True, "deleteRequested": False} for provider_id in provider_ids]
    return result


def _incomplete_app_resource(properties):
    """失敗・中断・削除中だけ、まだ設定が返らない部分構築として認める。"""
    return str(properties.get("provisioningState", "")).casefold() in {
        "failed", "canceled", "cancelled", "deleting",
    }


def _app_resource_owner(configuration, resource_id, environment_id, resource, *, code):
    """同じ環境の固定CA/ジョブだけを認め、矛盾する環境参照を一つも許さない。"""
    kind = resource_id.split('/')[-2]
    tags, properties = resource.get('tags'), resource.get('properties')
    if (not _same(resource.get('id'), resource_id)
            or not _same(resource.get('name'), resource_id.rsplit('/', 1)[-1])
            or str(resource.get('type', '')).casefold() != ('Microsoft.App/' + kind).casefold()
            or str(resource.get('location', '')).replace(' ', '').casefold() != configuration['location'].casefold()
            or not isinstance(tags, Mapping) or tags.get('product') != configuration['productName']
            or tags.get('environment') != configuration['environmentName']
            or not isinstance(properties, Mapping)):
        _error(code, 'アプリまたはジョブのID・種類・region・所有タグを確認できません。', resource_id)
    references = [properties[key] for key in ('environmentId', 'managedEnvironmentId')
                  if properties.get(key) not in (None, '')]
    if (any(not _same(value, environment_id) for value in references)
            or not references and not _incomplete_app_resource(properties)):
        _error(code, 'アプリまたはジョブが今回の共用CAEを参照していません。', resource_id)


def _credential_apps_preflight(configuration, state, transport):
    """保存出力の有無にかかわらず、固定したGatewayのCA・ジョブを照合する。"""
    outputs = state.get("outputs", {})
    if not isinstance(outputs, Mapping):
        _error('credential-state-mismatch', 'Gatewayの配備出力形式を確認できません。')
    record = outputs.get("credentialBroker")
    foundation = outputs.get("gatewayFoundation")
    stem = configuration["resourceStem"]
    service_group = configuration["resourceGroupNames"]["svc"]
    prefix = f"/subscriptions/{configuration['subscriptionId']}/resourceGroups/{service_group}/providers/Microsoft.App/"
    environment = prefix + "managedEnvironments/cae-" + stem
    names = resource_names(configuration)
    # Gatewayの配備成否や保存出力に依存せず、対象は同じ環境の固定名に限定します。
    if record is not None and (not isinstance(record, Mapping)
            or record and (record.get("applicationName") != names['application']
            or record.get("resourceGroup") != service_group)):
        _error("credential-state-mismatch", "認証窓口の配備記録が今回の環境と一致しません。")
    if foundation is not None and (not isinstance(foundation, Mapping)
            or foundation and (foundation.get("resourceGroup") != service_group
            or foundation.get("apimName") != "apim-" + stem
            or foundation.get("environmentName") != "cae-" + stem)):
        _error("credential-state-mismatch", "Gatewayの共通基盤が今回の環境と一致しません。")
    targets = [("jobs", names['reconcileJob']), ("jobs", names['schemaJob']),
               ("containerApps", names['application'])]
    result = []
    for kind, name in targets:
        resource_id = prefix + kind + "/" + name
        resource = _expect_read(_request(transport, "GET", resource_id, API["containerApps"]), resource_id)
        if resource is None:
            continue
        _app_resource_owner(configuration, resource_id, environment, resource, code='credential-owner-mismatch')
        result.append(resource_id)
    return result


def _portal_jobs_preflight(configuration, targets, transport):
    """PortalのSQL初期化・所有グループ照合ジョブを、共用CAEより先に解放する。"""
    prefix = _rg_id(configuration, 'svc') + '/providers/Microsoft.App/jobs/'
    result = []
    for name in portal_resource_names(configuration).values():
        resource_id = prefix + name
        resource = _expect_read(_request(transport, 'GET', resource_id, API['containerApps']), resource_id)
        if resource is None:
            continue
        _app_resource_owner(configuration, resource_id, targets['environmentId'], resource, code='portal-owner-mismatch')
        result.append(resource_id)
    return result


def _delete_app_children(configuration, targets, state, transport, *, max_checks, wait_seconds, sleep):
    """削除直前と待機中も所有を照合し、全ての既知の子が404になるまで待つ。"""
    result = []
    credential = _credential_apps_preflight(configuration, state, transport)
    portal = _portal_jobs_preflight(configuration, targets, transport)
    for resource_id in [*credential, *portal]:
        code = 'credential-owner-mismatch' if resource_id in credential else 'portal-owner-mismatch'
        resource = _expect_read(_request(transport, 'GET', resource_id, API['containerApps']), resource_id)
        if resource is None:
            continue
        _app_resource_owner(configuration, resource_id, targets['environmentId'], resource, code=code)
        deleting = str(resource['properties'].get('provisioningState', '')).casefold() == 'deleting'
        if not deleting:
            _request(transport, 'DELETE', resource_id, API['containerApps'])
        for attempt in range(max_checks):
            remaining = _expect_read(_request(transport, 'GET', resource_id, API['containerApps']), resource_id)
            if remaining is None:
                break
            _app_resource_owner(configuration, resource_id, targets['environmentId'], remaining, code=code)
            if attempt + 1 < max_checks:
                sleep(wait_seconds)
        else:
            _error('cleanup-pending', '共用CAEを使うアプリまたはジョブの削除が完了していません。', resource_id)
        result.append({'resourceId': resource_id, 'status': 'absent', 'httpStatus': 404,
                       'deleteRequested': not deleting, 'alreadyDeleting': deleting})
    # 先に404だったIDや、削除確認後に再出現したIDもCAE削除前に検出する。
    if (_credential_apps_preflight(configuration, state, transport)
            or _portal_jobs_preflight(configuration, targets, transport)):
        _error('cleanup-pending', '共用CAEに属するアプリまたはジョブが再出現しています。')
    return result


def pre_down(config_path: str | Path, state: Mapping[str, Any], transport: Transport, *,
             max_checks: int = 30, wait_seconds: float = 2,
             apim_max_checks: int = 180, apim_wait_seconds: float = 10,
             sleep: Callable[[float], None] = time.sleep,
             checkpoint: Callable[[list[dict]], None] | None = None) -> dict:
    """保持義務を確認し、モデル→APIM→Portal→管理VMの順で先に削除する。

    Locked保持があれば変更前に止める。Unlocked保持の削除も設定で明示した場合だけ。
    環境名がtestという理由だけで本番と違う削除権限を与えることはしない。
    APIMの非同期削除は他の短い確認と分け、既定で約30分の有限待機にする。
    期限を超えても成功とはせず、同じ対象の残存確認を担当者へ引き渡す。
    """
    _options(max_checks, wait_seconds)
    _options(apim_max_checks, apim_wait_seconds)
    configuration = load_configuration(config_path)
    current = _validate_state(configuration, state)
    management_targets = _management_preflight(configuration, state, transport)
    portal_targets = _portal_preflight(configuration, state, transport)
    portal_jobs = _portal_jobs_preflight(configuration, portal_targets, transport)
    credential_apps = _credential_apps_preflight(configuration, state, transport)
    policies: list[tuple[str, dict]] = []
    policy_results: list[dict] = []
    # 変更を1件も行う前に全保持ポリシーを調べる。後の方にLockedが見つかっても、
    # 先のポリシーだけ既に削除した状態にしない。
    for policy_id in current["policyIds"]:
        response = _request(transport, "GET", policy_id, API["storage"])
        policy = _expect_read(response, policy_id)
        if policy is None:
            policy_results.append({"resourceId": policy_id, "status": "absent", "httpStatus": 404})
            continue
        if not _same(policy.get("id"), policy_id):
            _error("resource-mismatch", "監査保持ポリシーの読戻しIDが一致しません。", policy_id)
        properties = policy.get("properties", {})
        mode = properties.get("state")
        period = properties.get("immutabilityPeriodSinceCreationInDays")
        if mode == "Deleted" and type(period) is int and period == 0:
            # 未作成・削除済みポリシーが空ETagのメタデータとして返る場合がある。
            # 保持の削除対象はないが、HTTP 404を確認したとは記録しない。
            policy_results.append({"resourceId": policy_id, "status": "retention-removed", "httpStatus": 200, "azureState": "Deleted"})
            continue
        if mode == "Locked":
            _error("locked-retention", "監査StorageにLocked WORMがあります。保持を回避せずazd downを停止します。保持期限と本番の保全手順を確認してください。", policy_id)
        if mode != "Unlocked":
            _error("unknown-retention", "監査保持ポリシーの状態を判定できません。", policy_id)
        if configuration["monitoring"]["auditImmutabilityMode"] != "Disabled":
            _error("configured-retention", "監査保持を有効にした構成のため、Unlockedポリシーもこの撤去処理では解除しません。保持方針を確認してください。", policy_id)
        etag = policy.get("etag") or next((value for key, value in response["headers"].items() if key.lower() == "etag"), None)
        if not isinstance(etag, str) or len(etag) < 3 or not etag.startswith('"') or not etag.endswith('"') or etag == '"*"':
            _error("missing-etag", "監査保持ポリシーの正確なETagを取得できないため解除しません。", policy_id)
        policies.append((policy_id, {"etag": etag}))

    if checkpoint is not None:
        checkpoint([dict(item) for item in portal_targets['applicationInfrastructure']])
    portal_targets['infrastructureFrozen'] = True
    models = _delete_models_first(configuration, current, transport,
                                 max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
    apim = _delete_apim_before_network(configuration, current, transport,
                                      max_checks=apim_max_checks,
                                      wait_seconds=apim_wait_seconds, sleep=sleep)
    portal = _delete_portal_before_network(configuration, portal_targets, transport,
        max_checks=apim_max_checks, wait_seconds=apim_wait_seconds, sleep=sleep, state=state)
    management = _delete_management_before_network(configuration, management_targets, transport,
        max_checks=apim_max_checks, wait_seconds=apim_wait_seconds, sleep=sleep)
    for policy_id, policy in policies:
        _request(transport, "DELETE", policy_id, API["storage"], headers={"If-Match": policy["etag"]})
        observed = _request(transport, "GET", policy_id, API["storage"])
        readback = _expect_read(observed, policy_id)
        if readback is None:
            policy_results.append({"resourceId": policy_id, "status": "absent", "httpStatus": 404})
        elif (_same(readback.get("id"), policy_id) and readback.get("properties", {}).get("state") in ("Unlocked", "Deleted")
              and type(readback.get("properties", {}).get("immutabilityPeriodSinceCreationInDays")) is int
              and readback["properties"]["immutabilityPeriodSinceCreationInDays"] == 0):
            # DELETE後も、保持期間0のUnlocked/Deletedメタデータが残ることがある。
            # 200で確認した状態として記録し、404や完全消滅とは区別する。
            policy_results.append({"resourceId": policy_id, "status": "retention-removed", "httpStatus": 200,
                                   "azureState": readback["properties"]["state"]})
        else:
            _error("retention-removal-unverified", "監査保持ポリシーの解除を確認できません。", policy_id)

    diagnostic_id = f"/subscriptions/{configuration['subscriptionId']}/providers/Microsoft.Insights/diagnosticSettings/diag-{configuration['resourceStem']}-activity"
    diagnostic = _expect_read(_request(transport, "GET", diagnostic_id, API["diagnostics"]), diagnostic_id)
    if diagnostic is not None:
        properties = diagnostic.get("properties", {})
        monitoring = state.get("monitoring") or {}
        own_storage_id = current["storage"].get("collectJapan", {}).get("id")
        if not _same(diagnostic.get("id"), diagnostic_id) or not _same(properties.get("workspaceId"), monitoring.get("workspaceId")):
            _error("diagnostic-owner-mismatch", "同名のActivity診断設定が現在のLAWを参照していないため削除しません。", diagnostic_id)
        if properties.get("storageAccountId") and not _same(properties["storageAccountId"], own_storage_id):
            _error("diagnostic-owner-mismatch", "Activity診断設定が別の監査Storageを参照しているため削除しません。", diagnostic_id)
        _request(transport, "DELETE", diagnostic_id, API["diagnostics"])
        _wait_absent(transport, diagnostic_id, API["diagnostics"], max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
    result = {"phase": "predown", "status": "ready-for-azd-down", "policies": policy_results,
              "modelDeployments": models,
              "apiManagement": apim,
              "credentialApplications": credential_apps,
              "portalJobs": portal_jobs,
              "portal": portal,
              "management": management,
              "activityDiagnostic": {"resourceId": diagnostic_id, "status": "absent", "httpStatus": 404}}
    credential_manager = _credential_manager_release(configuration)
    if credential_manager is not None:
        credential_manager["apimServiceDeletionVerified"] = apim.get("apimDeletionVerified") is True
        result["credentialManager"] = credential_manager
    return result


def _tombstone(configuration: dict, current: dict, key: str) -> str:
    """元のサービスIDとリージョンから、論理削除データの正確なIDを求める。"""
    name = _parts(current["serviceIds"][key])[-1]
    prefix = f"/subscriptions/{configuration['subscriptionId']}/providers/"
    location = current["locations"][key]
    if key == "keyVault":
        return prefix + f"Microsoft.KeyVault/locations/{location}/deletedVaults/{name}"
    if key == "foundry":
        group = configuration["resourceGroupNames"]["svc"]
        return prefix + f"Microsoft.CognitiveServices/locations/{location}/resourceGroups/{group}/deletedAccounts/{name}"
    return prefix + f"Microsoft.ApiManagement/locations/{location}/deletedservices/{name}"


def _api_center_retention(configuration: dict, state: Mapping[str, Any], transport: Transport) -> dict:
    """API Centerの削除履歴を読み、同名予約の残留を記録する。変更要求は送らない。

    個別の削除済みGETではなく、今回のsvc RGの一覧を全ページ確認する。
    名前はBicep出力から取得し、期限を確認できない状態を完全削除とは扱わない。
    """
    outputs = state.get("outputs")
    names = outputs.get("names") if isinstance(outputs, Mapping) else None
    name = names.get("apiCenter") if isinstance(names, Mapping) else None
    if not isinstance(name, str) or name.casefold() != ("apic-" + configuration["resourceStem"]).casefold():
        _error("api-center-retention-unverified", "API Center名のBicep出力を確認できません。")
    scope = _rg_id(configuration, "svc") + "/providers/Microsoft.ApiCenter"
    collection = scope + "/deletedServices"
    target_id = collection + "/" + name
    result = {"resourceId": scope + "/services/" + name, "deletedResourceId": target_id,
              "status": "absent", "lookup": "resource-group-deletedServices-list", "purgeRequested": False}
    next_page = collection + "?api-version=" + API["apiCenter"]
    visited, seen_ids = set(), set()
    while next_page:
        try:
            parsed = urlsplit(next_page)
            if (not isinstance(next_page, str) or not next_page or parsed.fragment
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.scheme not in ("", "https")
                or parsed.netloc and parsed.hostname != "management.azure.com"
                or parsed.path.casefold() != collection.casefold()
                or parse_qs(parsed.query).get("api-version") != [API["apiCenter"]]):
                raise ValueError()
            path = parsed.path + "?" + parsed.query
        except (ValueError, AttributeError, TypeError):
            _error("api-center-retention-unverified", "API Center削除履歴の継続先を確認できません。", collection)
        if path in visited or len(visited) >= 100:
            _error("api-center-retention-unverified", "API Center削除履歴が循環または上限に達しました。", collection)
        visited.add(path)
        read = _request(transport, "GET", collection, API["apiCenter"], path=path)
        if read["statusCode"] == 404:
            # RGの消滅後は一覧URLも404になり得る。個別資源の不存在とは同一視しない。
            return {**result, "status": "unverified", "listReadVerified": False, "httpStatus": 404,
                    "reasonCode": "api-center-deleted-list-not-readable",
                    "reason": "API Center削除履歴の一覧が404のため、同名予約の有無は未確認です。"}
        page = _expect_read(read, collection)
        if page is None or not isinstance(page.get("value"), list):
            _error("api-center-retention-unverified", "API Center削除履歴を取得できず、残留なしとは判定していません。", collection)
        for item in page["value"]:
            item_name = item.get("name") if isinstance(item, Mapping) else None
            item_id = item.get("id") if isinstance(item, Mapping) else None
            if (not isinstance(item_name, str) or re.fullmatch(r"[a-zA-Z0-9-]{3,90}", item_name) is None
                or not isinstance(item_id, str) or item_id.casefold() != (collection + "/" + item_name).casefold()
                or str(item.get("type", "")).casefold() != "microsoft.apicenter/deletedservices"
                or item_id.casefold() in seen_ids):
                _error("api-center-retention-mismatch", "API Center削除履歴のID・名前・種類が不正です。", collection)
            seen_ids.add(item_id.casefold())
            if item_id.casefold() == target_id.casefold():
                properties = item.get("properties")
                try:
                    deleted_at = properties["softDeletionDate"]
                    purge_at = properties["scheduledPurgeDate"]
                    deleted_time = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
                    purge_time = datetime.fromisoformat(purge_at.replace("Z", "+00:00"))
                    if deleted_time.utcoffset() is None or purge_time.utcoffset() is None or purge_time < deleted_time:
                        raise ValueError()
                except (ValueError, TypeError, KeyError, AttributeError):
                    _error("api-center-retention-unverified", "API Center削除履歴の保持期限を確認できません。", target_id)
                result.update({"status": "retained-soft-delete", "softDeletionDate": deleted_at, "scheduledPurgeDate": purge_at})
        next_page = page.get("nextLink")
        if next_page is not None and (not isinstance(next_page, str) or not next_page):
            _error("api-center-retention-unverified", "API Center削除履歴の継続先が不正です。", collection)
    return {**result, "listReadVerified": True}


def _credential_manager_release(configuration: dict) -> dict | None:
    """公開資源の消失から、観測できない内部storeの解放を推定しない。

    provider削除・親APIM削除・墓標purgeは正式な公開操作だが、内部accountの
    解放確認APIではない。設定件数も実在件数や解放済み件数として扱わない。
    """
    from .credential_pools import physical_providers
    count = len(physical_providers(configuration))
    if not count:
        return None
    return {"status": "unverified", "configuredProviderCount": count,
            "internalAccountReleaseVerified": False,
            "sameNameCredentialRecreationVerified": False,
            "reasonCode": "no-public-internal-account-release-contract",
            "nextAction": "microsoft-support-if-same-name-account-conflict"}


def post_down(config_path: str | Path, state: Mapping[str, Any], transport: Transport, *,
              max_checks: int = 30, wait_seconds: float = 2,
              sleep: Callable[[float], None] = time.sleep) -> dict:
    """所有5RGの消滅確認後、今回の論理削除データだけを照合して完全削除する。

    Key Vaultのpurge保護が有効なら解除せず残す。削除済み一覧を一括消去せず、
    元の配備記録と一致するサービスだけを対象にする。
    """
    _options(max_checks, wait_seconds)
    configuration = load_configuration(config_path)
    current = _validate_state(configuration, state)
    application_infrastructure = _application_infrastructure(configuration, state.get('applicationInfrastructure', []))
    group_results = []
    for kind in ("id", "nw", "monitor", "svc", "maint"):
        group_id = _rg_id(configuration, kind)
        response = _request(transport, "GET", group_id, API["groups"])
        if response["statusCode"] != 404:
            _error("resource-groups-remain", "対象の5RGが削除済みではありません。完全削除は実行していません。", group_id)
        group_results.append({"resourceId": group_id, "status": "absent", "httpStatus": 404})

    infrastructure_results = []
    for item in application_infrastructure:
        group_id = item['resourceGroupId']
        # 管理RGの削除はContainer Apps RPに任せる。記録したIDの読取だけで待機し、
        # 自分でME-*を探索・DELETEしたり、RGが残ったまま完全撤去と報告したりしない。
        for attempt in range(max_checks):
            resource = _expect_read(_request(transport, 'GET', group_id, API['groups']), group_id)
            if resource is None:
                break
            if attempt + 1 < max_checks:
                sleep(wait_seconds)
        else:
            _error('application-infrastructure-remains',
                   'Container Appsの管理ネットワークRGが残っています。Azureによる削除完了を確認して再実行してください。', group_id)
        infrastructure_results.append({**item, 'status': 'absent', 'httpStatus': 404})

    # 論理削除データを消す前に、通常のRG撤去が完了していることを確認する。
    # 一覧から名前が似た資源を探して一括削除する方法は使わない。
    service_results = []
    for key, original_id in current["serviceIds"].items():
        tombstone_id = _tombstone(configuration, current, key)
        deleted = _expect_read(_request(transport, "GET", tombstone_id, API[key]), tombstone_id)
        if deleted is None:
            service_results.append({"kind": key, "resourceId": tombstone_id, "status": "absent", "httpStatus": 404})
            continue
        if not _same(deleted.get("id"), tombstone_id):
            _error("tombstone-mismatch", "削除済み資源のIDが今回の配備記録と一致しません。", tombstone_id)
        properties = deleted.get("properties", {})
        source_key = "vaultId" if key == "keyVault" else "serviceId" if key == "apim" else None
        if source_key and not _same(properties.get(source_key), original_id):
            _error("tombstone-mismatch", "同名の削除済み資源の元Resource IDが異なるため完全削除しません。", tombstone_id)
        if (key == "keyVault" and configuration["services"]["vaultPurgeProtection"]
            or properties.get("purgeProtectionEnabled") is True or properties.get("enablePurgeProtection") is True):
            service_results.append({"kind": key, "resourceId": tombstone_id, "status": "retained-protection", "scheduledPurgeDate": properties.get("scheduledPurgeDate")})
            continue
        purge_id = tombstone_id + "/purge" if key == "keyVault" else tombstone_id
        _request(transport, "POST" if key == "keyVault" else "DELETE", purge_id, API[key])
        _wait_absent(transport, tombstone_id, API[key], max_checks=max_checks, wait_seconds=wait_seconds, sleep=sleep)
        service_results.append({"kind": key, "resourceId": tombstone_id, "status": "absent", "httpStatus": 404})

    # RG消滅後は、そのRGの削除履歴一覧自体が読めない場合があります。
    # 他3サービスの必須purgeを先に完了し、API Centerの確認不能は別項目へ記録します。
    # 404や権限不足を「完全削除済み」や「永久残留」と決め付けず、RGも再作成しません。
    try:
        api_center_retention = _api_center_retention(configuration, state, transport)
    except CleanupError as error:
        api_center_retention = {"status": "unverified", "reasonCode": error.code,
                                "reason": str(error), "lookupResourceId": error.resource_id,
                                "listReadVerified": False, "purgeRequested": False}

    retained = any(item["status"].startswith("retained") for item in service_results)
    status = ("complete-with-unverified-api-center-retention" if api_center_retention["status"] == "unverified"
              else "complete-with-protected-retention" if retained else "complete-with-api-center-retention"
              if api_center_retention["status"] == "retained-soft-delete" else "complete")
    result = {"phase": "postdown", "status": status, "resourceGroups": group_results,
              "applicationInfrastructure": infrastructure_results,
              "softDeletedServices": service_results, "apiCenterRetention": api_center_retention}
    credential_manager = _credential_manager_release(configuration)
    if credential_manager is not None:
        apim = next((item for item in service_results if item["kind"] == "apim"), {})
        credential_manager["apimSoftDeleteAbsenceVerified"] = apim.get("status") == "absent"
        # RGと墓標の確認結果を保持し、内部accountの未確認をcompleteへ吸収しない。
        result.update(status="resources-absent-with-unverified-credential-manager-release",
                      resourceCleanupStatus=status, credentialManager=credential_manager)
    return result

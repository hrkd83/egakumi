"""APIMの公開入口と所有者チケットの資格情報を準備し、配備結果を照合する。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from urllib.parse import parse_qsl, unquote, urlparse

from .apim_naming import apim_name
from .paths import local_paths


API = "2024-05-01"
HTTP2 = "Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2"
SECRET_NAME = "core-l7-ingress-secret"
TICKET_NAME = "core-realtime-ticket-key"


def require_shared_ingress(config: dict, outputs: dict) -> None:
    """通常HTTP(S)入口保護を配備した出力か照合する。通信は行わない。"""
    host = config.get("gateway", {}).get("publicHostName")
    applied = outputs.get("ingressSecrets", {})
    if (not isinstance(host, str) or not host
            or not isinstance(applied, dict) or applied.get("enabled") is not True
            or applied.get("l7IngressEnabled") is not True
            or not isinstance(applied.get("publicHostName"), str)
            or applied["publicHostName"].casefold() != host.casefold()):
        raise ValueError("The permanent HTTP(S) ingress protection must be provisioned before API publication; update the Core first")


def realtime_enabled(config: dict) -> bool:
    """Realtimeの所有者チケットを使う制御APIが選択されているか読む。"""
    return config.get("gateway", {}).get("realtimeControlEnabled", False) is True or any(entry.get("kind") in ("realtimeCreate", "realtimeHangup")
               for api in config.get("apiDefinitions", [])
               for entry in api.get("authorization", {}).get("operationConstraints", {}).values())


def owner_ticket_enabled(config: dict) -> bool:
    """Realtimeまたは文書ジョブの所有者を署名で確認する機能の有無を読む。"""
    definitions = config.get("infrastructureApiDefinitions")
    if definitions is None:
        # 空の公開API配列は標準APIの後続公開を意味します。CoreのBicep入力と
        # 同じ正本から文書機能を調べ、公開前の鍵準備を漏らしません。
        from catalog.standard_apis import infrastructure_api_definitions
        definitions = infrastructure_api_definitions(config.get("apiDefinitions", []))
    return realtime_enabled(config) or any(
        entry.get("kind") in ("documentAnalysis", "documentAnalysisStatus")
        for api in definitions
        for entry in api.get("authorization", {}).get("operationConstraints", {}).values()
    )


def secret_path(config: dict, root: Path, purpose="l7-ingress") -> Path:
    # macOSの/varのようなOS側リンクは信頼した作業ルートの解決時だけ許す。
    # 環境別のsecretsや資格情報ファイルがリンクなら、別ファイルを開かないよう拒否する。
    """環境と用途ごとに分離したローカル資格情報ファイルの場所を求める。"""
    return local_paths(config, root).secrets / (purpose + "-" + config["resourceStem"] + ".json")


def prepare_secret(config: dict, root: Path, write_protected) -> dict:
    """選択された機能にだけ、入口用とチケット署名用の独立した資格情報を用意する。"""
    result = {"l7IngressSecret": prepare_credential(config, root, "l7-ingress", write_protected, record_registration=True)}
    if owner_ticket_enabled(config):
        result["realtimeTicketKey"] = prepare_credential(config, root, "realtime-ticket", write_protected, record_registration=True)
    return result


def _read_credential(config: dict, path: Path, *, verify_registration=False) -> dict | None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("L7 ingress credential path must not use symlinks")
    if not path.exists():
        return None
    if not path.is_file() or (os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != 0o600):
        raise ValueError("L7 ingress credential requires a private regular file")
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("L7 ingress credential file is invalid") from None
    if not isinstance(current, dict) or any(current.get(key) != config[key]
            for key in ("subscriptionId", "azureTenantId", "resourceStem")):
        raise ValueError("L7 ingress credential belongs to a different environment")
    value = current.get("value")
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{128}", value):
        raise ValueError("L7 ingress credential file is invalid")
    generation = current.get("generation")
    if verify_registration and ("generation" in current or "localIntegrity" in current):
        if (not isinstance(generation, str) or re.fullmatch(r"[a-f0-9]{32}", generation) is None
                or current.get("localIntegrity") != hashlib.sha256(value.encode("ascii")).hexdigest()):
            raise ValueError("Ingress credential provenance is invalid; restore the protected file")
    return current


def prepare_credential(config: dict, root: Path, purpose: str, write_protected, *, record_registration=False) -> str:
    """Keep the current credential and record ingress registration only for its owning caller."""
    path = secret_path(config, root, purpose)
    current = _read_credential(config, path, verify_registration=record_registration)
    created = current is None
    if created:
        current = {key: config[key] for key in ("subscriptionId", "azureTenantId", "resourceStem")}
        current["value"] = secrets.token_hex(64)
    record_generation = record_registration and "generation" not in current
    if record_generation:
        current["generation"] = secrets.token_hex(16)
        # The integrity value stays exclusively in the protected source file.
        current["localIntegrity"] = hashlib.sha256(current["value"].encode("ascii")).hexdigest()
    if created or record_generation:
        write_protected(path, current)
    return current["value"]


def plan_secret_registration(config, root, transport, credentials):
    """Identify initial ARM writes using only the two exact child resources' metadata."""
    plan = {}
    for purpose, parameter, name in (
        ("l7-ingress", "l7IngressSecret", SECRET_NAME),
        ("realtime-ticket", "realtimeTicketKey", TICKET_NAME),
    ):
        if parameter not in credentials:
            continue
        current = _read_credential(config, secret_path(config, root, purpose), verify_registration=True)
        if current is None or not current.get("generation") or current["value"] != credentials[parameter]:
            raise ValueError("The owned ingress credential source could not be verified")
        identity = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}"
                    f"/providers/Microsoft.KeyVault/vaults/kv-{config['resourceStem']}/secrets/{name}")
        result = transport.request("GET", identity + "?api-version=2024-11-01")
        status, body = result.get("statusCode"), result.get("body")
        if type(status) is int and status == 404:
            plan[parameter] = current["generation"]
            continue
        if (type(status) is not int or status != 200 or not isinstance(body, dict)
                or str(body.get("id", "")).casefold() != identity.casefold()
                or str(body.get("type", "")).casefold() != "microsoft.keyvault/vaults/secrets"
                or not isinstance(body.get("tags"), dict)
                or body["tags"].get("egakumi-ingress-generation") != current["generation"]
                or not isinstance(body.get("properties"), dict)
                or not isinstance(body["properties"].get("attributes"), dict)
                or body["properties"]["attributes"].get("enabled") is not True):
            raise ValueError("Ingress secret registration differs; restore the protected source or rebuild the environment")
        # A matching, enabled registration needs no PUT or additional version.
    return plan


def remove_secret(config: dict, root: Path) -> None:
    """所有5RGの消滅確認後にだけ、対象環境のローカル入口資格情報を削除する。"""
    for purpose in ("l7-ingress", "realtime-ticket"):
        path = secret_path(config, root, purpose)
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("Ingress credential path must not use symlinks")
        path.unlink(missing_ok=True)


def _ok(response: dict, operation: str, allowed=(200,)) -> dict:
    """想定HTTP状態を検査し、秘密を含む応答本文をエラー表示せずbodyを返す。"""
    if response.get("statusCode") not in allowed:
        raise ValueError(operation + " failed; review the scoped Azure operation (private output suppressed)")
    return response.get("body") or {}


def apim_name_matches(config: dict, name: str) -> bool:
    """環境・製品・構築世代から決めた APIM 名一つだけを認める。

    Bicep と同じ確定名を組み立て、文字列全体を照合する。
    接頭辞だけの一致や、別の構築世代の名前は受け付けない。
    呼出側ではこの名前に加え、Subscription と svc RG の完全 ID も照合する。
    """
    expected = apim_name(config)
    return isinstance(name, str) and name.casefold() == expected.casefold()


def _own_apim(config: dict, outputs: dict) -> tuple[str, dict]:
    """配備出力のAPIM IDを命名・配置先・撤去記録と照合する。"""
    require_shared_ingress(config, outputs)
    feature = outputs["ingressSecrets"]
    apim_id = feature.get("apimId", "")
    prefix = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}/providers/Microsoft.ApiManagement/service/"
    if (not isinstance(apim_id, str) or not apim_id.casefold().startswith(prefix.casefold())
            or not apim_name_matches(config, apim_id[len(prefix):])):
        raise ValueError("Ingress bootstrap targeted a different API Management service")
    cleanup_id = outputs.get("cleanup", {}).get("serviceResourceIds", {}).get("apim")
    if not isinstance(cleanup_id, str) or cleanup_id.lower() != apim_id.lower():
        raise ValueError("Ingress bootstrap service differs from cleanup ownership")
    return apim_id, feature


def _completion_target(target: str, apim_id: str, location: str | None, refresh_path: str | None) -> bool:
    """ARM が返した完了先を、同じ APIM または同じサブスクリプション・地域の正式な形式に限る。

    APIM の PATCH は、リソース配下ではなく地域別 operationResults を返すこともある。
    任意の ARM リソースへ GET しないよう、正式な完了先のパスとクエリーだけを認める。
    URL 自体はエラーに表示しない。トークン等を含む不正な URL がログへ残るのを防ぐ。
    """
    if len(target) > 8192 or any(ord(char) <= 32 or ord(char) == 127 for char in target) or "#" in target:
        return False
    try:
        uri = urlparse(target)
        pairs = parse_qsl(uri.query, keep_blank_values=True)
    except ValueError:
        return False
    if (uri.scheme != "https" or uri.netloc.lower() not in ("management.azure.com", "management.azure.com:443")
        or uri.params or uri.fragment or len({key for key, _ in pairs}) != len(pairs)):
        return False
    query = dict(pairs)
    if query.get("api-version") != API:
        return False
    # ID は UUID だけとは限らず、Service Update の公式例には Base64 の ID もある。
    # 単一の短いセグメントに限定し、エンコードされた /・\\・パス遡及は受け付けない。
    def operation_id(value: str) -> bool:
        decoded = unquote(value)
        return re.fullmatch(r"[A-Za-z0-9_+=.-]{1,2048}", decoded) is not None and decoded not in (".", "..")

    service_match = re.fullmatch(re.escape(apim_id) + r"/(?:tenant/)?operationResults/([^/]+)", uri.path, re.IGNORECASE)
    subscription = re.fullmatch(r"/subscriptions/([0-9a-f-]{36})/resourceGroups/[^/]+/providers/Microsoft\.ApiManagement/service/[^/]+", apim_id, re.IGNORECASE)
    regional_match = None
    if subscription and isinstance(location, str) and re.fullmatch(r"[a-z0-9]+", location, re.IGNORECASE):
        regional_prefix = f"/subscriptions/{subscription[1]}/providers/Microsoft.ApiManagement/locations/{location}/operationResults/"
        regional_match = re.fullmatch(re.escape(regional_prefix) + r"([^/]+)", uri.path, re.IGNORECASE)
    match = service_match or regional_match
    if match:
        return (operation_id(match[1]) and set(query) <= {"api-version", "asyncResponse"}
                and query.get("asyncResponse", "") == "")
    # Named Value の Location フォールバックだけは、直前に更新した同じ Secret を GET する。
    # https://learn.microsoft.com/rest/api/apimanagement/named-value/refresh-secret
    same_refresh = (isinstance(refresh_path, str) and uri.path.lower() == refresh_path.lower()
                    and re.fullmatch(re.escape(apim_id) + r"/namedValues/[A-Za-z0-9._-]+/refreshSecret", refresh_path, re.IGNORECASE) is not None)
    # 現行サービスは Azure-AsyncOperation に同じ refreshSecret と azure-asyncId、
    # Location には asyncId と最終応答コード 200/201 を返す。どちらも同じ対象へ限定する。
    return same_refresh and (
        (set(query) == {"api-version", "azure-asyncId"} and operation_id(query["azure-asyncId"]))
        or (set(query) == {"api-version", "asyncId", "asyncCode"}
            and operation_id(query["asyncId"]) and query["asyncCode"] in ("200", "201")))


def _poll_operation(response: dict, apim_id: str, transport, *, sleep, deadline, location=None, refresh_path=None) -> None:
    """直前の ARM 応答が返した、所有範囲内の正式な完了 URL を同じまま追跡する。"""
    if response.get("statusCode") != 202:
        _ok(response, "Complete API Management operation", (200, 201, 204))
        return
    headers = {str(key).lower(): value for key, value in response.get("headers", {}).items()}
    candidates = [headers.get("azure-asyncoperation"), headers.get("location")]
    if not any(isinstance(target, str) for target in candidates):
        raise ValueError("API Management operation has no scoped completion link")
    # Named Value refresh uses Location as its final-state URL. Some APIM
    # responses also contain a signed Azure-AsyncOperation URL outside the
    # accepted path/query forms. Prefer that monitor only when it passes the
    # same scope checks; otherwise follow the validated Location unchanged.
    target = next((target for target in candidates if isinstance(target, str)
                   and _completion_target(target, apim_id, location, refresh_path)), None)
    if target is None:
        raise ValueError("API Management operation completion link is outside this service")
    while time.monotonic() < deadline:
        observed = transport.request("GET", target)
        body = _ok(observed, "Read API Management operation", (200, 202, 204))
        status = str(body.get("status", "")).lower()
        if status in ("failed", "canceled", "cancelled"):
            raise ValueError("API Management operation did not succeed")
        if observed["statusCode"] in (200, 204) and status not in ("inprogress", "running", "accepted"):
            return
        sleep(10)
    raise ValueError("API Management operation is still pending; no additional infrastructure was created")


def _ready_service(apim_id: str, transport, *, sleep, deadline) -> dict:
    """所有 APIM の更新完了を待ち、再開時に進行中の設定へ PATCH を重ねない。

    非同期の完了 URL が成功しても、本体の読取結果への反映が遅れることがある。
    初回の変更判断と最後の確認で同じ期限を使い、待機のたびに期限を延長しない。
    正常な更新中の二状態だけを待つ。それ以外の失敗・削除中・未知状態は停止する。
    """
    explained_wait = False
    while True:
        response = transport.request("GET", apim_id + "?api-version=" + API)
        body = _ok(response, "Read API Management readiness")
        state = str(body.get("properties", {}).get("provisioningState", "")).lower()
        if state == "succeeded":
            return response
        if state not in ("updating", "activating"):
            raise ValueError("API Management service is not ready; inspect its provisioning state (private output suppressed)")
        if not explained_wait:
            # ドメイン反映はAzure側で15分以上かかり、DeveloperではAPI通信も中断し得ます。
            # 待機を再送・再構築と混同しないよう、同じ更新を観測していることを一度案内します。
            print("Waiting for the existing API Management infrastructure update. Custom domain changes can take 15 minutes or longer; Developer tier API traffic can be interrupted. Do not restart azd up while this update is pending.", flush=True)
            explained_wait = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("API Management service is still pending; no additional update was requested")
        sleep(min(10, remaining))


def _native_service_properties(body: dict, apim_id: str, native_host: str) -> dict:
    """所有APIMの既定HTTPS URLとInternal VNetを実応答で照合する。"""
    if (not isinstance(body, dict)
            or str(body.get("id", "")).casefold() != apim_id.casefold()
            or str(body.get("name", "")).casefold() != apim_id.rsplit("/", 1)[-1].casefold()
            or str(body.get("type", "")).casefold() != "microsoft.apimanagement/service"):
        raise ValueError("Ingress endpoint response identifies a different API Management service")
    properties = body.get("properties")
    if (not isinstance(properties, dict) or properties.get("virtualNetworkType") != "Internal"
            or not isinstance(properties.get("gatewayUrl"), str)
            or properties["gatewayUrl"].casefold() != "https://" + native_host.casefold()):
        raise ValueError("Ingress endpoint requires the owned internal API Management native HTTPS gateway")
    custom = properties.get("customProperties", {})
    if not isinstance(custom, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in custom.items()):
        raise ValueError("API Management custom properties cannot be preserved")
    return properties


def _refresh_secret(apim_id: str, name: str, vault_name: str, transport, *, sleep, deadline, location=None) -> None:
    """Key Vault参照の再読込を要求し、正しいSecretとMIで成功したか読み直す。"""
    named_id = apim_id + "/namedValues/" + name
    refresh = transport.request("POST", named_id + "/refreshSecret?api-version=" + API)
    _poll_operation(refresh, apim_id, transport, sleep=sleep, deadline=deadline,
                    location=location, refresh_path=named_id + "/refreshSecret")
    named = _ok(transport.request("GET", named_id + "?api-version=" + API), "Verify L7 ingress named value")
    kv = named.get("properties", {}).get("keyVault", {})
    expected_secret = f"https://{vault_name}.vault.azure.net/secrets/{name}"
    if (kv.get("secretIdentifier", "").rstrip("/") != expected_secret or kv.get("identityClientId") is not None
        or str(kv.get("lastStatus", {}).get("code", "")).lower() != "success"):
        raise ValueError("Ingress named value refresh was not confirmed")


def verify_endpoint(config: dict, outputs: dict, transport, *, sleep=time.sleep, timeout_seconds=3600) -> dict:
    """所有APIMの通常HTTPSとBicepで指定した受信HTTP/2の設定を読み戻す。"""
    apim_id, feature = _own_apim(config, outputs)
    native_host = apim_name(config) + ".azure-api.net"
    reported_host = feature.get("apimGatewayHost")
    if not isinstance(reported_host, str) or reported_host.casefold() != native_host.casefold():
        raise ValueError("Ingress endpoint output identifies a different native API Management gateway")
    deadline = time.monotonic() + timeout_seconds
    while True:
        response = _ready_service(apim_id, transport, sleep=sleep, deadline=deadline)
        service = _ok(response, "Verify API Management native endpoint")
        properties = _native_service_properties(service, apim_id, native_host)
        if properties.get("customProperties", {}).get(HTTP2) == "false":
            return {"apimGatewayHost": native_host, "enabled": True, "l7IngressEnabled": True,
                    "apimHttp2": "false", "status": "management-plane-configured", "runtimeVerified": False,
                    "trustBoundary": "AppGW HTTP(S) rewrite and APIM ingress validation"}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("API Management endpoint/HTTP2 readback is still pending; verify the Core deployment")
        sleep(min(10, remaining))


def configure_named_values(config: dict, outputs: dict, transport, *, sleep=time.sleep, timeout_seconds=600) -> dict:
    """Gateway資材の反映後に、入口・チケット鍵のKey Vault参照を確認する。"""
    realtime_on = realtime_enabled(config)
    ticket_on = owner_ticket_enabled(config)
    apim_id, _ = _own_apim(config, outputs)
    vault_id = outputs.get("cleanup", {}).get("serviceResourceIds", {}).get("keyVault", "")
    prefix = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}/providers/Microsoft.KeyVault/vaults/"
    if not isinstance(vault_id, str) or not vault_id.lower().startswith(prefix.lower()) or '/' in vault_id[len(prefix):] or not re.fullmatch(r"kv-[a-z0-9-]+", vault_id[len(prefix):]):
        raise ValueError("Ingress secrets targeted a different Key Vault")
    vault_name = vault_id.rsplit("/", 1)[-1]
    deadline = time.monotonic() + timeout_seconds
    names = [SECRET_NAME] + ([TICKET_NAME] if ticket_on else [])
    for name in names:
        _refresh_secret(apim_id, name, vault_name, transport, sleep=sleep, deadline=deadline, location=config.get("location"))
    return {"enabled": True, "l7IngressEnabled": True, "realtimeEnabled": realtime_on,
            "ownerTicketEnabled": ticket_on,
            "status": "management-plane-configured", "runtimeVerified": False,
            "trustBoundary": "AppGW HTTP(S) rewrite and APIM ingress validation"}

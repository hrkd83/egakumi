"""通常の構築後に、gRPC・Realtime用の入口設定を仕上げる。

設定された機能にだけ、互いに独立したランダムな資格情報をローカルに準備し、
構築後に今回のAPIM・Key Vault・マネージドIDと一致することを照合する。
VMでの証明書発行より先にAPIM本体が必要なため、証明書発行後にAPIMの
カスタムドメイン・HTTP/2設定の反映をAzure ARMで確認する。
Named ValueのKey Vault参照は、Gateway資材を配備した後に別途確認する。
VMへAPIMの管理権限を与える必要はない。既存の別ドメイン設定は保持する。
prepareはローカルファイル操作、configure_endpointはAzure設定変更である。
結果は管理面の設定確認であり、gRPCの疎通やWebRTC通話の合格とは別に扱う。
Graph操作や追加の試験専用基盤は作らない。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from urllib.parse import parse_qsl, unquote, urlparse

from .paths import local_paths


API = "2024-05-01"
HTTP2 = "Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2"
SECRET_NAME = "core-l7-ingress-secret"
TICKET_NAME = "core-realtime-ticket-key"


def enabled(config: dict) -> bool:
    """公開gRPC用のTCP/HTTP2入口が選択されているか読む。"""
    return config.get("gateway", {}).get("grpc", {}).get("enabled", False) is True


def realtime_enabled(config: dict) -> bool:
    """Realtimeの所有者チケットを使う制御APIが選択されているか読む。"""
    return config.get("gateway", {}).get("realtimeControlEnabled", False) is True or any(entry.get("kind") in ("realtimeCreate", "realtimeHangup")
               for api in config.get("apiDefinitions", [])
               for entry in api.get("authorization", {}).get("operationConstraints", {}).values())


def secret_path(config: dict, root: Path, purpose="l7-ingress") -> Path:
    # macOSの/varのようなOS側リンクは信頼した作業ルートの解決時だけ許す。
    # 環境別のsecretsや資格情報ファイルがリンクなら、別ファイルを開かないよう拒否する。
    """環境と用途ごとに分離したローカル資格情報ファイルの場所を求める。"""
    return local_paths(config, root).secrets / (purpose + "-" + config["resourceStem"] + ".json")


def prepare_secret(config: dict, root: Path, write_protected) -> dict:
    """選択された機能にだけ、入口用とチケット署名用の独立した資格情報を用意する。"""
    result = {}
    for active, purpose, parameter in ((enabled(config), "l7-ingress", "l7IngressSecret"),
                                       (realtime_enabled(config), "realtime-ticket", "realtimeTicketKey")):
        if active:
            result[parameter] = _prepare_credential(config, root, purpose, write_protected)
    return result


def _prepare_credential(config: dict, root: Path, purpose: str, write_protected) -> str:
    """既存の所有者限定ファイルを照合して再利用し、なければ暗号学的乱数で生成する。"""
    path = secret_path(config, root, purpose)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("L7 ingress credential path must not use symlinks")
    identity = {key: config[key] for key in ("subscriptionId", "azureTenantId", "resourceStem")}
    if path.exists():
        if not path.is_file() or (os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != 0o600):
            raise ValueError("L7 ingress credential requires a private regular file")
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("L7 ingress credential file is invalid") from None
        if not isinstance(current, dict) or any(current.get(key) != value for key, value in identity.items()):
            raise ValueError("L7 ingress credential belongs to a different environment")
        value = current.get("value")
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{128}", value):
            raise ValueError("L7 ingress credential file is invalid")
    else:
        value = secrets.token_hex(64)
        write_protected(path, {**identity, "value": value})
    return value


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
    """環境と製品から決めた APIM 名一つだけを認める。

    Bicep と同じ「apim-環境名-プロダクト名」を組み立て、文字列全体を照合する。
    接頭辞だけの一致や、以前のランダム末尾・競合回避用の別名は受け付けない。
    呼出側ではこの名前に加え、Subscription と svc RG の完全 ID も照合する。
    """
    expected = "apim-" + config["resourceStem"]
    return isinstance(name, str) and name.casefold() == expected.casefold()


def _own_apim(config: dict, outputs: dict) -> tuple[str, dict]:
    """配備出力のAPIM IDを命名・配置先・撤去記録と照合する。"""
    grpc = outputs.get("grpc" if enabled(config) else "ingressSecrets", {})
    if not isinstance(grpc, dict) or grpc.get("enabled") is not True:
        raise ValueError("gRPC bootstrap outputs are missing")
    apim_id = grpc.get("apimId", "")
    prefix = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}/providers/Microsoft.ApiManagement/service/"
    if (not isinstance(apim_id, str) or not apim_id.casefold().startswith(prefix.casefold())
            or not apim_name_matches(config, apim_id[len(prefix):])):
        raise ValueError("gRPC bootstrap targeted a different API Management service")
    cleanup_id = outputs.get("cleanup", {}).get("serviceResourceIds", {}).get("apim")
    if not isinstance(cleanup_id, str) or cleanup_id.lower() != apim_id.lower():
        raise ValueError("gRPC bootstrap service differs from cleanup ownership")
    return apim_id, grpc


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
    target = headers.get("azure-asyncoperation") or headers.get("location")
    if not isinstance(target, str):
        raise ValueError("API Management operation has no scoped completion link")
    if not _completion_target(target, apim_id, location, refresh_path):
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


def _configure_domain(config: dict, grpc: dict, outputs: dict, apim_id: str, transport, *, sleep, deadline) -> None:
    """発行済みの同一ホスト証明書をAPIMへ結び、他のドメインを残してHTTP/2を有効にする。"""
    host = config["gateway"]["grpc"]["publicHostName"]
    if host != config["gateway"]["publicHostName"] or grpc.get("publicHostName") != host:
        raise ValueError("gRPC hostname must match the certificate prepared for this environment")
    uri = urlparse(grpc.get("certificateSecretUri", ""))
    vault_id = outputs.get("cleanup", {}).get("serviceResourceIds", {}).get("keyVault", "")
    expected_vault = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}"
                      f"/providers/Microsoft.KeyVault/vaults/kv-{config['resourceStem']}")
    if not isinstance(vault_id, str) or vault_id.casefold() != expected_vault.casefold():
        raise ValueError("gRPC certificate targeted a different Key Vault")
    vault_name = vault_id.rsplit("/", 1)[-1] if isinstance(vault_id, str) else ""
    if (uri.scheme != "https" or uri.hostname != vault_name + ".vault.azure.net" or uri.port not in (None, 443)
        or uri.username is not None or uri.password is not None or uri.query or uri.fragment
        or uri.path.rstrip("/") != "/secrets/" + config["certificate"]["certificateName"]):
        raise ValueError("gRPC certificate must use this environment's versionless Key Vault certificate secret")
    # Key Vault firewall 有効時の APIM UAMI 非対応を避け、APIM 自身の ID を使う。
    # null を明示して、以前の UAMI 指定が残った同一ドメインも更新する。
    # https://learn.microsoft.com/azure/api-management/api-management-howto-use-managed-service-identity
    # 公開ドメインを、SNI のない接続でも選択する既定の証明書にします。
    # Azure はこの構成を true として読み返すため、false を要求し続けると、
    # 設定済みでも再 PATCH を繰り返します。他のドメイン設定は下の kept で保持します。
    desired = {"type": "Proxy", "hostName": host, "keyVaultId": uri.geturl().rstrip("/"),
               "identityClientId": None, "defaultSslBinding": True, "negotiateClientCertificate": False}
    current_response = _ready_service(apim_id, transport, sleep=sleep, deadline=deadline)
    current = _ok(current_response, "Read API Management custom domains")
    properties = current.get("properties", {})
    if properties.get("virtualNetworkType") != "Internal":
        raise ValueError("gRPC bootstrap requires the owned internal API Management gateway")
    domains = properties.get("hostnameConfigurations", [])
    if not isinstance(domains, list) or any(not isinstance(item, dict) for item in domains):
        raise ValueError("API Management custom domain inventory is invalid")
    own = [item for item in domains if item.get("type") == "Proxy" and str(item.get("hostName", "")).lower() == host.lower()]
    if len(own) > 1 or any(str(item.get("keyVaultId", "")).rstrip("/") not in ("", desired["keyVaultId"]) for item in own):
        raise ValueError("An existing API Management hostname has a different certificate owner")
    changed = (not own or any(own[0].get(key) != value for key, value in desired.items())
               or str(properties.get("customProperties", {}).get(HTTP2, "")).lower() != "true")
    # APIM作成時はまだ証明書がないため、発行後にこの対象ドメインだけを反映する。
    # 別のカスタムドメインや既存customPropertiesは保持してPATCHする。
    if changed:
        kept = [copy.deepcopy(item) for item in domains if item not in own]
        custom = {**properties.get("customProperties", {}), HTTP2: "true"}
        headers = {}
        etag = current.get("etag") or next((value for key, value in current_response.get("headers", {}).items() if key.lower() == "etag"), None)
        if isinstance(etag, str) and etag and etag.strip('"') != "*":
            headers["If-Match"] = etag
        operation = transport.request("PATCH", apim_id + "?api-version=" + API,
            {"properties": {"hostnameConfigurations": kept + [desired], "customProperties": custom}}, headers=headers)
        _ok(operation, "Update API Management custom domain", (200, 201, 202, 204))
    # サービス更新の完了 URL は Azure 側で形式が変わる場合があるため追いません。
    # 今回所有する APIM 本体だけを GET し、進行状態と設定値の両方で完了を確認します。
    # PATCH 直後に古い Succeeded が返っても、設定が未反映なら期限内で待ち続けます。
    # 受付済みの PATCH を再送せず、Named Value の更新とは別に扱います。
    while True:
        observed = _ok(_ready_service(apim_id, transport, sleep=sleep, deadline=deadline), "Verify API Management custom domain")
        observed_properties = observed.get("properties", {})
        matched = [item for item in observed_properties.get("hostnameConfigurations", []) if all(item.get(key) == value for key, value in desired.items())]
        if any(str(item.get("certificateStatus", "")).lower() == "failed" for item in matched):
            raise ValueError("API Management custom domain certificate did not succeed")
        if matched and str(observed_properties.get("customProperties", {}).get(HTTP2, "")).lower() == "true":
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("API Management custom domain/HTTP2 readback is still pending; no additional update was requested")
        sleep(min(10, remaining))


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


def configure_endpoint(config: dict, outputs: dict, transport, *, sleep=time.sleep, timeout_seconds=3600) -> dict:
    """Core配備後にAPIMの公開ドメイン・HTTP/2だけを仕上げる。

    APIM作成→プライベート接続→VM証明書発行→APIM参照反映という依存順を守る。
    VMへAPIMの管理権限を渡さず、限定したARM更新で循環依存を避ける。
    この結果だけでgRPCやWebRTCの実通信が合格したとは判断しない。
    """
    grpc_on = enabled(config)
    if not grpc_on:
        return {"enabled": False}
    apim_id, feature = _own_apim(config, outputs)
    deadline = time.monotonic() + timeout_seconds
    _configure_domain(config, feature, outputs, apim_id, transport, sleep=sleep, deadline=deadline)
    return {"enabled": True, "grpcEnabled": True,
            "status": "management-plane-configured", "runtimeVerified": False,
            "certificateRenewal": "versionless-key-vault-reference",
            "certificateRefresh": "observe-sync-and-expiry-in-runtime-test",
            "trustBoundary": "AppGW configuration readers are trusted; TCP traffic has no WAF inspection"}


def configure_named_values(config: dict, outputs: dict, transport, *, sleep=time.sleep, timeout_seconds=600) -> dict:
    """Gateway資材の反映後に、入口・チケット鍵のKey Vault参照を確認する。"""
    grpc_on, realtime_on = enabled(config), realtime_enabled(config)
    if not grpc_on and not realtime_on:
        return {"enabled": False}
    apim_id, _ = _own_apim(config, outputs)
    vault_id = outputs.get("cleanup", {}).get("serviceResourceIds", {}).get("keyVault", "")
    prefix = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}/providers/Microsoft.KeyVault/vaults/"
    if not isinstance(vault_id, str) or not vault_id.lower().startswith(prefix.lower()) or '/' in vault_id[len(prefix):] or not re.fullmatch(r"kv-[a-z0-9-]+", vault_id[len(prefix):]):
        raise ValueError("Ingress secrets targeted a different Key Vault")
    vault_name = vault_id.rsplit("/", 1)[-1]
    deadline = time.monotonic() + timeout_seconds
    names = ([SECRET_NAME] if grpc_on else []) + ([TICKET_NAME] if realtime_on else [])
    for name in names:
        _refresh_secret(apim_id, name, vault_name, transport, sleep=sleep, deadline=deadline, location=config.get("location"))
    return {"enabled": True, "grpcEnabled": grpc_on, "realtimeEnabled": realtime_on,
            "status": "management-plane-configured", "runtimeVerified": False,
            "certificateRenewal": "versionless-key-vault-reference" if grpc_on else "not-used-by-apim",
            "certificateRefresh": "observe-sync-and-expiry-in-runtime-test" if grpc_on else "not-used-by-apim",
            "trustBoundary": "AppGW configuration readers are trusted; TCP traffic has no WAF inspection" if grpc_on else "APIM ticket signing credential is server-side"}

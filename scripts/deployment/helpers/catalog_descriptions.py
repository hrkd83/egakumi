"""公開 API 契約から、APIM とカタログに表示する接続説明だけを作る。

Azure への通信や設定変更は行わない。native azd と API だけの再公開が同じ
関数を使い、公開 FQDN/port・認証・Product・モデル情報の二重管理を防ぐ。
裏側 URL、主体 ID、認証値、秘密ファイル、入力/応答本文は説明へ含めない。
API Center の description 上限 1,000 文字を超える説明は配備前に拒否する。
"""
from __future__ import annotations

import copy
import re
from urllib.parse import urlsplit


MAX_DESCRIPTION_LENGTH = 1000
CATEGORIES = {"model-users": "Model Gateway", "tool-users": "Tool Gateway", "agent-users": "Hosted Agent API"}
SERVICE_CLASSES = {"platform": "初期提供・正式API", "sample": "別配備サンプルAPI"}


def _constraints(api):
    """統合後も、各入力profileのモデル・上限を独立した公開制約として読む。"""
    for constraint in api["authorization"]["operationConstraints"].values():
        if constraint.get("profiles"):
            inherited = {key: value for key, value in constraint.items() if key != "profiles"}
            for profile in constraint["profiles"]:
                yield {**inherited, **profile}
        else:
            yield constraint


def _short_list(values, limit=3):
    """重複を除いて順序を保ち、一覧が長い場合は省略件数を明示する。"""
    unique = list(dict.fromkeys(str(value) for value in values))
    shown = ", ".join(unique[:limit])
    return shown + (f" ほか{len(unique) - limit}件" if len(unique) > limit else "")


def _authentication(api, *, generated_mcp=False):
    """接続時に必要な購読キーのヘッダー名を示す。"""
    return f"認証: APIM購読キー。header={api.get('subscriptionKeyHeader', 'api-key')}。"


def _endpoint(config, api, *, generated_mcp=False):
    """実設定の公開入口だけから接続先を作る。内部APIM/Foundry名は使わない。

    URL と日本語の補足の間には空白を置く。カタログの自動リンクが括弧や説明文を
    URL の一部として取り込み、コピーした接続先が壊れることを防ぐ。
    """
    host = config["gateway"]["publicHostName"]
    if generated_mcp:
        return f"接続: https://{host}/{api['mcp']['path']}/mcp （Streamable HTTP）。"
    kind = api.get("apiType", "http")
    if kind == "grpc":
        grpc = config["gateway"]["grpc"]
        authority = f"{grpc['publicHostName']}:{grpc['publicPort']}"
        # API Center の自動同期は APIM 自身の URL を表示するため、AppGW の
        # TCP 入口で使う公開ポートを引き継ぐとは限らない。消費者がコピーできる
        # 完全な公開 URL を、APIM から同期される説明にも明記する。
        return f"接続: https://{authority}/ （native gRPC、TLS、SNI={grpc['publicHostName']}、ALPN=h2）。"
    prefix = api["path"].strip("/")
    origin = f"https://{host}" + ("/" + prefix if prefix else "")
    if kind == "websocket":
        return "接続: " + origin.replace("https://", "wss://", 1) + " （WebSocket）。"
    if kind == "mcp":
        if api["mcpProxy"]["transport"] == "sse":
            # サーバーが案内する相対POST先へAPIMが公開prefixを付ける。
            # 説明だけを/messages/固定にすると、実際の登録先と食い違ってしまう。
            message_path = next(endpoint["uriTemplate"] for endpoint in api["mcpProxy"]["endpoints"]
                                if endpoint["name"] == "message")
            return f"接続: GET {origin}/sse 、POST {origin}{message_path} （従来HTTP+SSE）。"
        return f"接続: {origin}/mcp （Streamable HTTP）。"
    return "接続: " + origin + " （HTTPS）。"


def _models(config, api, *, concise=False):
    """操作で許可した登録モデルだけを表示し、配備/実機成功を推定しない。"""
    names = []
    for constraint in _constraints(api):
        names.extend(constraint.get("allowedModels", []))
        if constraint.get("model"):
            names.append(constraint["model"])
    websocket = api.get("websocket", {})
    names.extend(websocket.get("allowedModels", []))
    names.extend(websocket.get("session", {}).get("models", []))
    if websocket.get("defaultModel"):
        names.append(websocket["defaultModel"])
    if websocket.get("queryParameters", {}).get("model"):
        names.append(websocket["queryParameters"]["model"])
    registry = {model["name"]: model for model in config.get("services", {}).get("modelDeployments", [])}
    descriptions = []
    for name in dict.fromkeys(names):
        model = registry.get(name)
        if model and not concise:
            descriptions.append(f"{name} [{model['modelFormat']}/{model['modelName']}, v={model['modelVersion']}]")
        else:
            descriptions.append(name)
    return "登録モデル: " + _short_list(descriptions, 2) + "。" if descriptions else ""


def _protocol_note(config, api, *, generated_mcp=False):
    """既存の方式契約を説明し、未測定のICE宛先や利用可能性は付け足さない。"""
    if generated_mcp:
        return "MCPのtool操作は、このAPIの購読キーと登録済みOperationで認可します。"
    if api.get("apiType") == "grpc":
        definition = api["grpc"]["definition"]
        package = re.search(r"\bpackage\s+([\w.]+)\s*;", definition)
        services = re.findall(r"\bservice\s+(\w+)\s*\{", definition)
        prefix = package[1] + "." if package else ""
        names = _short_list(prefix + service for service in services)
        return (("gRPC service: " + names + "。" if names else "")
                + "API Centerの同期URLに公開ポートがない場合も、上記の公開URLで接続します。"
                + "HTTPのAPI prefixはRPC pathへ付けません。TCP入口はHTTP WAF検査対象外で、他HTTP APIの迂回は禁止です。")
    constraints = list(_constraints(api))
    is_webrtc_control = any(item.get("kind") in ("realtimeCreate", "realtimeHangup", "liveCreate") for item in constraints)
    is_voice_live_calls = (api.get("apiType") == "websocket"
                          and urlsplit(api.get("backendUrl", "")).path.rstrip("/").endswith("/voice-live/realtime/calls"))
    if is_webrtc_control or is_voice_live_calls:
        return "WebRTCの接続制御はGateway経由。mediaは端末↔サービスのICE経路で、Gateway/PE経由を保証しません。端末側の通信許可が必要です。実宛先/portは受入記録を確認してください。"
    if api.get("websocket", {}).get("realtimeObserver"):
        return "本人所有のRealtime callを観測する接続。接続開始/終了APIや通常音声WSSとは別です。"
    if any(operation["urlTemplate"].endswith("/.well-known/agent-card.json") for operation in api["operations"]):
        return "A2A: Agent CardとJSON-RPC。提供するtask照会/取消/streamは公開操作契約に従います。"
    if api.get("apiType") == "websocket":
        if api.get("websocket", {}).get("session"):
            return "双方向WSS。モデルは接続後のsessionイベントで指定し、アプリと上流サービスで検査します。APIMは接続開始時の認証・固定接続先・回数上限を検査します。音声利用量は別に確認してください。"
        return "双方向WSS。接続時間・session・音声利用量はHTTP要求数とは別に確認してください。"
    return ""


def describe_api(config, api, *, generated_mcp=False):
    """同じ契約から最大1,000文字の説明を作り、元の設定を変更しない。"""
    category = CATEGORIES.get(api["productId"], "API Gateway")
    service_class = api.get("serviceClass", "sample" if api["name"].startswith("sample-") else "platform")
    category_line = f"区分: {category} / Product={api['productId']}。提供区分: {SERVICE_CLASSES[service_class]}。"
    operations = [f"{operation['method']} {operation['urlTemplate']}" for operation in api["operations"]]
    constraints = list(_constraints(api))
    byte_limits = sorted({item["maxRequestBytes"] for item in constraints if "maxRequestBytes" in item and item.get("bodyFormat") != "none"})
    token_limits = sorted({value for item in constraints for value in (item.get("maxOutputTokensByModel", {}).values() if item.get("maxOutputTokensByModel") else [item.get("maxOutputTokens")]) if isinstance(value, int) and not isinstance(value, bool)})
    limits = ("要求上限byte=" + _short_list(byte_limits) + "。" if byte_limits else "")
    if 524288000 in byte_limits:
        limits += "500 MiBはClassic APIM本文バッファーの技術上限。ファイル単位・入力tokenの制約は提供元が検査します。"
    limits += "出力枠=" + _short_list(token_limits) + " tokens。" if token_limits else ""
    if any("dynamic" in item.get("outputTokenLimitKinds", {}).values() for item in constraints):
        limits += "一部モデルの出力枠は振り分け先で変わります。"
    fixed = [category_line, _endpoint(config, api, generated_mcp=generated_mcp),
             _authentication(api, generated_mcp=generated_mcp),
             _protocol_note(config, api, generated_mcp=generated_mcp)]
    for concise in (False, True):
        # 長いモデル一覧では件数を明示して要約する。接続先や認証条件は切らない。
        lines = fixed + ([] if generated_mcp or api.get("apiType") == "grpc" else ["操作: " + _short_list(operations, 3) + "。"])
        lines += [limits, _models(config, api, concise=concise), "構成から生成した情報です。実機の試験結果は別記録です。"]
        result = "\n".join(line for line in lines if line)
        if len(result) <= MAX_DESCRIPTION_LENGTH:
            return result
    raise ValueError("Catalog description exceeds 1000 characters; shorten the public contract metadata before deployment")


def with_catalog_descriptions(config, apis=None):
    """native/API再公開へ同じ説明を付け、他のAPI・認可・接続先は完全に保持する。"""
    result = copy.deepcopy(config.get("apiDefinitions", []) if apis is None else apis)
    for api in result:
        api["catalogDescription"] = describe_api(config, api)
        if "mcp" in api:
            api["mcp"]["catalogDescription"] = describe_api(config, api, generated_mcp=True)
    return result

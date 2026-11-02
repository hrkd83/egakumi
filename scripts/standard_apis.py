"""API登録時に、空のAPI配列を標準APIへ展開する。

環境の正本と配備記録から接続先を決め、既存のモデル・CM設定を利用する。
この処理はメモリー内だけで完結し、入力ファイル・Azure資源・秘密値を扱わない。
APIを明記した設定はその配列を使い、基盤構築時の空配列の意味は変更しない。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

from deployment.helpers.configuration import normalize_configuration
from model_capabilities import apply_model_capabilities


ROOT = Path(__file__).resolve().parents[1]
API_DEFINITION_DIRECTORY = ROOT / "apps/gateway/apim/apis"
STANDARD_API_ORDER = (
    "openai-inference-v1", "pii-v1", "graph-profile-v1",
    "openai-embeddings-v1", "cohere-inference-v1", "openai-images-v1",
    "blackforest-images-v1", "azure-speech-v1", "openai-audio-v1",
    "microsoft-decision-v1", "openai-realtime-control-v1",
    "openai-realtime-ws-v1", "openai-realtime-observer-v1",
    "azure-voice-live-ws-v1", "azure-voice-live-webrtc-v1",
    "openai-live-control-v1", "openai-live-ws-v1", "openai-transcription-ws-v1",
)
# 配備対象はディレクトリ内のJSONです。一覧を別の正本にしません。
# 既存APIの出力順だけを維持し、新規ファイルは末尾へ名前順に追加します。
_API_POSITION = {name: index for index, name in enumerate(STANDARD_API_ORDER)}
DEFINITION_FILES = tuple(sorted(API_DEFINITION_DIRECTORY.glob("*.json"),
    key=lambda path: (_API_POSITION.get(path.stem, len(_API_POSITION)), path.name)))
STANDARD_API_NAMES = frozenset(path.stem for path in DEFINITION_FILES)
PLACEHOLDER = re.compile(r"__([A-Z][A-Z0-9_]+)__")

# 日本語の短い音声認識を標準とし、Voice Liveは独立したサービスのモデルを選ぶ。
# このモデル名はFoundryの32個のモデル配備へ追加する指定ではない。
# Voice Liveのモデル・API版を選択した構成での受入条件は INTEGRATION_TESTS.md を参照。
# https://learn.microsoft.com/azure/ai-services/speech-service/voice-live
# https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-webrtc
PUBLIC_BINDINGS = json.loads((ROOT / 'apps/gateway/apim/public-bindings.json').read_text(encoding='utf-8'))['bindings']


def _same(actual, expected, label):
    """環境の公開識別子を照合し、別の配備記録を接続先に使わない。"""
    if not isinstance(actual, str) or actual.casefold() != expected.casefold():
        raise ValueError(label + " differs from the selected environment")


def _bindings(config, state):
    """設定から決めたFoundryの名前とIDを配備記録で検証してURLを組み立てる。"""
    if not isinstance(state, dict) or not isinstance(state.get("outputs"), dict):
        raise ValueError("Standard API publication requires a completed core deployment record")
    for key in ("subscriptionId", "azureTenantId", "resourceStem"):
        _same(state.get(key), config[key], "Core deployment " + key)
    outputs = state["outputs"]
    for actual in (state.get("resourceGroupNames"), outputs.get("resourceGroups")):
        if actual != config["resourceGroupNames"]:
            raise ValueError("Core deployment resource groups differ from the selected environment")
    foundry_name = "aif-" + config["resourceStem"]
    resource_id = (
        "/subscriptions/" + config["subscriptionId"]
        + "/resourceGroups/" + config["resourceGroupNames"]["svc"]
        + "/providers/Microsoft.CognitiveServices/accounts/" + foundry_name
    )
    _same(outputs.get("names", {}).get("foundry"), foundry_name, "Foundry name")
    _same(outputs.get("services", {}).get("foundry"), resource_id, "Foundry service ID")
    _same(state.get("serviceResourceIds", {}).get("foundry"), resource_id, "Foundry cleanup ID")
    _same(outputs.get("endpoints", {}).get("foundry"),
          "https://" + foundry_name + ".services.ai.azure.com", "Foundry endpoint")
    _same(outputs.get("endpoints", {}).get("public", "").rstrip("/"),
          "https://" + config["gateway"]["publicHostName"], "Gateway endpoint")
    cognitive = foundry_name + ".cognitiveservices.azure.com"
    openai = foundry_name + ".openai.azure.com"
    # Speechは同じFoundryのカスタムドメインを使い、APIMのMIと既存のPE経路で呼ぶ。
    # https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech
    # https://learn.microsoft.com/azure/ai-services/speech-service/rest-speech-to-text-short
    return {
        "PUBLIC_HOST_NAME": config["gateway"]["publicHostName"],
        "OPENAI_RESOURCE_BASE_URL": "https://" + openai,
        "SPEECH_RESOURCE_ID": resource_id,
        "SPEECH_TTS_BASE_URL": "https://" + cognitive + "/tts",
        "SPEECH_STT_BASE_URL": "https://" + cognitive + "/stt",
        "REALTIME_WS_BASE_URL": "wss://" + openai + "/openai/v1/realtime",
        "REALTIME_OBSERVER_WS_BASE_URL": "wss://" + openai + "/openai/v1/realtime",
        "LIVE_WS_BASE_URL": "wss://" + openai + "/openai/v1/live/sessions",
        "TRANSCRIPTION_WS_BASE_URL": "wss://" + openai + "/openai/v1/realtime",
        "VOICE_LIVE_WS_BASE_URL": "wss://" + cognitive + "/voice-live/realtime",
        "VOICE_LIVE_CALLS_WS_BASE_URL": "wss://" + cognitive + "/voice-live/realtime/calls",
        **PUBLIC_BINDINGS,
    }


def _substitute(value, bindings):
    """公開テンプレートの差込み欄を解決し、未知の欄を残したまま公開しない。"""
    if isinstance(value, dict):
        return {key: _substitute(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, bindings) for item in value]
    if isinstance(value, str):
        def replace(match):
            name = match[1]
            if name not in bindings:
                raise ValueError("Standard API template contains an unsupported binding: " + name)
            return bindings[name]
        return PLACEHOLDER.sub(replace, value)
    return value


def load_api_definitions(path: Path) -> list[dict]:
    """1 API単位のJSON、定義ディレクトリ、明示的なAPI配列を読み込む。

    ディレクトリではファイル名とAPI名を一致させ、同名定義の混入を拒否する。
    モデル・ネットワーク・資格情報の共通設定は、この読込では扱わない。
    """
    path = Path(path)
    paths = sorted(path.glob("*.json")) if path.is_dir() else [path]
    definitions = []
    for file in paths:
        document = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(document, dict) and "name" in document:
            apis = [document]
        elif isinstance(document, dict):
            apis = document.get("apiDefinitions")
        else:
            apis = document
        if not isinstance(apis, list) or any(not isinstance(api, dict) or not api.get("name") for api in apis):
            raise ValueError("API definitions must contain named API objects")
        if path.is_dir() and (len(apis) != 1 or apis[0]["name"] != file.stem):
            raise ValueError("Each API file must contain the API matching its filename")
        definitions.extend(apis)
    names = [api["name"] for api in definitions]
    if not definitions or len(names) != len(set(names)):
        raise ValueError("API definitions must contain unique API names")
    if all(file.resolve().parent == API_DEFINITION_DIRECTORY.resolve() for file in paths):
        return apply_model_capabilities(definitions, bindings=PUBLIC_BINDINGS)
    return definitions


def _definitions():
    """標準18 APIを1 API単位の正本から読み、配備順を一定に保つ。"""
    definitions = [api for path in DEFINITION_FILES for api in load_api_definitions(path)]
    if (not definitions or len(definitions) != len(STANDARD_API_NAMES)
            or {api.get("name") for api in definitions} != STANDARD_API_NAMES):
        raise ValueError("Standard API definitions must match the standard API file inventory")
    if any(path.stem != api["name"] for path, api in zip(DEFINITION_FILES, definitions)):
        raise ValueError("Standard API filenames must match their API names")
    return definitions


def infrastructure_api_definitions(definitions: list[dict]) -> list[dict]:
    """公開先を解決せず、基盤のWAF・監査に必要な契約だけを返す。"""
    result = []
    for api in definitions or _definitions():
        constraints = {}
        for name, source in api['authorization']['operationConstraints'].items():
            constraint = {key: copy.deepcopy(source[key]) for key in ('kind', 'bodyFormat', 'contentTypes', 'allowedModels', 'maxRequestBytes') if key in source}
            if 'profiles' in source:
                constraint['profiles'] = [{'modalities': list(profile.get('modalities', []))} for profile in source['profiles']]
            constraints[name] = constraint
        projected = {
            'name': api.get('name', ''), 'apiType': api.get('apiType', 'http'), 'path': api['path'],
            'operations': [{key: operation[key] for key in ('name', 'method', 'urlTemplate')} for operation in api['operations']],
            'authorization': {'operationConstraints': constraints},
        }
        if 'mcp' in api:
            projected['mcp'] = {'path': api['mcp']['path']}
        if 'mcpProxy' in api:
            projected['mcpProxy'] = {
                'transport': api['mcpProxy']['transport'],
                'endpoints': [{key: endpoint[key] for key in ('name', 'uriTemplate')} for endpoint in api['mcpProxy']['endpoints']],
            }
        result.append(projected)
    return result


def resolve_publication_configuration(raw_source: dict, state: dict) -> dict:
    """API登録用の設定を返す。空配列なら標準18件、明記済みならその定義を使う。

    raw_sourceはload_configuration_sourceで読んだ派生項目を含まない設定。
    publishApisは入力値を維持し、実行可否の判定は公開コマンドが担当する。
    """
    if raw_source.get("apiDefinitions") != []:
        return normalize_configuration(raw_source)
    # 基盤の検査ではAPIを展開せず、接続先を確認してから登録用のコピーへ展開する。
    foundation = normalize_configuration(raw_source)
    bindings = _bindings(foundation, state)
    providers = foundation["credentialProviders"]
    login = foundation["gateway"]["authentication"].get("credentialLogin", {})
    if (not any(provider["name"] == "graph-delegated" for provider in providers)
            or "graph-delegated" not in login.get("providerNames", [])):
        raise ValueError("Standard APIs require the graph-delegated CM profile and shared credentialLogin setting")
    result = copy.deepcopy(raw_source)
    result["apiDefinitions"] = _substitute(_definitions(), bindings)
    for api in result["apiDefinitions"]:
        if api.get("credentialConnection", {}).get("mode") == "personal":
            api["credentialConnection"]["providerName"] = "graph-delegated"
        api["subscriptionKeyHeader"] = foundation["gateway"]["authentication"]["subscriptionKeyHeader"]
        # APIの登録と利用者の購読発行を分け、例に含まれる試験用キーを発行しない。
        # 明示APIのkeySubscriptionsは上の分岐で、そのまま利用者設定として扱う。
        api["keySubscriptions"] = []
    return normalize_configuration(result)

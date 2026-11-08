#!/usr/bin/env python3
"""基盤設定に正式API・モデルを加え、合成済み設定を保存する。

企業用paramsまたは合成済み設定、API定義ディレクトリまたは追加定義、公開ID・URLの
bindingsを入力し、ローカルのJSONを出力する。foundationは後でURLが決まる
APIをまだ含めず、fullは必要なbindingsを埋めて正式機能を揃える。
別リポジトリが提供するサンプルAPI・Agent・Toolsの接続先は生成しない。
秘密値はbindingsへ入れない。元の秘密ファイルへの参照は保存場所が変わっても
同じファイルを指すように解決する。モデルの重複・矛盾、APIの意図しない上書き、
未入力のURLを検査し、publishApisは常にfalseにする。
Azureへの接続、資源作成、API公開、推論は行わず、追加Azure費用は発生しない。
"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import argparse
import copy
import json
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit

from deployment.helpers.configuration import ConfigurationError, normalize_configuration, load_configuration_source, configuration_for_file, SECRET_VALUE_KEYS
from integration_client import private_write
from api_migration import is_sample_api_definition, migrate_api_definitions
from standard_apis import load_api_definitions
from deployment.helpers.paths import private_parent

CORE = Path(__file__).resolve().parents[1]
STANDARD_OVERLAY_DIRECTORY = CORE / "apps/gateway/apim/apis"
PLACEHOLDER = re.compile(r"__([A-Z][A-Z0-9_]+)__")


def platform_source(value):
    """旧入力のsample定義も、公開bindingの算出・置換より先に外す。"""
    result = copy.deepcopy(value)
    if "apiDefinitions" in result:
        result["apiDefinitions"] = [api for api in result["apiDefinitions"] if not is_sample_api_definition(api)]
    return result


def required_bindings(overlay):
    """追加設定に残る__NAME__形式の公開ID・URLの差込み名を列挙する。"""
    return sorted(set(PLACEHOLDER.findall(json.dumps(overlay))))


def substitute(value, bindings):
    """辞書・配列をたどり、公開プレースホルダーを入力bindingsで置き換える。"""
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda match: bindings[match[1]], value)
    if isinstance(value, list): return [substitute(item, bindings) for item in value]
    if isinstance(value, dict): return {key: substitute(item, bindings) for key, item in value.items()}
    return value


def merge_object(base, overlay):
    """元をコピーして辞書を再帰的に重ねる。配列や単一値は追加設定で置き換える。"""
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict): result[key] = merge_object(result[key], value)
        else: result[key] = copy.deepcopy(value)
    return result


def prepare(base, overlay, bindings, *, base_directory, phase="full", overlay_directory=None):
    """試験定義を合成し、未入力・秘密混入・モデル矛盾・API上書きをローカル検査する。

    同じモデルを複数の試験APIが使っても配備は1つにまとめ、基盤で指定した容量を守る。
    API分類をProductへ揃え、外部バックエンドのFW宛先を集める。ここでは公開しない。
    """
    if phase not in ("foundation", "full"): raise ValueError("Unknown preparation phase")
    base, overlay = platform_source(base), platform_source(overlay)
    all_required = required_bindings(overlay)
    if phase == "foundation": overlay = {key: value for key, value in overlay.items() if key != "apiDefinitions"}
    required = required_bindings(overlay)
    if not isinstance(bindings, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in bindings.items()):
        raise ValueError("Bindings must map public placeholder names to strings")
    if set(bindings) - set(all_required): raise ValueError("Bindings contain unsupported placeholder names")
    if any(not bindings.get(key, "").strip() or PLACEHOLDER.search(bindings[key]) for key in required):
        missing = [key for key in required if not bindings.get(key, "").strip() or PLACEHOLDER.search(bindings[key])]
        raise ValueError("Unresolved public bindings: " + ", ".join(missing))
    if any(re.sub(r"[-_]", "", name).lower() in {re.sub(r"[-_]", "", key).lower() for key in SECRET_VALUE_KEYS} for name in bindings):
        raise ValueError("Secret values must not be supplied as bindings")
    extra = substitute(overlay, bindings)
    # API例のファイル参照も、必ずそのJSONを置いたフォルダーを基準に扱います。
    # 合成設定を環境別stateへ出しても、CM用secretが別の相対保存先へずれないよう、
    # いったんbase側の場所から読める相対参照へ変換します。内容は読みません。
    extra = configuration_for_file(extra,
        base_directory=Path(overlay_directory) if overlay_directory is not None else STANDARD_OVERLAY_DIRECTORY,
        output_directory=Path(base_directory))
    # Voice Live の通常音声 WSS と WebRTC 用 SDP 制御は別 endpoint です。
    # 間違った入口への接続を、通常 WSS の成功で代用しないよう公開前に止めます。
    voice_paths = {"model-voice-live": "/voice-live/realtime",
                   "azure-voice-live-ws-v1": "/voice-live/realtime",
                   "model-voice-live-webrtc": "/voice-live/realtime/calls",
                   "azure-voice-live-webrtc-v1": "/voice-live/realtime/calls"}
    for api in extra.get("apiDefinitions", []):
        expected = voice_paths.get(api.get("name"))
        if expected and urlsplit(api.get("backendUrl", "")).path.rstrip("/") != expected:
            raise ValueError("Voice Live backend path must match its distinct WSS or WebRTC control API")
    result = merge_object(base, {key: value for key, value in extra.items() if key not in ("apiDefinitions", "services")})
    # 各APIの入口を基盤の購読キーヘッダーへ揃えます。
    authentication = result["gateway"].setdefault("authentication", {})
    authentication.setdefault("subscriptionKeyHeader", "api-key")
    for api in extra.get("apiDefinitions", []):
        api["subscriptionKeyHeader"] = authentication["subscriptionKeyHeader"]
    result["services"] = merge_object(base["services"], {key: value for key, value in extra.get("services", {}).items() if key != "modelDeployments"})
    # 同じモデルでChat・画像入力・PDF入力を試す場合も配備を重複させない。
    # モデル版やSKUの矛盾は止め、基盤側で確保した容量を優先する。
    models = {}
    for model in [*base["services"].get("modelDeployments", []), *extra.get("services", {}).get("modelDeployments", [])]:
        name = model["name"]
        if name in models:
            # Chat・画像・PDFが共有する配備は、版・SKUを一致させる。
            # 基盤側で明示した容量を試験の追加定義で上書きしない。
            for key in ("modelName", "modelVersion", "modelFormat", "skuName", "versionUpgradeOption"):
                if models[name].get(key) != model.get(key): raise ValueError("Conflicting model deployment definitions")
        else: models[name] = copy.deepcopy(model)
    result["services"]["modelDeployments"] = list(models.values())
    # 同じAPIは再利用します。内容が異なる同名APIを無言で上書きしません。
    apis = {api["name"]: copy.deepcopy(api) for api in base.get("apiDefinitions", [])}
    for api in extra.get("apiDefinitions", []):
        if api["name"] in apis:
            existing = normalize_configuration({**result, "apiDefinitions": [apis[api["name"]]]}, base_directory=base_directory)["apiDefinitions"]
            incoming = normalize_configuration({**result, "apiDefinitions": [api]}, base_directory=base_directory)["apiDefinitions"]
            if existing != incoming:
                raise ValueError("Coverage overlay must not silently replace an existing API")
            continue
        apis[api["name"]] = copy.deepcopy(api)
    # 旧APIと新APIを混ぜた入力も、同じ移行処理で正規の公開契約へ揃える。
    # backendPathや上流URIは移行処理が保持し、公開prefixの文字列置換では作らない。
    apis = {api["name"]: api for api in migrate_api_definitions(list(apis.values()))}
    for api in apis.values():
        product = "model-users" if api.get("path", "").startswith("models/") else "tool-users"
        api.setdefault("productId", product)
    result["apiDefinitions"] = list(apis.values())
    firewall = result["network"].setdefault("firewall", {})
    destinations = firewall.setdefault("apimAllowedFqdns", [])
    for api in apis.values():
        backends = [api, *[{**api, **operation["backendOverride"]} for operation in api.get("operations", [])
                            if operation.get("backendOverride")]]
        for backend in backends:
            if backend.get("backendService") == "external":
                host = urlsplit(backend.get("backendUrl", "")).hostname
                if host and host not in destinations: destinations.append(host)
    result["publishApis"] = False
    # 出力先が変わっても、元設定の秘密ファイル参照が同じ場所を指すよう絶対化する。
    path_fields = [(result.get("management", {}), "sshPublicKeyFile")]
    path_fields += [(result.get("certificate", {}), key) for key in ("pluginArgumentsFile", "preparedPfxFile", "preparedPfxPasswordFile")]
    path_fields += [(provider, "clientSecretFile") for provider in result.get("credentialProviders", [])]
    for parent, key in path_fields:
        if parent.get(key): parent[key] = str((Path(base_directory) / Path(parent[key]).expanduser()).resolve())
    normalize_configuration(result, base_directory=Path(base_directory))
    return result


def main():
    """CLIでテンプレートまたは合成済み設定を新規保存し、Azureへ変更を加えない。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True, help="API definition directory, a single API JSON, or a configuration overlay JSON")
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bindings-template", action="store_true")
    parser.add_argument("--phase", choices=("foundation", "full"), default="full")
    args = parser.parse_args()
    try:
        args.output = private_parent(args.output)
        if args.overlay.is_dir():
            overlay = {"apiDefinitions": load_api_definitions(args.overlay)}
        else:
            overlay = json.loads(args.overlay.read_text(encoding="utf-8"))
            if isinstance(overlay, list) or (isinstance(overlay, dict) and "name" in overlay):
                overlay = {"apiDefinitions": load_api_definitions(args.overlay)}
        overlay = platform_source(overlay)
        base = platform_source(load_configuration_source(args.base))
        if args.bindings_template:
            result = {name: "" for name in required_bindings(overlay if args.phase == "full" else {key: value for key, value in overlay.items() if key != "apiDefinitions"})}
        else:
            if not args.bindings: raise ValueError("A public bindings file is required")
            result = prepare(base, overlay, json.loads(args.bindings.read_text()), base_directory=args.base.resolve().parent, phase=args.phase,
                             overlay_directory=args.overlay.resolve() if args.overlay.is_dir() else args.overlay.resolve().parent)
            # 入力が小さなparamsでも、生成物はconfigを取り込んだ合成済み設定となる。
            # エディター用schemaも、その形式に合わせる。
            result["$schema"] = str(Path(__file__).resolve().parents[1] / "schemas/configuration.schema.json")
        if args.output.resolve() in {args.base.resolve(), args.overlay.resolve(), args.bindings.resolve() if args.bindings else None}:
            raise ValueError("Output must not overwrite an input")
        if not args.bindings_template:
            # 内部で解決した絶対パスを保存JSONへ持ち込まず、移動可能な参照へ戻す。
            result = configuration_for_file(result, base_directory=args.base.resolve().parent,
                                            output_directory=args.output.resolve().parent)
        private_write(args.output, json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({"saved": str(args.output), "resourceMutations": 0, "publishApis": False, "phase": args.phase, "bindingsTemplate": args.bindings_template}))
        return 0
    except (ValueError, OSError, ConfigurationError) as exc:
        # 設定エラーでも入力値を転載せず、失敗した例外の種類だけを表示する。
        print(json.dumps({"succeeded": False, "failureClass": type(exc).__name__})); return 2


if __name__ == "__main__": raise SystemExit(main())

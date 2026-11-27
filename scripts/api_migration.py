#!/usr/bin/env python3
"""Explicit, offline migration to the Core-owned platform API catalog.

Externally deployed sample registrations are excluded. The transformation does
not publish APIs, delete Azure resources or retain legacy URL aliases. By default
the CLI only reports counts; --write saves a protected backup outside the checkout
before replacing the input file. Pure functions never mutate their arguments.
"""

from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any


class MigrationError(ValueError):
    """A migration cannot preserve the registered contract safely."""


# Insertion order is the public catalog order in api-design-proposal.md.
CANONICAL_APIS = {
    "openai-inference-v1": ("models/openai/v1", "platform", ("models", "model-multimodal", "model-audio-chat")),
    "openai-embeddings-v1": ("models/openai-embeddings/v1", "platform", ("model-embeddings",)),
    "cohere-inference-v1": ("models/cohere/v1", "platform", ("model-cohere-embeddings", "model-rerank")),
    "openai-images-v1": ("models/openai-images/v1", "platform", ("model-images",)),
    "blackforest-images-v1": ("models/blackforestlabs/v1", "platform", ("model-flux-11", "model-flux-kontext", "model-flux-2")),
    "azure-speech-v1": ("models/azure-speech/v1", "platform", ("model-tts", "model-stt")),
    "openai-audio-v1": ("models/openai-audio/v1", "platform", ("model-openai-audio",)),
    "microsoft-decision-v1": ("models/microsoft-decision/v1", "platform", ("model-decision",)),
    "openai-realtime-control-v1": ("models/openai-realtime/v1", "platform", ("model-webrtc",)),
    "openai-realtime-ws-v1": ("models/openai-realtime/v1/ws", "platform", ("model-realtime", "model-realtime-mini")),
    "openai-realtime-observer-v1": ("models/openai-realtime/v1/observer", "platform", ("model-realtime-observer",)),
    "azure-voice-live-ws-v1": ("models/voice-live/v1/ws", "platform", ("model-voice-live",)),
    "azure-voice-live-webrtc-v1": ("models/voice-live/v1/webrtc", "platform", ("model-voice-live-webrtc",)),
    "pii-v1": ("tools/pii/v1", "platform", ("pii-v1",)),
    "graph-profile-v1": ("tools/graph/v1", "platform", ()),
}
API_RENAMES = {old: new for new, (_, _, sources) in CANONICAL_APIS.items() for old in sources}
# These names identify definitions previously shipped by Core. Their owning
# sample projects now register their APIs separately; they are not migrated into
# Core's platform catalog, even when a legacy definition lacks serviceClass.
EXTERNAL_SAMPLE_API_NAMES = frozenset({
    "tool-sample-rest", "sample-rest-v1",
    "tool-sample-mcp-json", "tool-sample-mcp-stream", "sample-mcp-v1",
    "tool-sample-mcp-legacy", "sample-mcp-legacy",
    "tool-sample-a2a", "sample-a2a-v1",
    "tool-sample-grpc", "sample-grpc-v1",
    "hosted-agent-sample", "hosted-agent-sample-v1", "sample-hosted-agent-v1",
})


def is_sample_api_definition(api: dict) -> bool:
    return api.get("serviceClass") == "sample" or api.get("name") in EXTERNAL_SAMPLE_API_NAMES


OPERATION_RENAMES = {
    ("model-multimodal", "vision"): "chat-completions",
    ("model-multimodal", "pdf"): "responses-create",
    ("model-audio-chat", "audio-chat"): "chat-completions",
    ("model-flux-11", "flux-generate"): "flux11-generate",
    ("model-flux-kontext", "flux-generate"): "kontext-generate",
    ("model-flux-kontext", "flux-edit"): "kontext-edit",
    ("model-flux-2", "flux-generate"): "flux2-generate",
    ("model-flux-2", "flux-edit"): "flux2-edit",
}
LEGACY_SETTING_IDS = {
    ("models", "chat-completions"): "chat",
    ("models", "responses-create"): "response",
    ("model-multimodal", "vision"): "vision",
    ("model-multimodal", "pdf"): "pdf",
    ("model-audio-chat", "audio-chat"): "audio-chat",
}
FLUX_ROUTES = {
    "flux11-generate": "/flux-1.1/generations", "kontext-generate": "/kontext/generations",
    "kontext-edit": "/kontext/edits", "flux2-generate": "/flux-2/generations", "flux2-edit": "/flux-2/edits",
}
BACKEND_FIELDS = ("backendService", "backendUrl", "backendAudience", "backendAuthentication", "backendHeaders")


def _unique(values: list[Any]) -> list[Any]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _backend(api: dict) -> dict:
    result = {key: deepcopy(api[key]) for key in BACKEND_FIELDS if key in api}
    result.setdefault("backendAuthentication", {"mode": "managedIdentity"})
    result.setdefault("backendHeaders", {})
    return result


def _merge_inference_settings(candidates: list[tuple[dict, str | None]]) -> dict:
    """旧入口の既定順を保ち、操作の基礎設定とモデル別差分へまとめます。"""
    if len(candidates) == 1 and candidates[0][1] is None:
        # 正規APIだけを再処理するときは、任意設定や省略状態も変えません。
        return candidates[0][0]

    base = deepcopy(candidates[0][0])
    base.pop("modelSettings", None)
    base["allowedModels"] = _unique([model for constraint, _ in candidates
                                    for model in constraint["allowedModels"]])
    # 別名も操作の表へ集約し、旧入口ごとの別名を失わないようにします。
    # 同じ別名が異なるモデルを指す場合は、黙って一方を選ばず移行を中止します。
    if any("modelAliases" in constraint for constraint, _ in candidates):
        aliases = {}
        for constraint, _ in candidates:
            for alias, model in constraint.get("modelAliases", {}).items():
                if model not in base["allowedModels"]:
                    raise MigrationError("Model aliases must target an allowed model")
                if alias in aliases and aliases[alias] != model:
                    raise MigrationError("Conflicting model aliases")
                aliases[alias] = model
        base["modelAliases"] = aliases
    # 本文を読む前の操作共通上限には、すべての旧候補を収容できる値を使います。
    # 各モデルの小さい上限は、下の差分計算でモデル別設定へ残します。
    base["maxRequestBytes"] = max(constraint["maxRequestBytes"] for constraint, _ in candidates)
    selected = {}
    for constraint, legacy_setting_id in candidates:
        for model in constraint["allowedModels"]:
            if model in selected:
                # 同じモデルが複数の旧入口にある場合は、従来の最初の候補を選びます。
                continue
            settings = {key: deepcopy(value) for key, value in constraint.items()
                        if key not in {"allowedModels", "modelSettings", "modelAliases"}}
            settings.update(deepcopy(constraint.get("modelSettings", {}).get(model, {})))
            if legacy_setting_id is not None:
                settings["settingId"] = legacy_setting_id
            selected[model] = settings
    # 能力・次元のモデル対応表は操作側へ一度だけ保存します。
    # 各モデルが選んだ旧候補の値を採用し、候補全体の表を差分へ複製しません。
    model_table_fields = ("maxOutputTokensByModel", "outputTokenLimitKinds", "embeddingDimensionsByModel")
    for field in model_table_fields:
        if not any(field in settings for settings in selected.values()):
            continue
        table = {}
        for model, settings in selected.items():
            values = settings.pop(field, {})
            if model in values:
                table[model] = values[model]
            else:
                raise MigrationError("Merged model settings require complete model tables")
        base[field] = table
    if "maxOutputTokensByModel" in base:
        maxima = [value for value in base["maxOutputTokensByModel"].values() if value is not None]
        base["maxOutputTokens"] = max(maxima) if maxima else None
        for model, settings in selected.items():
            settings["maxOutputTokens"] = base["maxOutputTokensByModel"][model]
    # 一部のモデルだけが持つ任意設定は、そのモデル側へ置きます。
    # 設定を省略した旧候補へ、ほかの候補の制限を継承させないためです。
    shared_fields = {"kind", "allowedModels", "maxRequestBytes", "modelAliases"} | set(model_table_fields)
    for key in list(base):
        if key not in shared_fields and any(key not in settings for settings in selected.values()):
            del base[key]
    base["modelSettings"] = {
        model: {key: value for key, value in settings.items() if key not in base or base[key] != value}
        for model, settings in selected.items()
    }
    return base


def _merge_group(target: str, sources: list[dict]) -> dict:
    path, service_class, legacy_sources = CANONICAL_APIS[target]
    order = {name: index for index, name in enumerate((target,) + legacy_sources)}
    sources = sorted(sources, key=lambda api: order[api["name"]])
    result = deepcopy(sources[0])
    result.update(name=target, path=path, serviceClass=service_class)
    # Preserve labels on an already canonical catalog on repeated preparation.
    if sources[0]["name"] != target:
        result["displayName"] = target.replace("-", " ").title()
    result["operations"] = []
    result.pop("keySubscriptions", None)
    result["authorization"] = {
        "mode": sources[0]["authorization"]["mode"],
        "operationConstraints": {},
    }
    default_backend = _backend(result)
    operations = {}
    inference_candidates = {}
    constraints = result["authorization"]["operationConstraints"]
    for api in sources:
        if api.get("productId") != result.get("productId"):
            raise MigrationError("Merged APIs must have the same product")
        if target != "azure-speech-v1" and _backend(api) != default_backend:
            raise MigrationError("Merged APIs require compatible registered backends")
        auth = api["authorization"]
        if auth["mode"] != "subscriptionKey":
            raise MigrationError("APIs must use subscription key authorization")
        if set(auth) - {"mode", "operationConstraints"}:
            raise MigrationError("Unsupported API authorization settings")
        for old_operation in api.get("operations", []):
            old_name = old_operation["name"]
            name = OPERATION_RENAMES.get((api["name"], old_name), old_name)
            operation = deepcopy(old_operation)
            operation["name"] = name
            if target == "blackforest-images-v1":
                operation["urlTemplate"] = FLUX_ROUTES[name]
            if target == "azure-speech-v1" and api["name"] != target:
                operation["backendOverride"] = _backend(api)
            constraint = deepcopy(api["authorization"]["operationConstraints"][old_name])
            if target == "openai-inference-v1":
                setting_id = None if api["name"] == target else LEGACY_SETTING_IDS[(api["name"], old_name)]
                inference_candidates.setdefault(name, []).append((constraint, setting_id))
            elif name not in constraints:
                constraints[name] = constraint
            elif constraints[name] != constraint:
                raise MigrationError("Conflicting operation constraints")
            if name not in operations:
                operations[name] = operation
            elif ({key: value for key, value in operations[name].items() if key != "displayName"} !=
                  {key: value for key, value in operation.items() if key != "displayName"}):
                raise MigrationError("Conflicting operations")
    for name, candidates in inference_candidates.items():
        constraints[name] = _merge_inference_settings(candidates)
    result["operations"] = list(operations.values())
    if target == "openai-realtime-ws-v1":
        models = []
        for api in sources:
            settings = api.get("websocket", {})
            models.extend(settings.get("allowedModels", []))
            if settings.get("queryParameters", {}).get("model"):
                models.append(settings["queryParameters"]["model"])
        models = _unique(models)
        if not models:
            raise MigrationError("Realtime API requires explicitly allowed models")
        query = deepcopy(result.get("websocket", {}).get("queryParameters", {}))
        query.pop("model", None)
        previous_default = result.get("websocket", {}).get("defaultModel")
        result["websocket"] = {"queryParameters": query, "allowedModels": models, "defaultModel": previous_default or models[0]}
    if target == "openai-realtime-control-v1":
        for constraint in constraints.values():
            constraint["publicCallPath"] = "/" + path + "/calls"
    if target == "openai-realtime-observer-v1":
        result["websocket"]["realtimeObserver"]["publicCallPath"] = "/models/openai-realtime/v1/calls"
    return result


def migrate_api_definitions(api_definitions: list[dict]) -> list[dict]:
    """Return Core platform definitions and exclude separately deployed samples.

    Partial catalogs and already migrated catalogs are supported. Unrecognized
    custom platform APIs must explicitly declare their serviceClass. Duplicate
    source API names are rejected rather than silently shadowing a registration.
    Retained APIs keep their contracts and grants; omitted sample definitions
    are only removed from the returned local configuration, never from Azure.
    """
    if not isinstance(api_definitions, list):
        raise MigrationError("apiDefinitions must be an array")
    groups: dict[str, list[dict]] = {}
    custom = []
    seen = set()
    for api in api_definitions:
        if not isinstance(api, dict) or not isinstance(api.get("name"), str):
            raise MigrationError("Every API must have a name")
        name = api["name"]
        if name in seen:
            raise MigrationError("Duplicate API names cannot be migrated")
        seen.add(name)
        if is_sample_api_definition(api):
            continue
        target = API_RENAMES.get(name, name)
        if target not in CANONICAL_APIS:
            if api.get("serviceClass") != "platform":
                raise MigrationError("Custom APIs require an explicit serviceClass")
            definition = deepcopy(api)
            definition.pop("keySubscriptions", None)
            custom.append(definition)
        else:
            groups.setdefault(target, []).append(api)
    return [_merge_group(name, groups[name]) for name in CANONICAL_APIS if name in groups] + custom


def migrate_configuration(configuration: dict) -> dict:
    if not isinstance(configuration, dict):
        raise MigrationError("Configuration must be an object")
    result = deepcopy(configuration)
    if "apiDefinitions" not in result:
        raise MigrationError("Configuration has no apiDefinitions")
    result["apiDefinitions"] = migrate_api_definitions(result["apiDefinitions"])
    return result


def _write_with_backup(path: Path, content: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise MigrationError("Input must be a regular file")
    # Do not honor a caller-supplied TMPDIR inside the Git checkout.
    temporary_root = "/private/tmp" if Path("/private/tmp").is_dir() else "/tmp"
    backup_dir = Path(tempfile.mkdtemp(prefix="egakumi-api-migration-", dir=temporary_root))
    os.chmod(backup_dir, 0o700)
    backup = backup_dir / "configuration.backup.json"
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output, path.open("rb") as original:
        shutil.copyfileobj(original, output)
    # The backup location is private and outside Git; no config is echoed.
    descriptor, temporary = tempfile.mkstemp(prefix=".api-migration-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", type=Path)
    parser.add_argument("--write", action="store_true", help="Replace input after creating a private backup outside Git")
    parser.add_argument("--dry-run", action="store_true", help="Report counts only (the default)")
    args = parser.parse_args(argv)
    if args.write and args.dry_run:
        parser.error("--write and --dry-run cannot be combined")
    try:
        original = json.loads(args.configuration.read_text(encoding="utf-8"))
        migrated = migrate_configuration(original)
        counts = {"before": len(original["apiDefinitions"]), "after": len(migrated["apiDefinitions"]),
                  "platform": sum(a.get("serviceClass") == "platform" for a in migrated["apiDefinitions"]),
                  "sample": sum(a.get("serviceClass") == "sample" for a in migrated["apiDefinitions"]),
                  "removedSamples": sum(is_sample_api_definition(a) for a in original["apiDefinitions"]),
                  "written": args.write}
        if args.write:
            backup = _write_with_backup(args.configuration, json.dumps(migrated, ensure_ascii=False, indent=2) + "\n")
            counts["backupFile"] = str(backup)
        print(json.dumps(counts))
        return 0
    except (MigrationError, OSError, ValueError, KeyError, TypeError):
        # Never echo input values, IDs, credential paths, or parse excerpts.
        print("API migration failed; configuration was not migrated. Check the source definitions and file access.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

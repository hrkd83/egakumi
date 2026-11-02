#!/usr/bin/env python3
"""購読キーと本人OAuth接続を使うAPI疎通確認設定をローカルで作る。

基盤のparams/configへ、CM用Entraアプリのclient IDと秘密ファイル参照を
組み合わせ、標準定義から選んだ3 APIと共通ログイン窓口を
持つJSONを新規保存する。共通設定は入力済みのparams/configを正本とする。
本人の接続はMicrosoft認証後に作り、認証済み接続セッションで利用する。
既存モデルと宣言元基準のファイル参照を保持し、秘密値の読取やAzure変更は行わない。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import uuid

from deployment.helpers.configuration import ConfigurationError, normalize_configuration, load_configuration_source, configuration_for_file
from standard_apis import API_DEFINITION_DIRECTORY, load_api_definitions


CORE = Path(__file__).resolve().parents[1]
TRIAL_API_NAMES = ("openai-inference-v1", "pii-v1", "graph-profile-v1")


def public_guid(value: str) -> str:
    """空や全ゼロでない公開GUIDを正規化する。"""
    try:
        parsed = uuid.UUID(value)
        if parsed.int == 0:
            raise ValueError()
        return str(parsed)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A nonzero public application GUID is required") from None


def build_trial_configuration(base: dict, *, base_directory: Path, output_directory: Path,
                              cm_client_id: str, cm_client_secret_file: Path) -> dict:
    """CMプロバイダーと共通ログイン窓口を設定し、既存モデルを保持する。"""
    client_id = public_guid(cm_client_id)
    definitions = {api["name"]: api for api in load_api_definitions(API_DEFINITION_DIRECTORY)}
    selected = [copy.deepcopy(definitions[name]) for name in TRIAL_API_NAMES]
    secret_path = Path(cm_client_secret_file).expanduser()
    secret_path = (output_directory / secret_path).resolve() if not secret_path.is_absolute() else secret_path.resolve()
    result = copy.deepcopy(base)
    providers = [provider for provider in result.get("credentialProviders", []) if provider.get("name") == "graph-delegated"]
    login = result.get("gateway", {}).get("authentication", {}).get("credentialLogin", {})
    if len(providers) != 1 or "graph-delegated" not in login.get("providerNames", []):
        raise ValueError("Configure graph-delegated and credentialLogin in the base parameters first")
    providers[0]["clientId"] = client_id
    providers[0]["clientSecretFile"] = str(secret_path)
    # モデル・プール・OAuth権限・テナント・共通認可は入力設定の値を保持します。
    authentication = result["gateway"].setdefault("authentication", {})
    authentication.setdefault("subscriptionKeyHeader", "api-key")
    for api in selected:
        api["subscriptionKeyHeader"] = authentication["subscriptionKeyHeader"]
        api["keySubscriptions"] = []
    result["apiDefinitions"] = selected
    result["publishApis"] = True
    # 出力フォルダーを変えても、宣言元のファイル参照は同じ場所を指すようにする。
    # ここでは参照文字列を解決するだけで、秘密ファイルの内容を開かない。
    for group, fields in (("management", ("sshPublicKeyFile",)),
                          ("certificate", ("pluginArgumentsFile", "preparedPfxFile", "preparedPfxPasswordFile"))):
        for key in fields:
            if key in result[group]:
                path = Path(result[group][key]).expanduser()
                result[group][key] = str((base_directory / path).resolve() if not path.is_absolute() else path.resolve())
    for provider in result["credentialProviders"]:
        path = Path(provider["clientSecretFile"]).expanduser()
        provider["clientSecretFile"] = str((base_directory / path).resolve() if not path.is_absolute() else path.resolve())
    # ここで加える外部宛先は、本人委任のGraph /meだけ。APIM本体のEntra・
    # Credential Manager用の通信許可とは役割を分けて追加する。
    apim_fqdns = result["network"]["firewall"].setdefault("apimAllowedFqdns", [])
    if "graph.microsoft.com" not in apim_fqdns:
        apim_fqdns.append("graph.microsoft.com")
    # 出力はparamsへの参照ではなく、configとAPI定義を取り込んだ合成済み設定。
    result["$schema"] = os.path.relpath(CORE / "schemas/configuration.schema.json", output_directory)
    normalize_configuration(result, base_directory=output_directory)
    return result


class PublicArguments(argparse.ArgumentParser):
    """引数エラーに利用者入力値をそのまま出さないCLIパーサー。"""
    def error(self, message):
        # argparse標準エラーは、誤って引数へ入力した秘密を表示し得るため使わない。
        """誤った引数の詳細値を伏せ、固定の入力エラーとして停止する。"""
        self.exit(2, "Invalid arguments. Use --help for public IDs and protected-file paths; never pass secret values.\n")


def main(argv=None) -> int:
    """公開IDを受け取り、基盤paramsとAPI雛形からローカル試験設定を新規保存する。"""
    parser = PublicArguments(description=__doc__)
    parser.add_argument("--base", type=Path, default=CORE / "params/test.local.json", help="Organization parameters referencing an optional config file")
    parser.add_argument("--output", type=Path, default=CORE / ".state/generated-config/integration.local.json", help="Create a compiled integration configuration; an existing file is never overwritten")
    parser.add_argument("--cm-client-id", required=True, help="Entra application client ID used by Credential Manager; an existing Core app can be reused")
    parser.add_argument("--cm-client-secret-file", type=Path, default=CORE / "secrets/entra-client-secret.txt", help="Protected-file path relative to output directory; its contents are not read")
    args = parser.parse_args(argv)
    try:
        base_path, output = args.base.expanduser().resolve(), args.output.expanduser().resolve()
        if output.exists():
            raise ValueError("Output already exists; choose a new --output file for review")
        base = load_configuration_source(base_path)
        value = build_trial_configuration(base, base_directory=base_path.parent, output_directory=output.parent,
            cm_client_id=args.cm_client_id, cm_client_secret_file=args.cm_client_secret_file)
        # build内で元の入力元へ解決した参照を、保存先基準の相対パスへ戻す。
        value = configuration_for_file(value, base_directory=output.parent, output_directory=output.parent)
        output.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        print(json.dumps({"created": True, "output": str(output), "apiNames": [api["name"] for api in value["apiDefinitions"]],
                          "authorizationMode": "subscriptionKey", "secretValuesRead": False, "azureChangesPerformed": False}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, ConfigurationError):
        print("Configuration was not created. Check public IDs, source settings and a new output path; secret values are not accepted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

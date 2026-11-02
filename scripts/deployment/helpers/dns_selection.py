#!/usr/bin/env python3
"""azd upの開始時に公開DNS事業者を選び、同じ構築入力へ保存する。

ここで選ぶのは証明書のDNS-01検証を行う事業者で、VNetのDNSサーバーではない。
秘密値は扱わず、paramsのcertificate.pluginとAzureの公開ID参照を更新する。企業固有値をconfigへ
戻したり、DNS APIへ接続したりしない。保存前に新設定を丸ごと検査し、再読込した
設定を返すことで、Firewallの自動許可先も選んだ事業者へ揃える。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from .configuration import ConfigurationError, load_configuration, validate_azure_dns_identity


class DnsSelectionError(ValueError):
    """入力値やファイル内容をエラーへ含めず、選択処理を停止する。"""


PROVIDERS = {"1": "Cloudflare", "2": "Route53", "3": "Azure"}


def _signature(info: os.stat_result) -> tuple:
    """読込後に別のプロセスが入力ファイルを変更したことを検出する。"""
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            stat.S_IMODE(info.st_mode))


def _source_snapshot(source: Path) -> tuple[bytes, os.stat_result]:
    """リンク経由の書換えを拒否し、通常のJSON入力を内容非表示で読み込む。"""
    for path in (source, *source.parents):
        if path.is_symlink():
            raise DnsSelectionError("DNS事業者の選択はシンボリックリンク経由の入力へ保存できません。元ファイルを指定してください。")
    try:
        info = source.stat()
        if not stat.S_ISREG(info.st_mode):
            raise DnsSelectionError("DNS事業者の選択には、既存の通常のparams JSONを指定してください。")
        data = source.read_bytes()
        if _signature(info) != _signature(source.stat()):
            raise DnsSelectionError("構築入力が別の処理で変更されました。選択を保存せず停止しました。")
        return data, info
    except OSError:
        raise DnsSelectionError("DNS事業者の選択に使う構築入力を読み込めません。保存先とアクセス権を確認してください。") from None


def _unchanged(source: Path, data: bytes, info: os.stat_result) -> None:
    """人がメニューを操作している間の編集を上書きしない。"""
    current_data, current_info = _source_snapshot(source)
    if current_data != data or _signature(current_info) != _signature(info):
        raise DnsSelectionError("構築入力が別の処理で変更されました。選択を保存せず停止しました。")


def _choose(current: str) -> str:
    """標準3事業者を番号で選ぶ。Enterは現在値、qは変更せず終了する。"""
    default = next((number for number, plugin in PROVIDERS.items() if plugin == current), "4")
    print("\n公開DNS事業者の選択（公開証明書の発行・更新用）\n"
          "VNetのDNSサーバー設定とは別です。秘密値はこの選択欄に入力しないでください。\n"
          "1. Cloudflare\n2. AWS Route53\n3. Azure Public DNS", flush=True)
    if default == "4":
        # 正規化済みplugin名は識別子のみだが、端末制御文字を表示させない。
        safe_current = "".join(c for c in current if c.isalnum())[:80]
        print("4. その他：paramsに指定済みの " + safe_current + " を維持", flush=True)
    print("選択は構築入力のcertificate.pluginへ保存し、その後に必要な資格情報を案内します。\n"
          "資格情報の入力を後で中止しても、保存済みの事業者選択は残ります。", flush=True)
    while True:
        try:
            answer = input("DNS事業者を選択 [" + default + "]（Enterで現在値、qで中止）: ").strip()
        except (KeyboardInterrupt, EOFError):
            raise DnsSelectionError("DNS事業者の選択を中止しました。paramsと資格情報は変更していません。") from None
        if answer.lower() in ("q", "quit"):
            raise DnsSelectionError("DNS事業者の選択を中止しました。paramsと資格情報は変更していません。")
        if not answer or answer == default:
            return current
        if answer in PROVIDERS:
            return PROVIDERS[answer]
        print("表示された番号、Enter、qのいずれかを指定してください。", flush=True)


def _azure_identity(current: dict) -> dict:
    """不足する公開IDだけを対話入力する。認証方式の選択やsecret入力はしない。"""
    result = {key: current[key] for key in ("azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId") if key in current}
    print("Azure DNSは事前作成したユーザー割り当てマネージドIDを使います。\n"
          "DNS側の操作権限を事前に付与してください。既存のIDは再利用します。", flush=True)
    try:
        for key, label in (("azureDnsSubscriptionId", "DNSゾーンがあるSubscription ID"),
                           ("azureDnsManagedIdentityResourceId", "DNS更新用マネージドIDのリソースID（/subscriptions/.../userAssignedIdentities/...）")):
            if key not in result:
                value = input(label + "（qで中止）: ").strip()
                if value.lower() in ("q", "quit"):
                    raise DnsSelectionError("Azure DNSの入力を中止しました。paramsは変更していません。")
                result[key] = value
        validate_azure_dns_identity(result)
    except (EOFError, KeyboardInterrupt):
        raise DnsSelectionError("Azure DNSの入力を中止しました。paramsは変更していません。") from None
    except ConfigurationError:
        raise DnsSelectionError("Azure DNSのSubscription IDまたはユーザー割り当てMIのリソースIDが不正です。paramsは変更していません。") from None
    return result


def select_dns_provider(source: Path | str, config: dict) -> dict:
    """対話時だけ事業者を選び、検査済みの最新設定を返す。

    呼出元は端末・依存関係を確認した後、秘密入力やAzure要求より前に呼ぶ。
    非対話のCIとprepared証明書ではparamsをそのまま使い、ファイルを変更しない。
    同じ選択なら既存入力のinode・時刻を保ち、変更時もconfigや秘密ファイルへ
    書き込まない。保存先が可変の合成済み入力も、宣言元基準の相対参照を保つ。
    """
    if config.get("certificate", {}).get("mode") != "dnsPlugin" or not sys.stdin.isatty():
        return config
    source = Path(source).expanduser().absolute()
    data, info = _source_snapshot(source)
    try:
        raw = json.loads(data)
        # 呼出元が設定を読んだ後、依存関係の検査中に編集された場合も停止する。
        if load_configuration(source) != config:
            raise DnsSelectionError("読込後に構築入力が変更されました。DNS選択を保存せず停止しました。")
    except (ConfigurationError, UnicodeError, json.JSONDecodeError):
        raise DnsSelectionError("DNS選択用の構築入力を検査できません。paramsを確認してください。") from None
    _unchanged(source, data, info)
    selected = _choose(config["certificate"]["plugin"])
    identity = _azure_identity(config["certificate"]) if selected == "Azure" else {}
    _unchanged(source, data, info)
    if config["certificate"]["plugin"] == selected and all(config["certificate"].get(key) == value for key, value in identity.items()):
        return config
    if source.name.endswith(".example.json"):
        raise DnsSelectionError("公開exampleは直接変更しません。paramsのexampleをlocal JSONへコピーしてからazd upを実行してください。")
    if not info.st_mode & 0o222:
        raise DnsSelectionError("DNS事業者の選択を保存するにはparamsへの書込み権限が必要です。")
    raw.setdefault("certificate", {})["plugin"] = selected
    raw["certificate"].update(identity)
    temporary: Path | None = None
    try:
        # 同じフォルダーに一時保存すればconfigFile・秘密参照の基準が変わらない。
        descriptor, name = tempfile.mkstemp(prefix=".dns-provider-", suffix=".json", dir=source.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode) & 0o666)
            stream.write(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        updated = load_configuration(temporary)
        _unchanged(source, data, info)
        os.replace(temporary, source)
        print("DNS事業者の選択を構築入力のcertificate.pluginへ保存しました。", flush=True)
        return updated
    except ConfigurationError:
        raise DnsSelectionError("選択後の構築設定に矛盾があるため保存しませんでした。paramsを確認してください。") from None
    except (OSError, UnicodeError):
        raise DnsSelectionError("DNS事業者の選択を保存できません。入力ファイルと権限を確認してください。") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

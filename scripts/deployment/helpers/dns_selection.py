"""公開構築に必要なAzure Public DNSの事前準備を確認・案内する。"""
from __future__ import annotations

from pathlib import Path

from .configuration import ConfigurationError, validate_supported_certificate


class DnsSelectionError(ValueError):
    """入力値やファイル内容を含めず、DNSの前提不足を案内する。"""


def select_dns_provider(source: Path | str, config: dict) -> dict:
    """Azure DNSの前提を確認する。対話選択・paramsの読書きは行わない。"""
    certificate = config.get("certificate", {})
    try:
        validate_supported_certificate(certificate)
    except ConfigurationError:
        raise DnsSelectionError(
            "公開構築はAzure Public DNSによる証明書発行だけに対応しています。"
            "公開DNSゾーンのSubscription IDと専用ユーザー割り当てマネージドIDをparamsへ設定してください。"
            "DNS事業者や証明書方式の対話選択は行いません。") from None
    print("公開DNSの事前準備: Azure Public DNSの権威ゾーンとNS委任を準備してください。\n"
          "DNS更新用の専用ユーザー割り当てマネージドIDへ、対象ゾーンのDNS Zone Contributorを付与してください。\n"
          "証明書はこのIDでDNS-01検証して発行・更新します。DNS資格情報の追加入力はありません。", flush=True)
    return config

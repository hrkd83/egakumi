"""Portal配備・実行待機・撤去で共通に使う、環境別ジョブの固定名。"""
from __future__ import annotations


def resource_names(configuration: dict) -> dict:
    """有効な最大長stemでもAzureのジョブ名上限内に収まる名前を返す。"""
    stem = configuration["resourceStem"]
    return {
        "schemaJob": f"caj-{stem}-psql",
        "reconcileJob": f"caj-{stem}-psync",
    }


def subscription_app_names(configuration: dict) -> dict:
    """最大長stemでも32文字に収まる、購読管理用の固定アプリ名を返す。"""
    stem = configuration['resourceStem']
    return {
        'subscriptionAdmin': f'ca-{stem}-sub-admin',
        'subscriptionProfileReader': f'ca-{stem}-sub-profile',
    }

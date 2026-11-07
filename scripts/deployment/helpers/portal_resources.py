"""Portal配備・実行待機・撤去で共通に使う、環境別ジョブの固定名。"""
from __future__ import annotations


def resource_names(configuration: dict) -> dict:
    """有効な最大長stemでもAzureのジョブ名上限内に収まる名前を返す。"""
    stem = configuration["resourceStem"]
    return {
        "schemaJob": f"caj-{stem}-psql",
        "reconcileJob": f"caj-{stem}-psync",
    }

"""廃止済みgRPCカタログ仕上げの互換入口。Azureへ追加の更新は行わない。

通常APIの説明は構築・公開BicepのAPI定義で再現する。過去の呼出元から
この入口へ達した場合も、廃止したgRPC設定を黙って公開しない。
"""
from __future__ import annotations


def finalize_descriptions(config, outputs, transport):
    """通常APIは資材内の説明を使用し、gRPC専用PATCHを行わない。"""
    if any(api.get("apiType") == "grpc" or "grpc" in api for api in config.get("apiDefinitions", [])):
        raise ValueError("Native gRPC publication has been retired; catalog inputs are not supported")
    return {"enabled": False, "checked": 0, "updated": 0, "apiIds": []}

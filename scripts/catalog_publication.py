"""gRPC のインポート完了後に、公開接続説明だけを APIM へ確実に反映する。

gRPC の proto インポートでは、同時に指定した description が空になる場合がある。
通常の azd up と API 再公開は、どちらもこの仕上げを使う。API Center 側の同期
データを書き換えず、APIM の正本を確認して、差分がある API の説明だけを更新する。
バックエンド・proto・認可・ポリシーには触れず、秘密値を記録しない。
"""
from __future__ import annotations

from deployment.helpers.catalog_descriptions import describe_api
from deployment.helpers.configuration import API_NAME
from deployment.helpers.grpc_lifecycle import apim_name_matches


API_VERSION = "2025-09-01-preview"


def _same(left, right):
    """Resource ID の大小文字だけを許容し、別の資源への変更を拒否する。"""
    return isinstance(left, str) and isinstance(right, str) and left.casefold() == right.casefold()


def _body(response, operation):
    """成功以外で停止し、Azure が返す入力入りエラー本文を表示しない。"""
    if response.get("statusCode") != 200 or not isinstance(response.get("body"), dict):
        raise ValueError(operation + " failed; private Azure output suppressed")
    return response["body"]


def finalize_descriptions(config, outputs, transport):
    """今回所有する gRPC API の説明を照合し、必要な場合だけ ETag 付きで更新する。

    全対象を読んで所有関係を確認した後に変更を始める。HTTP 競合や再読取の不一致は
    自動再試行せず停止する。二回目以降も同じ説明なら GET のみで完了する。
    """
    apis = [api for api in config.get("apiDefinitions", []) if api.get("apiType") == "grpc"]
    if not config.get("publishApis", False) or not apis:
        return {"enabled": False, "checked": 0, "updated": 0, "apiIds": []}
    name = outputs.get("names", {}).get("apim")
    if not apim_name_matches(config, name):
        raise ValueError("Catalog publication APIM name differs from this environment")
    apim_id = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
               f"{config['resourceGroupNames']['svc']}/providers/Microsoft.ApiManagement/service/{name}")
    cleanup_id = outputs.get("cleanup", {}).get("serviceResourceIds", {}).get("apim")
    if not _same(cleanup_id, apim_id):
        raise ValueError("Catalog publication APIM differs from cleanup ownership")
    parent = _body(transport.request("GET", apim_id + "?api-version=" + API_VERSION), "Read catalog APIM")
    if (not _same(parent.get("id"), apim_id)
            or any(parent.get("tags", {}).get(key) != config[field]
                   for key, field in (("product", "productName"), ("environment", "environmentName")))):
        raise ValueError("Catalog publication APIM ownership differs from this environment")
    plans = []
    for api in apis:
        if not isinstance(api.get("name"), str) or not API_NAME.fullmatch(api["name"]):
            raise ValueError("Catalog publication API name is invalid")
        api_id = apim_id + "/apis/" + api["name"]
        path = api_id + "?api-version=" + API_VERSION
        response = transport.request("GET", path)
        actual = _body(response, "Read catalog API")
        properties = actual.get("properties", {})
        if (not _same(actual.get("id"), api_id) or properties.get("type") != "grpc"
                or properties.get("path") != api["path"]
                or properties.get("displayName") != api["displayName"]):
            raise ValueError("Catalog publication API differs from the reviewed gRPC contract")
        expected = describe_api(config, api)
        etag = next((value for key, value in response.get("headers", {}).items()
                     if key.casefold() == "etag"), None)
        changed = properties.get("description", "") != expected
        if changed and (not isinstance(etag, str) or not etag or etag == "*"):
            raise ValueError("Catalog publication requires an exact API ETag")
        plans.append((api_id, path, properties, expected, etag, changed))
    updated = 0
    for api_id, path, before, expected, etag, changed in plans:
        if not changed:
            continue
        # proto をもう一度送ると説明が消えるため、説明一項目だけを PATCH する。
        _body(transport.request("PATCH", path, {"properties": {"description": expected}},
                                headers={"If-Match": etag}), "Finalize catalog description")
        after = _body(transport.request("GET", path), "Verify catalog description")
        expected_properties = {**before, "description": expected}
        if not _same(after.get("id"), api_id) or after.get("properties") != expected_properties:
            raise ValueError("Catalog description readback differs; other API fields must remain unchanged")
        updated += 1
    return {"enabled": True, "checked": len(plans), "updated": updated,
            "apiIds": [plan[0] for plan in plans]}

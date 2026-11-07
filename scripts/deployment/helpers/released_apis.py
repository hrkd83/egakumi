"""通常の購読が利用できるAPIを、明示されたリリース状態だけから決める。"""
from __future__ import annotations

import time

RELEASED_PRODUCT_ID = "released-apis"
RELEASED_PRODUCT_NAME = "Released APIs"
APIM_API = "2024-05-01"


def selected_api_names(definitions, *, released):
    """今回指定したAPIと生成MCPだけを返す。指定外のAPIの所属には触れない。"""
    names = []
    for api in definitions:
        if (api.get("released") is True) != released:
            continue
        names.append(api["name"])
        if "mcp" in api:
            names.append(api["mcp"]["name"])
    return names


def reconcile_unreleased_apis(apim_id, definitions, transport, *, sleep=time.sleep, timeout_seconds=60):
    """増分配備で残る公開製品の所属を解除する。API・検証製品・購読は削除しない。"""
    removed = []
    product_id = apim_id + "/products/" + RELEASED_PRODUCT_ID

    def has_membership(api_name):
        path = apim_id + "/apis/" + api_name + "/products?api-version=" + APIM_API
        response = transport.request("GET", path)
        if response.get("statusCode") == 404:
            return False
        body = response.get("body") or {}
        if response.get("statusCode") != 200 or body.get("nextLink") or not isinstance(body.get("value"), list):
            raise ValueError("Released API product membership could not be read")
        for product in body["value"]:
            if not isinstance(product, dict) or not isinstance(product.get("id"), str):
                raise ValueError("Released API product membership is invalid")
        # Azure は Product 自体の ID と、API 配下の所属 ID の両方を返します。
        # 今回の API と公開製品の正確な別表現だけを受け入れます。
        membership_ids = {product_id.casefold(),
                          (apim_id + "/apis/" + api_name + "/products/" + RELEASED_PRODUCT_ID).casefold()}
        return any(product["id"].rstrip("/").casefold() in membership_ids for product in body["value"])

    for api_name in selected_api_names(definitions, released=False):
        if not has_membership(api_name):
            continue
        # この製品の関連だけがリリース状態の正本。検証用Productとの関連は維持する。
        path = product_id + "/apis/" + api_name + "?api-version=" + APIM_API
        response = transport.request("DELETE", path, headers={"If-Match": "*"})
        if response.get("statusCode") not in (200, 202, 204, 404):
            raise ValueError("Released API product membership removal failed (HTTP " + str(response.get("statusCode")) + ")")
        deadline = time.monotonic() + timeout_seconds
        while has_membership(api_name):
            if time.monotonic() >= deadline:
                raise ValueError("Released API product membership removal is still pending")
            sleep(3)
        removed.append(api_name)
    return {"productId": RELEASED_PRODUCT_ID, "releasedApiNames": selected_api_names(definitions, released=True),
            "removedApiNames": removed}

"""API登録後にAPIMの初期サンプルだけを整理する。キー値は取得・記録しない。"""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlsplit

APIM_API = "2024-05-01"
API_LINK_VERSION = "2025-09-01-preview"  # MCPの所属も含めて業務利用を確認する。
SAMPLE_API = "echo-api"
SAMPLE_PRODUCTS = ("starter", "unlimited")


def cleanup_candidates(apis):
    """オフライン計画へ候補を載せる。存在や削除可否は実行時にだけ確認する。"""
    api_names = {api["name"].casefold() for api in apis}
    api_names.update(api["mcp"]["name"].casefold() for api in apis if "mcp" in api)
    product_names = {api.get("productId", "").casefold() for api in apis}
    return {"apiNames": [] if SAMPLE_API in api_names else [SAMPLE_API],
            "productNames": [name for name in SAMPLE_PRODUCTS if name not in product_names],
            "subscriptionSelection": "sample-product-or-echo-api-scopes-only", "inventoryChecked": False}


def _read(transport, path, api=APIM_API):
    response = transport.request("GET", path + "?api-version=" + api)
    if response["statusCode"] not in (200, 404):
        raise ValueError("Read default APIM samples failed (HTTP " + str(response["statusCode"]) + ")")
    return response


def _items(transport, collection, api=APIM_API):
    path, seen, items = collection + "?api-version=" + api, set(), []
    while path:
        parsed = urlsplit(path)
        if (path in seen or len(seen) >= 1000 or parsed.path.casefold() != collection.casefold()
                or parsed.scheme not in ("", "https") or parsed.netloc and parsed.hostname != "management.azure.com"
                or parsed.scheme and not parsed.netloc
                or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment
                or parse_qs(parsed.query).get("api-version") != [api]):
            raise ValueError("Default sample inventory left its APIM collection")
        seen.add(path)
        response = transport.request("GET", path)
        body = response.get("body") or {}
        if response["statusCode"] != 200 or not isinstance(body.get("value"), list):
            raise ValueError("Default sample inventory could not be read (HTTP " + str(response["statusCode"]) + ")")
        if any(not isinstance(item, dict) for item in body["value"]):
            raise ValueError("Default sample inventory is invalid")
        items.extend(body["value"])
        path = body.get("nextLink")
    return items


def _default_echo(body):
    properties = body.get("properties", {})
    try:
        url = urlsplit(properties.get("serviceUrl", ""))
        return (properties.get("path") == "echo" and properties.get("apiRevision") in (None, "", "1")
                and url.scheme in ("http", "https")
                and (url.hostname == "echoapi.cloudapp.net" or (
                    url.hostname == "echo.playground.azure-api.net"
                    and url.scheme == "https" and url.port in (None, 443)))
                and url.path.rstrip("/") == "/api"
                and url.port in (None, 80, 443) and not any((url.username, url.password, url.query, url.fragment)))
    except (TypeError, ValueError):
        return False


def cleanup_default_samples(apim_id, apis, transport, *, sleep=time.sleep, timeout_seconds=300):
    """既知の初期定義を購読→製品→APIの順で削除し、404まで確認する。

    設定で指定した名前、変更されたEcho、業務APIが所属する製品は保持する。
    それらを見つけてもAPI公開を止めず、保持理由を結果へ記録する。
    """
    result = {"status": "complete", "deletedApiIds": [], "deletedProductIds": [],
              "deletedSubscriptionIds": [], "skipped": []}
    candidates = cleanup_candidates(apis)
    echo_id = apim_id + "/apis/" + SAMPLE_API
    deadline = time.monotonic() + timeout_seconds

    def skip(resource_id, reason):
        result["skipped"].append({"resourceId": resource_id, "reason": reason})

    def remove(resource_id, key, predicate):
        current = _read(transport, resource_id)
        if current["statusCode"] == 404:
            return
        body = current.get("body") or {}
        if str(body.get("id", "")).casefold() != resource_id.casefold():
            raise ValueError("Default sample resource ID differs from the selected APIM")
        if not predicate(body):
            skip(resource_id, "changed-before-deletion")
            return
        if time.monotonic() >= deadline:
            raise ValueError("Default APIM sample cleanup is pending: " + resource_id)
        etag = body.get("etag") or next((v for k, v in current.get("headers", {}).items() if k.casefold() == "etag"), "*")
        response = transport.request("DELETE", resource_id + "?api-version=" + APIM_API, headers={"If-Match": etag})
        if response["statusCode"] not in (200, 202, 204, 404):
            raise ValueError("Delete default APIM sample failed (HTTP " + str(response["statusCode"]) + "): " + resource_id)
        while time.monotonic() < deadline:
            if _read(transport, resource_id)["statusCode"] == 404:
                result[key].append(resource_id)
                return
            sleep(3)
        raise ValueError("Default APIM sample deletion is pending: " + resource_id)

    echo = _read(transport, echo_id)
    echo_present = echo["statusCode"] == 200
    echo_is_default = echo_present and _default_echo(echo.get("body") or {})
    echo_removable = echo_is_default and SAMPLE_API in candidates["apiNames"]
    if echo_present and not echo_removable:
        skip(echo_id, "configured-api" if SAMPLE_API not in candidates["apiNames"] else "customized-api")

    eligible_products = []
    for name in SAMPLE_PRODUCTS:
        product_id = apim_id + "/products/" + name
        product = _read(transport, product_id)
        if product["statusCode"] == 404:
            continue
        if name not in candidates["productNames"]:
            skip(product_id, "configured-product")
            continue
        properties = (product.get("body") or {}).get("properties", {})
        links = _items(transport, product_id + "/apis", API_LINK_VERSION)
        only_echo = all(str(item.get("id", "")).casefold() in
                        {(product_id + "/apis/" + SAMPLE_API).casefold(), echo_id.casefold()} for item in links)
        if (str(properties.get("displayName", "")).casefold() != name or not only_echo
                or links and not echo_removable):
            skip(product_id, "customized-or-in-use-product")
            continue
        eligible_products.append(product_id)

    if echo_removable:
        links = _items(transport, echo_id + "/products")
        eligible_ids = {value.casefold() for value in eligible_products}
        # API-to-product lists can return association IDs instead of product IDs.
        # Accept only exact aliases of the products already eligible in this APIM.
        eligible_ids.update((echo_id + "/products/" + value.rsplit("/", 1)[-1]).casefold()
                            for value in eligible_products)
        if any(str(item.get("id", "")).casefold() not in eligible_ids for item in links):
            echo_removable = False
            skip(echo_id, "api-used-by-retained-product")

    subscription_scopes = {value.casefold() for value in eligible_products}
    if echo_removable:
        subscription_scopes.add(echo_id.casefold())
    subscriptions = _items(transport, apim_id + "/subscriptions") if subscription_scopes else []
    for subscription in subscriptions:
        scope = subscription.get("properties", {}).get("scope", "")
        if not isinstance(scope, str) or scope.rstrip("/").casefold() not in subscription_scopes:
            continue
        subscription_id = subscription.get("id", "")
        prefix = apim_id + "/subscriptions/"
        name = subscription_id[len(prefix):] if isinstance(subscription_id, str) and subscription_id.casefold().startswith(prefix.casefold()) else ""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", name) or name.casefold() == "master":
            raise ValueError("Default sample subscription ID differs from the selected APIM")
        remove(subscription_id, "deletedSubscriptionIds", lambda b: str(b.get("properties", {}).get("scope", "")).rstrip("/").casefold() in subscription_scopes)
    for product_id in eligible_products:
        remove(product_id, "deletedProductIds", lambda b: str(b.get("properties", {}).get("displayName", "")).casefold() == product_id.rsplit("/", 1)[-1])
    if echo_removable:
        remove(echo_id, "deletedApiIds", _default_echo)
    return result

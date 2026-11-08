"""固定したAPIM名を、有料リソースを作る前に読み取り検査する。

名前が使用中でも末尾へ文字列を足して別APIMを作らない。既存の同一環境なら
そのAPIMを再利用し、別環境や削除途中の資源があれば理由を示して停止する。
checkNameAvailabilityは名前の照会APIであり、POSTでも資源は作成しない。
Microsoft内部のCredential Manager予約は公開APIでは照合できないため、
この検査の成功を内部予約の解放保証として扱わない。
"""
from __future__ import annotations

from collections.abc import Mapping

from .apim_naming import apim_name


class ApimPreflightError(RuntimeError):
    """APIの生応答や秘密値を含めず、固定名で続行できない理由を返す。"""


def _read(transport, method: str, path: str, *, body=None):
    """応答の形を検査する。認証情報を含み得るエラー本文は転記しない。"""
    try:
        response = transport.request(method, path, body=body)
    except Exception:
        raise ApimPreflightError("APIMの固定名確認で通信に失敗しました。接続と認証先を確認してください。") from None
    if (not isinstance(response, Mapping)
            or type(response.get("statusCode")) is not int
            or response["statusCode"] not in (200, 404)):
        raise ApimPreflightError("APIMの固定名を事前確認できません。権限とAzureの応答を確認してください。")
    payload = response.get("body")
    if payload is not None and not isinstance(payload, Mapping):
        raise ApimPreflightError("APIMの固定名確認で想定外の応答を受け取りました。")
    return response["statusCode"], payload or {}


def verify_apim_name(config: dict, transport) -> dict:
    """同名APIMの所有・稼働状態、削除予約、グローバル名の空きを照合する。

    既存APIMの名前だけで所有と判定せず、対象サブスク内の完全なID、種類、
    リージョン、製品・環境タグも照合する。削除済み資源の復元やpurgeはせず、
    競合があれば有料の監視・NW配備へ入る前に停止する。
    """
    name = apim_name(config)
    resource_id = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
                   f"{config['resourceGroupNames']['svc']}/providers/Microsoft.ApiManagement/service/{name}")
    api = "2024-05-01"
    status, service = _read(transport, "GET", resource_id + "?api-version=" + api)
    if status == 200:
        tags = service.get("tags")
        actual_location = service.get("location")
        if (str(service.get("id", "")).casefold() != resource_id.casefold()
                or service.get("name") != name
                or str(service.get("type", "")).casefold() != "microsoft.apimanagement/service"
                or not isinstance(actual_location, str)
                or actual_location.replace(" ", "").casefold() != config["location"].casefold()
                or not isinstance(tags, Mapping)
                or any(tags.get(key) != config[source] for key, source in
                       (("product", "productName"), ("environment", "environmentName")))):
            raise ApimPreflightError("同名APIMの配置先・所有タグが一致しません。名前を変更せず、既存資源の所有を確認してください。")
        properties = service.get("properties")
        if not isinstance(properties, Mapping) or properties.get("provisioningState") != "Succeeded":
            raise ApimPreflightError("同名APIMが構築・更新・削除中です。完了状態を確認してから同じ名前で再実行してください。")
        return {"status": "owned-existing", "name": name, "resourceId": resource_id,
                "credentialManagerInternalReservationVerified": False}

    # APIMは本体削除後も名前を予約する。削除履歴がある間の新規作成は止め、
    # 所有者が通常の復元・完全削除手順を判断できるようにする。
    deleted_id = (f"/subscriptions/{config['subscriptionId']}/providers/Microsoft.ApiManagement/"
                  f"locations/{config['location']}/deletedservices/{name}")
    deleted_status, _ = _read(transport, "GET", deleted_id + "?api-version=" + api)
    if deleted_status == 200:
        raise ApimPreflightError("固定名APIMの削除履歴が残っています。名前を変更せず、対象の復元または完全削除を確認してください。")
    availability_status, availability = _read(transport, "POST",
        f"/subscriptions/{config['subscriptionId']}/providers/Microsoft.ApiManagement/checkNameAvailability?api-version={api}",
        body={"name": name})
    if availability_status != 200:
        raise ApimPreflightError("APIMの名前照会APIを確認できません。Azureの対応APIと権限を確認してください。")
    if availability.get("nameAvailable") is not True:
        raise ApimPreflightError("固定したAPIM名は利用できません。別名へ切り替えず、同名資源・削除予約またはAzureサポートで確認してください。")
    return {"status": "available-for-create", "name": name, "resourceId": resource_id,
            "credentialManagerInternalReservationVerified": False}

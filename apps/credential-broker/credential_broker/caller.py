"""APIMで検証した利用者と購読契約を別の主体として保持する。"""
from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID


@dataclass(frozen=True)
class UserCaller:
    """共有鍵を検証したHTTP入口からだけ受け取るユーザー主体。"""

    tenant_id: str
    principal_id: str

    def __post_init__(self):
        for name in ("tenant_id", "principal_id"):
            value = getattr(self, name)
            parsed = UUID(value)
            if not parsed.int or value != str(parsed):
                raise ValueError("Invalid verified caller")

    @property
    def caller_id(self) -> str:
        """購読IDとは異なる名前空間の正規化済み識別子を返す。"""
        return "user:" + self.tenant_id + ":" + self.principal_id

    @classmethod
    def parse(cls, value: str) -> UserCaller:
        """正規形のtenant/oidだけを受け付ける。"""
        if not isinstance(value, str) or not re.fullmatch(
            r"user:[a-f0-9-]{36}:[a-f0-9-]{36}", value
        ):
            raise ValueError("Invalid verified caller")
        _, tenant, principal = value.split(":")
        return cls(tenant, principal)


def storage_fields(caller: str | UserCaller) -> tuple[str | None, str | None]:
    """既存購読列と新ユーザー列のうち一方だけに値を設定する。"""
    if isinstance(caller, UserCaller):
        return None, caller.caller_id
    if not isinstance(caller, str) or not re.fullmatch(
        r"[A-Za-z0-9._-]{1,256}", caller
    ):
        raise ValueError("Invalid verified subscription")
    return caller, None


def matches(record: dict, caller: str | UserCaller) -> bool:
    """セッションの契約と、ユーザーの場合はCMの本人も照合する。"""
    subscription, user = storage_fields(caller)
    if (record.get("subscriptionId") != subscription
            or record.get("callerId") != user):
        return False
    if isinstance(caller, UserCaller):
        for field, expected in (
            ("tenantId", caller.tenant_id),
            ("objectId", caller.principal_id),
            ("requiredTenantId", caller.tenant_id),
            ("requiredObjectId", caller.principal_id),
        ):
            if record.get(field) is not None and record[field] != expected:
                return False
    return True

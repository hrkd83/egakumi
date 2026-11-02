"""公開設定と Secret 環境変数を分け、起動時に不足を検出する。"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from urllib.parse import urlsplit
from uuid import UUID


class SettingsError(ValueError):
    pass


def guid(value: str) -> str:
    try:
        parsed = UUID(value)
        if not parsed.int:
            raise ValueError()
        return str(parsed)
    except (ValueError, TypeError, AttributeError):
        raise SettingsError("A nonzero identity identifier is required") from None


def https_url(value: str, *, origin=False) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.port not in (None, 443)
                or (origin and parsed.path not in ("", "/"))):
            raise ValueError()
    except (ValueError, TypeError):
        raise SettingsError("A fixed HTTPS endpoint is required") from None
    return value.rstrip("/") if origin else value


@dataclass(frozen=True)
class Profile:
    name: str
    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)
    identity_endpoint: str = "https://graph.microsoft.com/v1.0/me"
    identity_id_property: str = "id"
    pools: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class Settings:
    public_origin: str
    shared_key: str = field(repr=False)
    profiles: dict[str, Profile] = field(repr=False)
    subscription_id: str = ""
    resource_group: str = ""
    apim_name: str = ""
    apim_principal_id: str = ""
    azure_tenant_id: str = ""
    managed_identity_client_id: str = ""
    transaction_ttl: int = 300
    idle_timeout_seconds: int = 604800
    absolute_lifetime_seconds: int = 2592000
    rotation_interval_seconds: int = 86400
    rotation_grace_seconds: int = 300
    rotation_handoff_seconds: int = 600
    rotation_candidate_ttl_seconds: int = 600
    login_calls_per_minute: int = 10

    @classmethod
    def from_environment(cls, env=None):
        env = os.environ if env is None else env
        def required(name):
            value = env.get(name, "")
            if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
                raise SettingsError("Required broker configuration is missing")
            return value
        try:
            source = json.loads(required("BROKER_PROFILES_JSON"))
            if not isinstance(source, dict) or not source:
                raise ValueError()
            profiles, pool_names = {}, set()
            for name, item in source.items():
                if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", name) or not isinstance(item, dict):
                    raise ValueError()
                secret_env = item["clientSecretEnv"]
                if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", secret_env):
                    raise ValueError()
                pools = []
                for pool in item["pools"]:
                    pool_name, capacity = pool["name"], pool["capacity"]
                    if (not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", pool_name)
                            or pool_name in pool_names or type(capacity) is not int or not 1 <= capacity <= 10000):
                        raise ValueError()
                    pools.append((pool_name, capacity))
                    pool_names.add(pool_name)
                if not pools or len(pool_names) > 1000:
                    raise ValueError()
                prop = item["identityIdProperty"]
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", prop):
                    raise ValueError()
                profiles[name] = Profile(name, guid(item["tenantId"]), guid(item["clientId"]),
                    required(secret_env), https_url(item["identityEndpoint"]), prop, tuple(pools))
            transaction_ttl = int(env.get("BROKER_TRANSACTION_TTL_SECONDS", "300"))
            idle_timeout = int(env.get("BROKER_IDLE_TIMEOUT_SECONDS", "604800"))
            absolute_lifetime = int(env.get("BROKER_ABSOLUTE_LIFETIME_SECONDS", "2592000"))
            rotation_interval = int(env.get("BROKER_ROTATION_INTERVAL_SECONDS", "86400"))
            rotation_grace = int(env.get("BROKER_ROTATION_GRACE_SECONDS", "300"))
            rotation_handoff = int(env.get("BROKER_ROTATION_HANDOFF_SECONDS", "600"))
            rotation_candidate_ttl = int(env.get("BROKER_ROTATION_CANDIDATE_TTL_SECONDS", "600"))
            login_limit = int(env.get("BROKER_LOGIN_CALLS_PER_MINUTE", "10"))
            if (not 60 <= transaction_ttl <= 600 or not 300 <= idle_timeout <= absolute_lifetime <= 31536000
                    or not 300 <= rotation_interval <= 2592000 or rotation_interval >= absolute_lifetime
                    or not 1 <= rotation_grace <= 3600 or rotation_grace >= rotation_interval
                    or not 60 <= rotation_handoff <= 3600
                    or not 60 <= rotation_candidate_ttl <= rotation_handoff
                    or not 1 <= login_limit <= 10000):
                raise ValueError()
            shared = required("BROKER_SHARED_KEY")
            if len(shared.encode()) < 32:
                raise ValueError()
            group, apim = required("AZURE_RESOURCE_GROUP"), required("APIM_NAME")
            if not re.fullmatch(r"[A-Za-z0-9._()-]{1,90}", group) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,49}", apim):
                raise ValueError()
            return cls(https_url(required("BROKER_PUBLIC_ORIGIN"), origin=True), shared, profiles,
                guid(required("AZURE_SUBSCRIPTION_ID")), group, apim, guid(required("APIM_PRINCIPAL_ID")),
                guid(required("AZURE_TENANT_ID")), guid(required("AZURE_CLIENT_ID")), transaction_ttl,
                idle_timeout, absolute_lifetime, rotation_interval, rotation_grace, rotation_handoff,
                rotation_candidate_ttl, login_limit)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise SettingsError("Broker configuration is invalid or incomplete") from None

    @property
    def apim_resource_id(self):
        return f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}/providers/Microsoft.ApiManagement/service/{self.apim_name}"

    @property
    def session_policy(self):
        """認証完了時にSQLへ固定する期限方針を返し、既存セッションへ遡及適用しない。"""
        return {"idleTimeoutSeconds": self.idle_timeout_seconds,
            "absoluteLifetimeSeconds": self.absolute_lifetime_seconds,
            "rotationIntervalSeconds": self.rotation_interval_seconds,
            "rotationGraceSeconds": self.rotation_grace_seconds,
            "rotationHandoffSeconds": self.rotation_handoff_seconds,
            "rotationCandidateTtlSeconds": self.rotation_candidate_ttl_seconds}

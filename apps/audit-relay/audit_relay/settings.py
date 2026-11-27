"""Runtime settings and fixed internal route definitions."""
from dataclasses import dataclass
import os
import re
from urllib.parse import urlsplit
from uuid import UUID


ROUTES = {"/chat/completions": "chat-completions", "/responses": "responses-create"}
FOUNDRY_SCOPE = "https://cognitiveservices.azure.com/.default"


def required(name):
    value = os.environ.get(name, "")
    if not value or value != value.strip():
        raise RuntimeError(f"Missing or invalid setting: {name}")
    return value


def identifier(name):
    try:
        return str(UUID(required(name)))
    except ValueError:
        raise RuntimeError(f"Invalid identifier setting: {name}") from None


def origin(name, suffix):
    try:
        value = urlsplit(required(name))
        if (value.scheme == "https" and value.path in ("", "/") and not value.query and not value.fragment
                and re.fullmatch(r"[a-z0-9-]+\." + re.escape(suffix), value.netloc)):
            return f"https://{value.netloc}"
    except ValueError:
        pass
    raise RuntimeError(f"Invalid service origin: {name}")


@dataclass(frozen=True)
class EventHubSettings:
    namespace: str
    name: str
    client_id: str

    @classmethod
    def load(cls):
        namespace = required("AUDIT_EVENTHUB_NAMESPACE")
        if not re.fullmatch(r"[a-z0-9-]+\.servicebus\.windows\.net", namespace):
            raise RuntimeError("Invalid Event Hubs hostname")
        return cls(namespace, required("AUDIT_EVENTHUB_NAME"), identifier("AZURE_CLIENT_ID"))


@dataclass(frozen=True)
class RelaySettings:
    hub: EventHubSettings
    tenant_id: str
    audience: str
    apim_principal_id: str
    endpoint: str
    max_http_bytes: int = 16 * 1024 * 1024
    max_capture_bytes: int = 1024 * 1024
    publish_timeout: float = 4.0

    @classmethod
    def load(cls):
        return cls(EventHubSettings.load(), identifier("AUDIT_TENANT_ID"),
            identifier("AUDIT_TOKEN_AUDIENCE"), identifier("AUDIT_APIM_PRINCIPAL_ID"),
            origin("AUDIT_FOUNDRY_OPENAI_ENDPOINT", "openai.azure.com"))


@dataclass(frozen=True)
class WorkerSettings:
    hub: EventHubSettings
    consumer_group: str
    evidence_url: str
    evidence_container: str
    checkpoint_url: str
    checkpoint_container: str

    @classmethod
    def load(cls):
        return cls(EventHubSettings.load(), required("AUDIT_EVENTHUB_CONSUMER_GROUP"),
            origin("AUDIT_STORAGE_ACCOUNT_URL", "blob.core.windows.net"), required("AUDIT_BLOB_CONTAINER"),
            origin("AUDIT_CHECKPOINT_STORAGE_ACCOUNT_URL", "blob.core.windows.net"),
            required("AUDIT_CHECKPOINT_CONTAINER"))

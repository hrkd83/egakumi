"""棚卸した部品だけを、合成テストの安定したbasenameから明示的に選ぶ。"""
from pathlib import Path


_PRIMARY_FILES = {
    identifier + suffix: Path(folder) / identifier / (identifier + suffix)
    for identifier, folder in (
        ("core-request-context", "fragments/audit"),
        ("core-audit-authenticated-subject", "fragments/audit"),
        ("core-trusted-ingress", "fragments/ingress"),
        ("core-credential-browser-exception", "fragments/authentication"),
        ("core-common-authentication", "fragments/authentication"),
        ("core-api-authentication-result", "fragments/authentication"),
        ("core-credential-connection", "fragments/authentication"),
        ("core-credential-connection-outbound", "fragments/authentication"),
        ("core-credential-session-use", "fragments/authentication"),
        ("core-gateway-telemetry", "fragments/audit"),
        ("core-backend-audit", "fragments/audit"),
        ("core-audit-body-model", "fragments/audit"),
        ("core-audit-context", "fragments/audit"),
        ("core-realtime-observer", "fragments/authentication"),
        ("core-api-settings", "fragments/settings"),
        ("core-query-observation", "fragments/request"),
        ("core-api-input-validation", "fragments/request"),
        ("core-api-operation-settings", "fragments/request"),
        ("core-api-target-validation", "fragments/request"),
        ("core-chat-store-disabled", "fragments/request"),
        ("core-model-limits", "fragments/request"),
        ("core-rate-limit", "fragments/quota"),
        ("core-token-limits", "fragments/quota"),
        ("core-entra-user-profile", "fragments/quota"),
        ("core-speech-ssml-guard", "fragments/request"),
        ("core-document-input-validation", "fragments/request"),
        ("core-query-telemetry", "fragments/audit"),
    )
    for suffix in (".xml", ".cs")
}
_PRIMARY_FILES["core-entra-authentication-error.xml"] = Path("fragments/authentication/core-entra-authentication-error/core-entra-authentication-error.xml")
_PRIMARY_FILES["core-api-request-preparation.xml"] = Path("fragments/request/core-api-request-preparation/core-api-request-preparation.xml")
_PRIMARY_FILES["query-observation-inbound.xml"] = Path("snippets/audit/query-observation-inbound/query-observation-inbound.xml")
_PRIMARY_FILES["rate-limit.xml"] = _PRIMARY_FILES["core-rate-limit.xml"]
_PRIMARY_FILES["token-limit.xml"] = _PRIMARY_FILES["core-token-limits.xml"]
_PRIMARY_FILES["entra-user-profile.xml"] = _PRIMARY_FILES["core-entra-user-profile.xml"]
_PRIMARY_FILES["credential-connection.xml"] = _PRIMARY_FILES["core-credential-connection.xml"]
_PRIMARY_FILES["credential-connection-outbound.xml"] = _PRIMARY_FILES["core-credential-connection-outbound.xml"]
_PRIMARY_FILES["credential-session-use.xml"] = _PRIMARY_FILES["core-credential-session-use.xml"]
_PRIMARY_FILES["backend-audit.xml"] = _PRIMARY_FILES["core-backend-audit.xml"]
_PRIMARY_FILES["audit-body-model.xml"] = _PRIMARY_FILES["core-audit-body-model.xml"]
_PRIMARY_FILES["audit-context.xml"] = _PRIMARY_FILES["core-audit-context.xml"]
_PRIMARY_FILES["realtime-observer.xml"] = _PRIMARY_FILES["core-realtime-observer.xml"]
for _suffix in (".xml", ".cs"):
    _PRIMARY_FILES["pii" + _suffix] = Path("operation-policies/pii") / ("pii" + _suffix)
    _PRIMARY_FILES["document-analysis" + _suffix] = Path("operation-policies/document-analysis") / ("document-analysis" + _suffix)
    _PRIMARY_FILES["document-analysis-status" + _suffix] = Path("operation-policies/document-analysis") / ("document-analysis-status" + _suffix)
    _PRIMARY_FILES["credential-telemetry" + _suffix] = Path("snippets/audit/credential-telemetry") / ("credential-telemetry" + _suffix)
    _PRIMARY_FILES["live-create" + _suffix] = Path("operation-policies/live") / ("live-create" + _suffix)
    for _operation in ("realtime-create", "realtime-hangup"):
        _PRIMARY_FILES[_operation + _suffix] = Path("operation-policies/realtime") / (_operation + _suffix)
    _PRIMARY_FILES["realtime-owner-context" + _suffix] = Path("snippets/authentication/realtime-owner-context") / ("realtime-owner-context" + _suffix)
    _PRIMARY_FILES["credential-challenge" + _suffix] = Path("snippets/authentication/credential-challenge") / ("credential-challenge" + _suffix)
for _suffix in (".xml", ".cs"):
    _PRIMARY_FILES["subscription-profile" + _suffix] = Path("snippets/quota/subscription-profile") / ("subscription-profile" + _suffix)
    _PRIMARY_FILES["rate-limit-error" + _suffix] = Path("snippets/quota/rate-limit-error") / ("rate-limit-error" + _suffix)
    _PRIMARY_FILES["mcp-rate-cleanup" + _suffix] = Path("snippets/quota/mcp-rate-cleanup") / ("mcp-rate-cleanup" + _suffix)
for _suffix in (".xml", ".cs"):
    for _operation in ("start", "status", "session-commit", "logout", "logout-all", "disconnect"):
        _PRIMARY_FILES["credential-login/" + _operation + _suffix] = Path("operation-policies/credential-login") / (_operation + _suffix)
for _template in ("gateway.xml", "protocol.xml", "mcp.xml"):
    _PRIMARY_FILES[_template] = Path("api-templates") / _template
_PRIMARY_FILES["credential-login/launch.xml"] = Path("operation-policies/credential-login/launch.xml")
_PRIMARY_FILES["credential-login/launch-post.xml"] = Path("operation-policies/credential-login/launch-post.xml")
_PRIMARY_FILES["credential-login/identity-callback.xml"] = Path("operation-policies/credential-login/identity-callback.xml")
for _suffix in (".xml", ".cs"):
    _PRIMARY_FILES["credential-login/callback" + _suffix] = Path("operation-policies/credential-login") / ("callback" + _suffix)


def policy_fixture_path(policies: Path, filename: str) -> Path:
    """固定部品は指定位置、他の既存資材は指定相対位置を返す。探索は行わない。"""
    return policies / _PRIMARY_FILES.get(filename, filename)

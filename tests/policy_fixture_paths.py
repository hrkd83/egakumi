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
        ("core-gateway-telemetry", "fragments/audit"),
        ("core-api-settings", "fragments/settings"),
    )
    for suffix in (".xml", ".cs")
}
_PRIMARY_FILES["core-entra-authentication-error.xml"] = Path("fragments/authentication/core-entra-authentication-error/core-entra-authentication-error.xml")


def policy_fixture_path(policies: Path, filename: str) -> Path:
    """固定部品は指定位置、他の既存資材は指定相対位置を返す。探索は行わない。"""
    return policies / _PRIMARY_FILES.get(filename, filename)

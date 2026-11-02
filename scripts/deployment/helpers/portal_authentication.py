"""ポータルのセッション署名鍵を構築対象へ結び付け、ローカルで保護する。"""
import json
from pathlib import Path

from .grpc_lifecycle import _prepare_credential, secret_path


PURPOSE = "portal-auth"


def prepare_secret(config: dict, root: Path, write_protected) -> dict:
    """既存の環境別署名鍵を再利用し、新規の場合だけ十分な乱数で生成する。

    値は画面やparamsへ出さず、呼出元が保護したBicepパラメーター経由で
    Key Vaultへ登録する。Entraアプリのclient secretとは別の鍵である。
    """
    return {"portalAuthenticationSecret": _prepare_credential(config, root, PURPOSE, write_protected)}


def remove_secret(config: dict, root: Path) -> None:
    """対象環境のAzureリソース撤去を確認した呼出元からのみ署名鍵を削除する。"""
    path = secret_path(config, root, PURPOSE)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Portal signing credential path must not use symlinks")
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("Portal signing credential file is invalid") from None
        if not isinstance(current, dict) or any(current.get(key) != config[key]
                for key in ("subscriptionId", "azureTenantId", "resourceStem")):
            raise ValueError("Portal signing credential belongs to a different environment")
    path.unlink(missing_ok=True)

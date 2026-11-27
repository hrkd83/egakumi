"""Core配備で使うARM応答の状態とリージョンを照合する。"""


class DeploymentError(RuntimeError):
    """構築の停止理由を、秘密値を含めず呼出元へ返す例外。"""
    pass


def same_azure_location(actual: str, expected: str) -> bool:
    """ARMの表示名（Japan East）と設定コード（japaneast）を照合する。

    APIによって同じリージョンが空白付きで返るため、ASCII空白と大小文字だけを
    正規化する。別リージョン・記号の違いは一致にせず、所有IDやタグの検査も維持する。
    """
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    return actual.replace(" ", "").casefold() == expected.replace(" ", "").casefold()


def require_success(response: dict, operation: str, allowed=(200, 201, 204)) -> dict:
    """許可したHTTP状態か確認し、エラー本文を表示せず構造化したbodyを返す。"""
    if response["statusCode"] not in allowed:
        raise DeploymentError(f"{operation} failed (HTTP {response['statusCode']})")
    return response.get("body") or {}

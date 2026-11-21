"""公開Documentスキーマの対応版を、配備設定と生成ポリシーへ渡す。"""
from __future__ import annotations

import json
from pathlib import Path

from .paths import REPOSITORY


DOCUMENT_API_VERSION_MARKER = "__DOCUMENT_API_VERSION__"


def load_document_api_version(root: Path = REPOSITORY) -> str:
    """公開契約の単一版を読む。複数版から先頭を暗黙選択しない。"""
    schema = json.loads((Path(root) / "apps/gateway/apim/schemas/document-analysis.json").read_text(encoding="utf-8"))
    versions = schema["schemas"]["DocumentAnalysisResult"]["properties"]["analyzeResult"]["properties"]["apiVersion"]["enum"]
    if not isinstance(versions, list) or len(versions) != 1 or not isinstance(versions[0], str) or not versions[0].strip():
        raise ValueError("The public Document contract must specify exactly one nonempty API version")
    return versions[0]


# 公開資材を配備プロセスで一度読み、要求ごとの設定読取りへ持ち込まない。
DOCUMENT_API_VERSION = load_document_api_version()

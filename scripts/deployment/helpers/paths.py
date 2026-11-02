"""作業ディレクトリーに依存せず、構築対象リポジトリのルートを求める。"""
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[3]

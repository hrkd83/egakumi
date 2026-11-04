"""公開資材の読取先と、環境別の非公開入出力先を分ける。"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import sys


REPOSITORY = Path(__file__).resolve().parents[3]


def checked_environment(value: str) -> str:
    """環境名を単一の安全なディレクトリー名として検査する。"""
    if not isinstance(value, str) or len(value) > 12 or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value):
        raise ValueError("Select a valid environment name (lowercase letters, digits and internal hyphens; at most 12 characters)")
    return value


def _no_links(path: Path, boundary: Path) -> None:
    """運用領域から外部へのリンクや、通常ファイルを親とする参照を拒否する。"""
    current = path
    while current != boundary:
        if current.is_symlink():
            system_alias = sys.platform == "darwin" and (
                current == Path("/var") and current.resolve() == Path("/private/var")
                or current == Path("/tmp") and current.resolve() == Path("/private/tmp"))
            if not system_alias:
                raise ValueError("Local runtime paths must not use symbolic links")
        if current != path and current.exists() and not current.is_dir():
            raise ValueError("Local runtime parent must be a regular directory")
        if current.parent == current:
            raise ValueError("Local runtime path is outside the selected workspace")
        current = current.parent


def private_output(path: Path, repository: Path = REPOSITORY) -> Path:
    """repo内への実行出力は直下の.local/.azureだけに許し、外部参照もリンクを拒否する。"""
    root = Path(repository).resolve()
    expanded = Path(path).expanduser()
    absolute = Path(os.path.abspath(expanded))
    boundary = root if absolute.is_relative_to(root) else Path(absolute.anchor)
    _no_links(absolute, boundary)
    resolved = absolute.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] not in (".local", ".azure"):
        raise ValueError("Runtime files inside the checkout must be stored under .local or .azure")
    return resolved


def private_parent(path: Path, repository: Path = REPOSITORY) -> Path:
    """運用フォルダーを700で準備する。既存の共有権限を黙って変更しない。"""
    target = private_output(path, repository)
    root = Path(repository).resolve()
    if target.is_relative_to(root):
        boundary = root
    else:
        boundary = target.parent
        while not boundary.exists():
            boundary = boundary.parent
    chain = []
    current = target.parent
    while current != boundary:
        chain.append(current)
        current = current.parent
    for directory in reversed(chain):
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ValueError("Local runtime folders must be regular directories")
        if os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Local runtime folders require owner-only permissions (chmod 700)")
    return target


@dataclass(frozen=True)
class LocalPaths:
    """1つのazd環境に対応するparams、秘密、state、生成物の保存先。"""
    repository: Path
    environment: str

    def __post_init__(self):
        object.__setattr__(self, "repository", Path(self.repository).resolve())
        checked_environment(self.environment)
        _no_links(self.root, self.repository)

    @property
    def root(self) -> Path:
        return self.repository / ".local" / self.environment

    @property
    def params(self) -> Path:
        return self.root / "params.local.json"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def parameters(self) -> Path:
        return self.root / "generated/main.parameters.json"

    def ensure(self) -> None:
        """初回の空の運用フォルダーを保護し、既存内容は保持する。"""
        for name in ("secrets", "state", "generated", "evidence"):
            private_parent(self.root / name / ".permissions-check", self.repository)
        private_parent(self.repository / ".azure/.permissions-check", self.repository)

    def checked(self, path: Path) -> Path:
        """明示指定でも別環境や公開資材側のファイルを読み書きしない。"""
        target = Path(path).expanduser()
        target = target if target.is_absolute() else self.repository / target
        target = private_output(target, self.repository)
        if not target.is_relative_to(self.root):
            raise ValueError("Runtime input/output must belong to .local/<selected-environment>")
        return target

    def check_selection(self, configuration: dict) -> None:
        """azdの選択とparamsの配備先を照合し、値を上書きして取り繕わない。"""
        for variable, key in (("AZURE_ENV_NAME", "environmentName"),
                              ("AZURE_SUBSCRIPTION_ID", "subscriptionId"),
                              ("AZURE_TENANT_ID", "azureTenantId")):
            actual = os.environ.get(variable)
            if actual and actual.casefold() != str(configuration[key]).casefold():
                raise ValueError("Selected azd environment differs from the configuration: " + variable)
        reference = os.environ.get("EGAKUMI_ARM_PARAMETERS_FILE")
        expected = f".local/{self.environment}/generated/main.parameters.json"
        if reference and reference != expected:
            raise ValueError("Selected ARM parameter reference differs from the local environment")
        azure = self.repository / ".azure"
        _no_links(azure / self.environment / ".env", self.repository)
        selection = azure / "config.json"
        if not os.environ.get("AZURE_ENV_NAME") and selection.exists():
            _no_links(selection, self.repository)
            try:
                selected = json.loads(selection.read_text(encoding="utf-8")).get("defaultEnvironment")
            except (OSError, ValueError, AttributeError):
                raise ValueError("Saved azd environment selection could not be read") from None
            if selected and selected != self.environment:
                raise ValueError("Select the azd environment matching the local parameters")


def local_paths(configuration: dict, repository: Path = REPOSITORY) -> LocalPaths:
    """検査済み設定から環境別の保存先を求める。資材ROOTは変更しない。"""
    return LocalPaths(repository, configuration["environmentName"])


def configuration_path(repository: Path = REPOSITORY) -> Path:
    """明示したparams、azdの環境名、保存した既定環境の順に入力を選ぶ。"""
    root = Path(repository).resolve()
    explicit = os.environ.get("WORKFORCE_CONFIG")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_absolute() else root / path
    environment = os.environ.get("AZURE_ENV_NAME")
    if not environment:
        selection = root / ".azure/config.json"
        _no_links(selection, root)
        try:
            environment = json.loads(selection.read_text(encoding="utf-8")).get("defaultEnvironment")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            raise ValueError("Saved azd environment selection could not be read") from None
    if not environment:
        raise ValueError("Select an azd environment or pass --config for .local/<environment>/params.local.json")
    return LocalPaths(root, environment).params

"""構築で使うPowerShell実行ファイルとモジュール検索先を決める。

明示された実行ファイル、PATH上のpwsh、リポジトリ内の同梱ランタイムの順に
探索する。実行ファイル名と引数を混在させた指定は受け付けない。
PSModulePathは呼出元の環境を直接変更せず、独立した環境辞書として返す。
ツールやモジュールのインストール、Azureログイン・通信、課金操作は行わない。
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil

from .paths import REPOSITORY


LOCAL_MODULES = REPOSITORY / ".tools/powershell/modules"
PORTABLE_RUNTIME = REPOSITORY / ".tools/powershell/7.6.7"


def _executable(value: str) -> str | None:
    """指定を実行ファイル1つとして解決し、空白で引数を混ぜた指定を受け付けない。"""
    expanded = os.path.expanduser(value)
    found = shutil.which(expanded)
    if found:
        return str(Path(found).resolve())
    return None


def locate_powershell() -> str:
    """明示指定、インストール済みpwsh、リポジトリ内ランタイムの順に実行先を選ぶ。"""
    override = os.environ.get("WORKFORCE_PWSH")
    if override is not None:
        executable = _executable(override.strip()) if override.strip() else None
        if executable is None:
            raise RuntimeError("WORKFORCE_PWSH must identify an executable PowerShell file")
        return executable
    installed = _executable("pwsh")
    if installed:
        return installed
    for name in ("pwsh.exe", "pwsh") if os.name == "nt" else ("pwsh",):
        executable = _executable(str(PORTABLE_RUNTIME / name))
        if executable:
            return executable
    raise RuntimeError("PowerShell 7.2 or later is required; install pwsh or set WORKFORCE_PWSH")


def powershell_environment(powershell: str | Path | None = None) -> dict[str, str]:
    """実行先のモジュールと既存検索先を含む、独立した子プロセス環境を返す。

    空の相対検索先や重複を整理し、呼出元の環境変数を直接変更しない。
    """
    executable = Path(powershell if powershell is not None else locate_powershell()).resolve()
    environment = os.environ.copy()
    candidates = [str(LOCAL_MODULES), str(executable.parent / "Modules")]
    candidates.extend(environment.get("PSModulePath", "").split(os.pathsep))
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            continue
        normalized = os.path.normpath(str(path))
        key = os.path.normcase(normalized)
        if key not in seen:
            unique.append(normalized)
            seen.add(key)
    environment["PSModulePath"] = os.pathsep.join(unique)
    return environment

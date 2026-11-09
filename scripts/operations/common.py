"""通常保守の対象照合・ARM通信・秘密を表示しない実行と保護保存。"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from contextlib import contextmanager

from deployment.helpers.apim_naming import apim_instance_id
from deployment.helpers.cleanup import CleanupError, _validate_state
from deployment.helpers.apim_generations import assert_current_instance
from deployment.helpers.configuration import load_configuration
from deployment.helpers.paths import LocalPaths, local_paths, private_parent
from deployment.lifecycle import ArmTransport


class OperationError(RuntimeError):
    """外部の本文・秘密・コマンド引数を含めない運用エラー。"""

    def __init__(self, message: str, *, exit_code: int | None = None):
        super().__init__(message)
        self.exit_code = exit_code


def poll_delay(response: dict, default: float = 5) -> float:
    """ARMの秒数Retry-Afterを短縮せず読む。待機予算を超える値は呼出側で停止する。"""
    if (type(default) not in (int, float) or not math.isfinite(default) or default < 0):
        raise OperationError("Invalid polling interval")
    headers = response.get("headers") or {}
    if not isinstance(headers, dict):
        raise OperationError("Invalid polling response headers")
    values = [v for k, v in headers.items() if isinstance(k, str) and k.casefold() == "retry-after"]
    if not values:
        return default
    if len(values) != 1 or not isinstance(values[0], str):
        raise OperationError("Ambiguous polling interval")
    digits = values[0].strip(" \t")
    if not re.fullmatch(r"[0-9]+", digits):
        raise OperationError("Unsupported polling interval")
    digits = digits.lstrip("0") or "0"
    return max(default, int(digits)) if len(digits) <= 8 else math.inf


@dataclass
class OperationContext:
    """同じ環境のparams・所有state・保存領域を束ねる。通信は必要時だけ開始する。"""

    config_path: Path
    config: dict
    state: dict
    workspace: LocalPaths
    transport: Any = None

    @property
    def state_path(self) -> Path:
        """現在のCore配備記録の場所を返す。"""
        return self.workspace.state / "latest.json"

    @classmethod
    def load(cls, config_path: str | Path) -> OperationContext:
        """保護入力とCore stateを照合する。Azure通信・入力の上書きはしない。"""
        try:
            config = load_configuration(config_path)
            workspace = local_paths(config)
            checked = workspace.checked(Path(config_path))
            workspace.check_selection(config)
            state_path = workspace.checked(workspace.state / "latest.json")
            for path in (checked, state_path):
                info = path.lstat()
                if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                        or os.name != "nt" and (stat.S_IMODE(info.st_mode) != 0o600
                                               or info.st_uid != os.geteuid())):
                    raise ValueError("Protected input requires owner-only regular files")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            _validate_state(config, state)
            assert_current_instance(config, workspace)
            if state.get("apimInstanceId") != apim_instance_id(config):
                raise ValueError("The selected APIM instance differs")
            return cls(checked, config, state, workspace)
        except (CleanupError, ValueError, OSError, KeyError, TypeError):
            raise OperationError("Check the selected protected inputs and Core state") from None

    @contextmanager
    def locked(self):
        """このcheckoutで同じ環境の運用スクリプトを重ねて実行しない。"""
        path = self.output_path(self.workspace.root / ".operations.lock")
        try:
            private_parent(path, self.workspace.repository)
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except (ValueError, OSError):
            raise OperationError("The protected operation lock could not be opened") from None
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode)
                    or os.name != "nt" and (stat.S_IMODE(info.st_mode) != 0o600
                                           or info.st_uid != os.geteuid())):
                raise OperationError("The operation lock is not an owner-only regular file")
            try:
                if os.name == "nt":
                    import msvcrt
                    if info.st_size == 0:
                        os.write(descriptor, b"\0")
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise OperationError("Another operation is already running for this environment") from None
            yield
        finally:
            os.close(descriptor)

    def _resource_path(self, resource_id: str) -> str:
        """所有5RG内の正確なARMパスだけを受け付ける。任意URLへは広げない。"""
        if not isinstance(resource_id, str):
            raise OperationError("An owned ARM resource is required")
        parsed = urlsplit(resource_id)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise OperationError("Use an owned ARM resource ID without a query")
        if (not resource_id.startswith("/") or any(c in resource_id for c in ("%", "\\"))
                or any(ord(c) <= 32 for c in resource_id)
                or any(part in ("", ".", "..") for part in resource_id.split("/")[1:])):
            raise OperationError("The ARM resource path is invalid")
        root = f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
        if not resource_id.casefold().startswith(root.casefold()):
            raise OperationError("The resource belongs to another subscription")
        group = resource_id[len(root):].split("/", 1)[0]
        if group.casefold() not in {v.casefold() for v in self.config["resourceGroupNames"].values()}:
            raise OperationError("The resource belongs to another resource group")
        return resource_id

    def request(self, method: str, resource_id: str, api_version: str,
                body=None, headers=None) -> dict:
        """対象限定のARM要求を送る。変更要求は共通通信でも再送しない。"""
        path = self._resource_path(resource_id)
        if not isinstance(api_version, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:-preview)?", api_version):
            raise OperationError("An explicit ARM API version is required")
        if method not in {"GET", "PUT", "PATCH", "POST", "DELETE"}:
            raise OperationError("Unsupported ARM operation")
        # ArmTransport自身が起動する認証CLIにも、応答のファイルログ抑止を継承する。
        os.environ["AZURE_LOGGING_ENABLE_LOG_FILE"] = "false"
        try:
            if self.transport is None:
                self.transport = ArmTransport(self.config)
            return self.transport.request(method, path + "?api-version=" + api_version,
                                          body=body, headers=headers)
        except Exception:
            raise OperationError("ARM response is unknown; inspect the resource before retrying") from None

    def service_id(self, key: str) -> str:
        """配備記録に保存したサービスのIDを返す。名前の探索は行わない。"""
        if key not in {"apim", "foundry", "keyVault"}:
            raise OperationError("Select a recorded service")
        try:
            return self._resource_path(self.state["serviceResourceIds"][key])
        except (KeyError, TypeError):
            raise OperationError("The service is missing from Core state") from None

    def verify_resource(self, resource_id: str, api_version: str,
                        expected_type: str | None = None) -> dict:
        """現物ID・種類と親RGの所有タグを確認してから資源の本文を返す。"""
        path = self._resource_path(resource_id)
        group_id = "/".join(path.split("/")[:5])
        # 各操作の開始時に親RGを読む。子資源がタグ非対応でも所属を確認する。
        group = self.request("GET", group_id, "2024-03-01")
        group_body = group.get("body") or {}
        tags = group_body.get("tags") or {}
        if (group.get("statusCode") != 200
                or str(group_body.get("id", "")).casefold() != group_id.casefold()
                or tags.get("product") != self.config["productName"]
                or tags.get("environment") != self.config["environmentName"]):
            raise OperationError("The parent resource group ownership could not be verified")
        response = self.request("GET", path, api_version)
        value = response.get("body")
        if (response.get("statusCode") != 200 or not isinstance(value, dict)
                or str(value.get("id", "")).casefold() != path.casefold()
                or expected_type and str(value.get("type", "")).casefold() != expected_type.casefold()):
            raise OperationError("The selected resource identity could not be verified")
        return value

    def output_path(self, path: str | Path) -> Path:
        """出力を現在の環境の保護領域へ限定する。"""
        try:
            return self.workspace.checked(Path(path))
        except (OSError, ValueError):
            raise OperationError("Output must belong to the selected protected environment") from None

    def save_private(self, path: str | Path, value,
                     overwrite: bool = False) -> Path:
        """textまたはJSONを600で原子的に保存する。既定では既存ファイルを置換しない。"""
        target = self.output_path(path)
        temporary = None
        try:
            private_parent(target, self.workspace.repository)
            if target.exists():
                info = target.lstat()
                if (not overwrite or not stat.S_ISREG(info.st_mode)
                        or os.name != "nt" and (stat.S_IMODE(info.st_mode) != 0o600
                                               or info.st_uid != os.geteuid())):
                    raise OperationError("Protected output already exists or has unsafe permissions")
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            descriptor, name = tempfile.mkstemp(prefix=".operation-", dir=target.parent)
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if overwrite:
                os.replace(temporary, target)
            else:
                # linkは既存の宛先へ上書きしない。秘密を途中状態の宛先へ書かない。
                os.link(temporary, target, follow_symlinks=False)
            return target
        except OperationError:
            raise
        except (OSError, ValueError, TypeError):
            raise OperationError("Protected output could not be saved") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def record(self, kind: str, payload: dict) -> Path:
        """値を表示せず、秘密を含まない段階・確認結果を環境別に記録する。"""
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,60}", kind):
            raise OperationError("Invalid operation record type")
        forbidden = {"password", "secrettext", "primarykey", "secondarykey", "clientsecret", "providersecrets", "accesstoken"}

        def check(value):
            if isinstance(value, dict):
                if any(str(k).casefold() in forbidden for k in value):
                    raise OperationError("Secret values must not be written to operation records")
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(payload)
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.workspace.state / "operations" / f"{now}-{uuid.uuid4().hex[:8]}-{kind}.json"
        return self.save_private(target, {"kind": kind, **payload})

    def run_cli(self, argv: list[str], *, timeout_seconds: float = 3600) -> str:
        """外部CLIをshellを介さず実行し、成功stdoutだけを呼出元のメモリーへ返す。"""
        if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
            raise OperationError("Invalid operation command")
        try:
            result = subprocess.run(argv, capture_output=True, text=True, check=False,
                                    timeout=timeout_seconds,
                                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                                         "AZURE_LOGGING_ENABLE_LOG_FILE": "false"})
        except subprocess.TimeoutExpired:
            raise OperationError("The command timed out; inspect the running operation before retrying") from None
        except (OSError, ValueError):
            raise OperationError("The operation command could not be started") from None
        if result.returncode:
            raise OperationError("The operation command failed; inspect its current state before retrying",
                                 exit_code=result.returncode)
        return result.stdout

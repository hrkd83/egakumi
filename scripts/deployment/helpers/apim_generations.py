"""APIM識別子を稼働中は固定し、撤去確認後の次回準備でだけ更新する。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from contextlib import contextmanager

from .apim_naming import INSTANCE_ID, apim_instance_id, apim_name, generate_apim_instance_id
from .paths import LocalPaths, private_parent


def _read(paths: LocalPaths, path: Path) -> dict:
    target = paths.checked(path)
    if not target.is_file() or (os.name != "nt" and stat.S_IMODE(target.stat().st_mode) != 0o600):
        raise ValueError("APIM preparation records require regular owner-only files")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("APIM preparation records could not be verified") from None
    if not isinstance(value, dict):
        raise ValueError("APIM preparation records must contain JSON objects")
    return value


def _write(paths: LocalPaths, path: Path, value: dict) -> None:
    target = private_parent(paths.checked(path), paths.repository)
    descriptor, temporary = tempfile.mkstemp(prefix=".apim-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _history(paths: LocalPaths) -> Path:
    return paths.checked(paths.root / "history/apim")


def used_instance_ids(paths: LocalPaths) -> set[str]:
    """過去世代のディレクトリー名だけを読み、過去の識別子を再発行しない。"""
    history = _history(paths)
    if not history.exists():
        return set()
    identifiers = set()
    for entry in history.iterdir():
        paths.checked(entry)
        if not entry.is_dir() or not INSTANCE_ID.fullmatch(entry.name):
            raise ValueError("APIM instance history could not be verified")
        identifiers.add(entry.name)
    return identifiers


def verify_state(config: dict, state: dict) -> None:
    """paramsとstateの世代・配置を照合する。旧形式への推測はしない。"""
    stem = config.get("resourceStem") or f"{config['environmentName']}-{config['productName']}"
    expected = {"subscriptionId": config["subscriptionId"], "azureTenantId": config["azureTenantId"],
                "resourceStem": stem, "apimInstanceId": apim_instance_id(config)}
    if any(not isinstance(state.get(key), str) or state[key].casefold() != str(value).casefold()
           for key, value in expected.items()):
        raise ValueError("Core state belongs to a different APIM instance; preserve its parameters and complete its cleanup first")
    services = state.get("serviceResourceIds", {})
    if not isinstance(services, dict) or state.get("apimInstanceId") != expected["apimInstanceId"]:
        raise ValueError("Core state has invalid APIM instance metadata")
    actual = services.get("apim")
    expected_id = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/rg-{stem}-svc"
                   f"/providers/Microsoft.ApiManagement/service/{apim_name(config)}")
    if actual is not None and (not isinstance(actual, str) or actual.casefold() != expected_id.casefold()):
        raise ValueError("Core APIM resource ID differs from the selected APIM instance")


def _verify_down(config: dict, state: dict, receipt: dict) -> None:
    verify_state(config, state)
    for key in ("subscriptionId", "azureTenantId", "resourceStem", "apimInstanceId"):
        if receipt.get(key) != state.get(key):
            raise ValueError("The postdown record belongs to a different APIM instance")
    result = receipt.get("result", {})
    completed = {"complete", "complete-with-unverified-api-center-retention",
                 "complete-with-protected-retention", "complete-with-api-center-retention"}
    cleanup_status = result.get("status") if isinstance(result, dict) else None
    if cleanup_status == "resources-absent-with-unverified-credential-manager-release":
        cleanup_status = result.get("resourceCleanupStatus")
    if not isinstance(result, dict) or result.get("phase") != "postdown" or cleanup_status not in completed:
        raise ValueError("A completed postdown record is required before preparing another APIM instance")
    groups = state.get("resourceGroupNames", {})
    if not isinstance(groups, dict) or set(groups) != {"id", "nw", "monitor", "svc", "maint"}:
        raise ValueError("The previous Core resource group inventory is incomplete")
    if any(name != f"rg-{state['resourceStem']}-{kind}" for kind, name in groups.items()):
        raise ValueError("The previous Core resource groups differ from this environment")
    expected = {f"/subscriptions/{state['subscriptionId']}/resourceGroups/{name}".casefold()
                for name in groups.values()}
    rows = result.get("resourceGroups", [])
    if (not isinstance(rows, list) or len(rows) != len(expected)
            or {str(row.get("resourceId", "")).casefold() for row in rows if isinstance(row, dict)} != expected
            or any(not isinstance(row, dict) or row.get("status") != "absent" or row.get("httpStatus") != 404 for row in rows)):
        raise ValueError("All previous Core resource groups must have confirmed GET 404 records")
    inventory = state.get("applicationInfrastructure", [])
    managed = result.get("applicationInfrastructure", [])
    if (not isinstance(inventory, list) or not isinstance(managed, list)
            or any(not isinstance(row, dict) or not isinstance(row.get("resourceGroupId"), str) for row in inventory)
            or len(managed) != len(inventory)
            or {row.get("resourceGroupId") for row in managed if isinstance(row, dict)}
            != {row.get("resourceGroupId") for row in inventory if isinstance(row, dict)}
            or any(not isinstance(row, dict) or row.get("status") != "absent" or row.get("httpStatus") != 404 for row in managed)):
        raise ValueError("Previous Container Apps managed resource groups must be confirmed absent")


def assert_preparation_available(paths: LocalPaths) -> None:
    """入力消失を新規環境と誤認して別IDを発行しない。"""
    if used_instance_ids(paths) or (paths.state.exists() and any(paths.state.iterdir())):
        raise ValueError("Existing deployment records remain; restore the exact local parameters instead of generating another APIM identifier")


def assert_current_instance(config: dict, paths: LocalPaths) -> None:
    """課金処理の前に、世代の取り違えと撤去済みIDの再使用を止める。"""
    identifier = apim_instance_id(config)
    if identifier in used_instance_ids(paths):
        raise ValueError("This APIM identifier is retired; finish explicit --new-apim-instance preparation")
    latest = paths.checked(paths.state / "latest.json")
    if not latest.exists() and paths.checked(paths.state / "cleanup-postdown.json").exists():
        raise ValueError("Restore the previous Core state before preparing another APIM instance")
    if latest.exists():
        state = _read(paths, latest)
        verify_state(config, state)
        receipt = paths.checked(paths.state / "cleanup-postdown.json")
        if receipt.exists():
            down = _read(paths, receipt)
            _verify_down(config, state, down)
            raise ValueError("This APIM instance was removed; prepare --new-apim-instance and update the Entra CM redirect URI before building")


@contextmanager
def _preparation_lock(paths: LocalPaths):
    """同時実行を拒否する。プロセス終了時にはOSがロックを解放する。"""
    target = private_parent(paths.checked(paths.root / ".apim-preparation.lock"), paths.repository)
    descriptor = os.open(target, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("APIM preparation requires a regular owner-only lock file")
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
            raise ValueError("Another APIM instance preparation is already running") from None
        # closeは例外・正常終了の両方でOSロックを解放する。
        yield
    finally:
        os.close(descriptor)


def prepare_next_instance(paths: LocalPaths, config: dict) -> dict:
    with _preparation_lock(paths):
        if _read(paths, paths.params) != config:
            raise ValueError("Local parameters changed during APIM preparation; reload them before retrying")
        return _prepare_next_instance(paths, config)


def _prepare_next_instance(paths: LocalPaths, config: dict) -> dict:
    """旧state一式を保存し、明示した次回構築の識別子だけを更新する。"""
    old_id = apim_instance_id(config)
    archive = _history(paths) / old_id
    latest = paths.checked(paths.state / "latest.json")
    if latest.exists():
        state = _read(paths, latest)
        receipt = _read(paths, paths.state / "cleanup-postdown.json")
        _verify_down(config, state, receipt)
        if archive.exists():
            if (archive / "state").exists():
                raise ValueError("Active and archived state both exist; preserve all APIM preparation records")
            plan = _read(paths, archive / "preparation.json")
            next_id = plan.get("nextApimInstanceId")
            apim_instance_id({"services": {"apimInstanceId": next_id}})
            if (plan.get("previousApimInstanceId") != old_id
                    or plan.get("environmentName") != config["environmentName"]
                    or next_id in used_instance_ids(paths)):
                raise ValueError("Interrupted APIM preparation could not be verified")
        else:
            next_id = generate_apim_instance_id(used_instance_ids(paths) | {old_id})
            plan = {"previousApimInstanceId": old_id, "nextApimInstanceId": next_id,
                    "environmentName": config["environmentName"]}
            _write(paths, archive / "preparation.json", plan)
        paths.state.rename(archive / "state")
    else:
        # 強制終了がstate移動とparamsの置換の間に起きても、同じ次世代IDで再開する。
        state = _read(paths, archive / "state/latest.json")
        receipt = _read(paths, archive / "state/cleanup-postdown.json")
        _verify_down(config, state, receipt)
        plan = _read(paths, archive / "preparation.json")
        next_id = plan.get("nextApimInstanceId")
        apim_instance_id({"services": {"apimInstanceId": next_id}})
        if plan.get("previousApimInstanceId") != old_id or plan.get("environmentName") != config["environmentName"]:
            raise ValueError("Interrupted APIM preparation belongs to a different environment")
        if next_id in used_instance_ids(paths):
            raise ValueError("Interrupted APIM preparation would reuse a previous identifier")
        if paths.state.exists() and any(paths.state.iterdir()):
            raise ValueError("Active state appeared during APIM preparation; preserve all records")
    updated = {**config, "services": {**config["services"], "apimInstanceId": next_id}}
    _write(paths, paths.params, updated)
    paths.ensure()
    return {"status": "new-apim-instance-prepared", "parameters": str(paths.params.relative_to(paths.repository)),
            "apimInstanceId": next_id, "apimName": apim_name(updated),
            "history": str(archive.relative_to(paths.repository)), "azureAccessPerformed": False,
            "next": "Display registration URIs from these parameters and update the Entra CM redirect URI before azd up."}

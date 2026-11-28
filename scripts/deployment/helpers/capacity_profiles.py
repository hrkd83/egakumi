"""標準プロファイルからサービス別の容量設定を合成する。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .paths import STANDARD_CONFIG_DIRECTORY, STANDARD_CONFIG_PROFILES


CAPACITY_FILES = (
    "application-gateway", "apim", "firewall", "foundry", "azure-sql", "key-vault",
    "container-apps", "storage", "azure-monitor", "management-vm", "credential-manager",
)
# General Purpose provisioned, standard-series (Gen5).
# https://learn.microsoft.com/azure/azure-sql/database/resource-limits-vcore-single-databases
BROKER_SQL_MAX_DATA_GIB = {
    2: 1024, 4: 1024, 6: 1536, 8: 2048, 10: 2048,
    12: 3072, 14: 3072, 16: 3072, 18: 3072, 20: 3072,
    24: 4096, 32: 4096, 40: 4096, 80: 4096, 128: 4096,
}


class CapacityInputError(ValueError):
    """標準プロファイルに必要なparams容量入力の欠落や不整合。"""


def _merge(target: dict, settings: dict, source: str, prefix: str = "") -> None:
    for key, value in settings.items():
        field = prefix + key
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge(target[key], value, source, field + ".")
        else:
            raise ValueError(f"Capacity file {source} duplicates {field}")


def _read_fragment(path: Path) -> dict:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError(f"Capacity file {path.name} must not use symbolic links")
    try:
        fragment = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"Capacity file {path.name} could not be read") from None
    if not isinstance(fragment, dict):
        raise ValueError(f"Capacity file {path.name} must be a JSON object")
    common = fragment.get("common")
    profiles = fragment.get("profiles")
    if (not isinstance(common, dict) or not isinstance(profiles, dict)
            or set(profiles) != set(STANDARD_CONFIG_PROFILES)
            or any(not isinstance(settings, dict) for settings in profiles.values())):
        raise ValueError(f"Capacity file {path.name} must define common settings and every standard profile")
    if set(fragment) - {"$schema", "$comment", "common", "profiles"}:
        raise ValueError(f"Capacity file {path.name} contains unsupported sections")
    return fragment


def _required_fields(values: object, keys: set[str], path: str) -> dict:
    if not isinstance(values, dict):
        raise CapacityInputError(f"{path}: is required and must be an object")
    missing = keys - values.keys()
    if missing:
        raise CapacityInputError(f"{path}: missing required fields: {', '.join(sorted(missing))}")
    if values.keys() - keys:
        raise CapacityInputError(f"{path}: contains unsupported fields")
    return values


def _quantity(value: object, minimum: int, maximum: int, path: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CapacityInputError(f"{path}: must be an integer from {minimum} to {maximum}")
    return value


def _apply_requested_capacity(settings: dict, selected: object, profile: str) -> None:
    path = "params.capacity"
    capacity = _required_fields(selected, {
        "applicationGateway", "apim", "credentialBroker", "brokerSql",
    }, path)
    app_gateway = _required_fields(capacity["applicationGateway"], {"minInstances", "maxInstances"}, path + ".applicationGateway")
    minimum_instances = _quantity(app_gateway["minInstances"], 0, 100, path + ".applicationGateway.minInstances")
    maximum_instances = _quantity(app_gateway["maxInstances"], 2, 125, path + ".applicationGateway.maxInstances")
    if minimum_instances > maximum_instances:
        raise CapacityInputError(f"{path}.applicationGateway: minInstances must not exceed maxInstances")

    apim = _required_fields(capacity["apim"], {"units"}, path + ".apim")
    units = _quantity(apim["units"], 2 if profile == "prod" else 1,
                      1 if profile == "dev" else 12, path + ".apim.units")

    broker = _required_fields(capacity["credentialBroker"], {"minReplicas", "maxReplicas"}, path + ".credentialBroker")
    minimum_replicas = _quantity(broker["minReplicas"], 1, 30, path + ".credentialBroker.minReplicas")
    maximum_replicas = _quantity(broker["maxReplicas"], minimum_replicas, 30, path + ".credentialBroker.maxReplicas")

    sql = _required_fields(capacity["brokerSql"], {"vCores", "maxDataGiB"}, path + ".brokerSql")
    vcores = sql["vCores"]
    if type(vcores) is not int or vcores not in BROKER_SQL_MAX_DATA_GIB:
        raise CapacityInputError(f"{path}.brokerSql.vCores: must be a supported General Purpose Gen5 size")
    maximum_data_gib = _quantity(sql["maxDataGiB"], 1, BROKER_SQL_MAX_DATA_GIB[vcores], path + ".brokerSql.maxDataGiB")
    broker_settings = settings.get("credentialBroker")
    if not isinstance(broker_settings, dict) or broker_settings.get("sqlSkuFamily") != "GP_Gen5":
        raise ValueError("The Broker SQL capacity file must fix sqlSkuFamily to GP_Gen5")
    sql_family = broker_settings.pop("sqlSkuFamily")

    _merge(settings, {
        "gateway": {"autoscaleMinCapacity": minimum_instances, "autoscaleMaxCapacity": maximum_instances},
        "services": {"apimCapacity": units},
        "credentialBroker": {
            "minReplicas": minimum_replicas, "maxReplicas": maximum_replicas,
            "sqlSkuName": f"{sql_family}_{vcores}", "sqlCapacity": vcores, "sqlMaxSizeGB": maximum_data_gib,
        },
    }, path)


def _apply_foundry_initial_capacity(common: dict) -> None:
    services = common.get("services")
    deployments = services.get("modelDeployments") if isinstance(services, dict) else None
    if not isinstance(deployments, list) or not deployments:
        raise ValueError("foundry.json must supply services.modelDeployments")
    for index, model in enumerate(deployments):
        if not isinstance(model, dict) or "capacity" in model:
            raise ValueError(f"foundry.json services.modelDeployments[{index}] must define a model without capacity")
        # 3環境の初期値。構築前に対象SKUの公開下限に合わせて検証します。
        model["capacity"] = 1


def compose_capacity_options(source: Path, standard: dict, *, requested_capacity: object = None) -> dict:
    """標準プロファイルのサービス設定に、必須のparams容量を合成する。"""
    if "capacityProfile" not in standard:
        if (source.parent.parts[-2:] == STANDARD_CONFIG_DIRECTORY.parts
                and source.stem in STANDARD_CONFIG_PROFILES):
            raise ValueError(f"Standard config {source.name} must declare its capacityProfile")
        return standard
    profile = standard["capacityProfile"]
    if profile not in STANDARD_CONFIG_PROFILES or source.name != f"{profile}.json":
        raise ValueError("The capacity profile must match its standard config filename")

    settings = copy.deepcopy(standard)
    settings.pop("capacityProfile")
    for name in CAPACITY_FILES:
        path = source.parent / "capacity" / f"{name}.json"
        fragment = _read_fragment(path)
        if name == "foundry":
            _apply_foundry_initial_capacity(fragment["common"])
        _merge(settings, fragment["common"], path.name)
        _merge(settings, fragment["profiles"][profile], path.name)
    _apply_requested_capacity(settings, requested_capacity, profile)
    return settings

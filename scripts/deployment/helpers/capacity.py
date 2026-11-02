"""構築前にVM・OSイメージ・モデルの利用可否と残り利用枠を読む。

利用者の設定とAzureのSKU・モデル一覧・クォータ・提供容量を突き合わせ、
不明・不足・非対応の理由をコードで返す。呼出元がAzure CLI実行関数を渡す。
再実行では、同じ環境に既に存在する割当だけを差し引き、新たに必要な量を
計算する。単なるモデル一覧への表示を、配備可能という意味にはしない。
読取のみで、モデル配備、利用枠の予約・増枠、VM作成、推論は行わない。
結果は確認時点の情報であり、その後の実配備成功を保証するものではない。
"""
from __future__ import annotations

import math
import re
from typing import Callable
from urllib.parse import urlencode


class Unverified(ValueError):
    """不足・不整合のあるメタデータを、利用可能と誤認しないための内部例外。"""


def _number(value) -> float:
    """NaN・無限大・boolを拒否し、比較できる有限の数値へ揃える。"""
    if isinstance(value, bool):
        raise Unverified()
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise Unverified() from None
    if not math.isfinite(result) or result < 0:
        raise Unverified()
    return result


def _identifier(value) -> str:
    """利用枠名など、照合に使う空でない公開識別子を検査する。"""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise Unverified()
    return value


def _entries(value) -> list:
    """一覧応答から配列を取り出し、不完全な形式を利用可能と扱わない。"""
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("value"), list) and not value.get("nextLink"):
        return value["value"]
    raise Unverified()


def _attributes(value) -> dict:
    """SKU機能一覧を辞書へ変換し、同じ機能名の矛盾を検出する。"""
    if isinstance(value, dict):
        return {str(key).lower(): item for key, item in value.items()}
    if not isinstance(value, list):
        raise Unverified()
    result = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or "value" not in item:
            raise Unverified()
        key = item["name"].lower()
        if key in result and result[key] != item["value"]:
            raise Unverified()
        result[key] = item["value"]
    return result


def _tokens(value) -> set[str]:
    """カンマ区切りなどの機能値を、大小文字に依存しない比較用集合へ揃える。"""
    if not isinstance(value, str):
        raise Unverified()
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def _true(value) -> bool:
    """Azureが文字列で返す有効値を、厳密に真偽として読む。"""
    return value is True or isinstance(value, str) and value.lower() == "true"


def _usage(records: list, name: str, requested: float) -> dict:
    """指定利用枠の現在値・上限から追加量が収まるか計算する。"""
    matches = []
    for record in records:
        if not isinstance(record, dict):
            raise Unverified()
        actual = record.get("name")
        actual = actual.get("value") if isinstance(actual, dict) else actual
        if isinstance(actual, str) and actual.lower() == name.lower():
            matches.append(record)
    if len(matches) != 1:
        raise Unverified()
    current, limit = _number(matches[0].get("currentValue")), _number(matches[0].get("limit"))
    remaining = max(0.0, limit - current)
    return {"name": name, "current": current, "limit": limit, "remaining": remaining,
            "requested": requested, "sufficient": remaining >= requested}


def _model_requirements(config: dict, deployment: dict) -> set[str]:
    """公開するAPI種別から、そのモデルに必要な機能の集合を求める。"""
    result = set()
    for api in config.get("apiDefinitions", []):
        constraints = api.get("authorization", {}).get("operationConstraints", {})
        for operation in api.get("operations", []):
            constraint = constraints.get(operation.get("name"), {})
            if deployment["name"] not in constraint.get("allowedModels", []):
                continue
            if constraint.get("kind") == "statelessResponses":
                result.add("responses")
            if "chat/completions" in operation.get("urlTemplate", "").lower():
                result.add("chatcompletion")
    return result


def _capacity_rules(limits: dict, requested: float) -> dict:
    """モデルSKUの最小・最大・刻み幅など、公開された容量制約を検査する。

    省略値やnullに勝手な標準値を与えず、実際に提示された境界だけを使う。
    """
    if not isinstance(limits, dict):
        raise Unverified()
    values = {key: None if limits.get(key) is None else _number(limits[key])
              for key in ("minimum", "maximum", "step", "default")}
    if any(value is not None and not value.is_integer() for value in values.values()):
        raise Unverified()
    if values["step"] == 0 or values["minimum"] is not None and values["maximum"] is not None and values["minimum"] > values["maximum"]:
        raise Unverified()
    allowed = limits.get("allowedValues")
    if allowed is not None:
        if not isinstance(allowed, list) or not allowed:
            raise Unverified()
        allowed = sorted({_number(value) for value in allowed})
        if any(not value.is_integer() for value in allowed):
            raise Unverified()
    sufficient = requested >= 1 and requested.is_integer()
    if values["minimum"] is not None:
        sufficient = sufficient and requested >= values["minimum"]
    if values["maximum"] is not None:
        sufficient = sufficient and requested <= values["maximum"]
    if allowed is not None:
        sufficient = sufficient and requested in allowed
    if values["step"] is not None and values["minimum"] is not None:
        sufficient = sufficient and (requested - values["minimum"]) % values["step"] == 0
    missing = [key for key in ("minimum", "maximum", "step") if values[key] is None]
    return {**values, "allowedValues": allowed, "notProvided": missing,
            "stepAlignmentVerified": values["minimum"] is not None and values["step"] is not None,
            "coverage": "published-constraints-only", "status": "partial" if missing else "verified",
            "sufficient": sufficient}


def _deployment_capacity(records: list, deployment: dict, location: str, requested: float) -> dict:
    """通常のモデル配備用容量を調べ、別用途のfine-tuning容量を代用しない。"""
    matches = []
    for entry in records:
        if not isinstance(entry, dict):
            raise Unverified()
        properties = entry.get("properties") or {}
        actual = properties.get("model") or {}
        if (str(entry.get("location", "")).lower() == location.lower()
                and properties.get("skuName") == deployment["skuName"]
                and all(actual.get(key) == deployment["model" + key.title()] for key in ("format", "name", "version"))):
            matches.append(_number(properties.get("availableCapacity")))
    if not matches or len(set(matches)) != 1:
        raise Unverified()
    available = matches[0]
    return {"status": "verified" if available >= requested else "insufficient",
            "availableCapacity": available, "requested": requested, "sufficient": available >= requested,
            "source": "modelCapacities.availableCapacity", "reservation": False}


def _existing_allocations(config: dict, resources: dict | None) -> tuple[dict | None, dict, list]:
    """同一環境の既存ARM資源だけを検査し、追加必要量から差し引ける割当を求める。"""
    if resources is None:
        return None, {}, []
    issues, models = [], {}
    if not isinstance(resources, dict) or set(resources) - {"vm", "modelDeployments"}:
        return None, {}, [{"target": "existing", "code": "EXISTING_ALLOCATION_UNVERIFIED"}]
    try:
        stem = config["environmentName"] + "-" + config["productName"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", stem):
            raise Unverified()
        scope = "/subscriptions/" + config["subscriptionId"] + "/resourceGroups/rg-" + stem
        vm_id = scope + "-maint/providers/Microsoft.Compute/virtualMachines/vm-" + stem + "-mgmt"
        vm = resources.get("vm")
        if vm is not None:
            if not isinstance(vm, dict) or str(vm.get("id", "")).lower() != vm_id.lower():
                raise Unverified()
            if str(vm.get("name", "")).lower() != ("vm-" + stem + "-mgmt").lower():
                raise Unverified()
            if str(vm.get("type", "")).lower() != "microsoft.compute/virtualmachines" or vm.get("location", "").lower() != config["location"].lower():
                raise Unverified()
            if not isinstance(vm.get("properties", {}).get("hardwareProfile", {}).get("vmSize"), str):
                raise Unverified()
        deployments = resources.get("modelDeployments", [])
        if not isinstance(deployments, list):
            raise Unverified()
        # 今回の Foundry 名に完全一致する親だけを認め、モデル名の部分だけを取り出す。
        pattern = re.compile(re.escape(scope + "-svc/providers/Microsoft.CognitiveServices/accounts/aif-" + stem)
                             + r"/deployments/([a-zA-Z0-9_.-]+)", re.IGNORECASE)
        account_id = None
        for deployment in deployments:
            if not isinstance(deployment, dict):
                raise Unverified()
            match = pattern.fullmatch(str(deployment.get("id", "")))
            if match is None or match[1].lower() != str(deployment.get("name", "")).lower():
                raise Unverified()
            if str(deployment.get("type", "")).lower() != "microsoft.cognitiveservices/accounts/deployments":
                raise Unverified()
            parent = deployment["id"].rsplit("/", 2)[0].lower()
            if account_id is not None and account_id != parent:
                raise Unverified()
            account_id = parent
            key = deployment["name"].lower()
            if key in models:
                raise Unverified()
            models[key] = deployment
        return vm, models, issues
    except (Unverified, KeyError, TypeError, AttributeError):
        return None, {}, [{"target": "existing", "code": "EXISTING_ALLOCATION_UNVERIFIED"}]


def verify_capacity(config: dict, run_json: Callable[[list[str]], object], *, existing_resources: dict | None = None) -> dict:
    """VM互換性・利用枠・モデル機能・提供容量を読取で照合し、公開結果だけを返す。

    VMサイズだけでなくOS世代やNVMe、モデル版・SKU・利用枠プールも確認する。
    同じプールを複数モデルが使う場合は合計量で判定し、読めない値は成功にしない。
    """
    subscription, location = config["subscriptionId"], config["location"]
    management = config["management"]
    report = {"status": "blocked", "operations": "read-only", "additionalAzureCostJPY": 0,
              "subscriptionId": subscription, "vm": {"status": "blocked", "location": location,
              "size": management["vmSize"]}, "models": [], "issues": []}
    existing_vm, existing_models, existing_issues = _existing_allocations(config, existing_resources)
    report["issues"].extend(existing_issues)

    def issue(target, code):
        """同じ問題コードが重複しないよう、結果のissuesへ追加する。"""
        entry = {"target": target, "code": code}
        if entry not in report["issues"]:
            report["issues"].append(entry)

    def query(arguments):
        """対象サブスクを明示して読取を実行し、秘密を含み得るCLI例外本文は引き継がない。"""
        try:
            return run_json(arguments + ["--subscription", subscription, "-o", "json"])
        except Exception:
            # CLIエラー本文には資格情報や応答内容が入り得るため、固定の確認不能へ変換する。
            raise Unverified() from None

    # 1. VMの提供制限・CPU利用枠・ディスク方式を確認する。リージョン制限と
    # ゾーン限定の制限を分け、使わないゾーンの制限だけで誤って停止しない。
    vm = report["vm"]
    sku = None
    try:
        records = _entries(query(["az", "vm", "list-skus", "--location", location, "--size", management["vmSize"],
                                  "--resource-type", "virtualMachines", "--all"]))
        matches = [entry for entry in records if isinstance(entry, dict)
                   and entry.get("name") == management["vmSize"]
                   and entry.get("resourceType", "virtualMachines").lower() == "virtualmachines"]
        if len(matches) != 1:
            raise Unverified()
        sku = matches[0]
        if not isinstance(sku.get("locations"), list) or location.lower() not in [str(v).lower() for v in sku["locations"]]:
            raise Unverified()
        restrictions = sku.get("restrictions")
        if not isinstance(restrictions, list):
            raise Unverified()
        vm["zoneOnlyRestrictions"] = []
        requested_zones = management.get("zones", []) or ([management["zone"]] if management.get("zone") else [])
        for restriction in restrictions:
            kind = restriction.get("type", "").lower()
            information = restriction.get("restrictionInfo") or {}
            places = information.get("locations") or restriction.get("values") or []
            if places and location.lower() not in [str(value).lower() for value in places]:
                continue
            if kind == "location":
                issue("vm", "VM_LOCATION_RESTRICTED")
            elif kind == "zone":
                zones = information.get("zones")
                if not isinstance(zones, list):
                    raise Unverified()
                vm["zoneOnlyRestrictions"].extend(str(zone) for zone in zones if str(zone).isdigit())
                if requested_zones and set(map(str, requested_zones)) & set(map(str, zones)):
                    issue("vm", "VM_ZONE_RESTRICTED")
            else:
                raise Unverified()
        capabilities = _attributes(sku.get("capabilities"))
        vcpus = _number(capabilities.get("vcpus"))
        if not vcpus.is_integer() or vcpus < 1:
            raise Unverified()
        family = _identifier(sku.get("family"))
        vm.update({"family": family, "vcpus": int(vcpus)})
        same_size = existing_vm is not None and existing_vm["properties"]["hardwareProfile"]["vmSize"].lower() == management["vmSize"].lower()
        additional_vcpus = 0 if same_size else vcpus
        vm.update({"additionalVcpus": int(additional_vcpus), "allocationCheck": "unchanged" if same_size else "full-new-size"})
        controller = management["diskControllerType"].lower()
        if controller not in _tokens(capabilities.get("diskcontrollertypes")):
            issue("vm", "VM_DISK_CONTROLLER_UNSUPPORTED")
        if controller == "nvme" and "v2" not in _tokens(capabilities.get("hypervgenerations")):
            issue("vm", "VM_GENERATION_UNSUPPORTED")
        for setting, capability, code in (
            ("encryptionAtHost", "encryptionathostsupported", "VM_HOST_ENCRYPTION_UNSUPPORTED"),
            ("enableAcceleratedNetworking", "acceleratednetworkingenabled", "VM_ACCELERATED_NETWORKING_UNSUPPORTED"),
        ):
            if management.get(setting) and not _true(capabilities.get(capability)):
                issue("vm", code)
        usage = _entries(query(["az", "vm", "list-usage", "--location", location]))
        vm["quota"] = [_usage(usage, key, additional_vcpus) for key in (family, "cores")]
        for quota in vm["quota"]:
            if not quota["sufficient"]:
                issue("vm", "VM_QUOTA_INSUFFICIENT")
    except (Unverified, KeyError, TypeError, AttributeError):
        issue("vm", "VM_AVAILABILITY_UNVERIFIED")

    # 2. OSイメージの世代とCPUアーキテクチャをSKUと照合する。Linuxというだけで
    # 起動や証明書bootstrapの互換性があるとはせず、検証対象Ubuntu系を確認する。
    image = management["imageReference"]
    if "id" in image:
        vm["image"] = {"status": "manual-check", "source": "resource-id"}
        issue("vm.image", "CUSTOM_IMAGE_COMPATIBILITY_REQUIRES_MANUAL_CHECK")
    else:
        try:
            urn = ":".join(image[key] for key in ("publisher", "offer", "sku", "version"))
            details = query(["az", "vm", "image", "show", "--location", location, "--urn", urn])
            if not isinstance(details, dict):
                raise Unverified()
            properties = details.get("properties", details)
            if not isinstance(properties, dict) or details.get("name") != image["version"]:
                raise Unverified()
            generation = properties.get("hyperVGeneration")
            if generation not in ("V1", "V2"):
                raise Unverified()
            features = _attributes(properties.get("features"))
            vm["image"] = {"status": "verified", "source": "marketplace", "urn": urn, "generation": generation}
            if properties.get("osDiskImage", {}).get("operatingSystem") != "Linux":
                issue("vm.image", "IMAGE_LINUX_REQUIRED")
            # OS初期設定・Entra SSH・証明書処理を検証するのはこのLTS系だけ。
            # 別のLinuxイメージまで互換性確認済みと扱わない。
            supported_image = {"publisher": "canonical", "offer": "ubuntu-24_04-lts", "sku": "server"}
            if any(str(image.get(key, "")).lower() != expected for key, expected in supported_image.items()):
                issue("vm.image", "IMAGE_UBUNTU_2404_SERVER_REQUIRED")
            if properties.get("architecture", "").lower() != "x64" or generation != "V2":
                issue("vm.image", "IMAGE_UBUNTU_2404_X64_GEN2_REQUIRED")
            if management["diskControllerType"] == "NVMe":
                if generation != "V2" or "nvme" not in _tokens(features.get("diskcontrollertypes")):
                    issue("vm.image", "IMAGE_NVME_UNSUPPORTED")
            elif "diskcontrollertypes" in features and "scsi" not in _tokens(features["diskcontrollertypes"]):
                issue("vm.image", "IMAGE_DISK_CONTROLLER_UNSUPPORTED")
            if management.get("enableAcceleratedNetworking") and not _true(features.get("isacceleratednetworksupported")):
                issue("vm.image", "IMAGE_ACCELERATED_NETWORKING_UNVERIFIED")
            if sku is not None:
                capabilities = _attributes(sku.get("capabilities"))
                if generation.lower() not in _tokens(capabilities.get("hypervgenerations")):
                    issue("vm.image", "IMAGE_VM_GENERATION_MISMATCH")
                architecture = properties.get("architecture")
                sku_architecture = capabilities.get("cpuarchitecturetype")
                if architecture is not None and sku_architecture is not None and architecture.lower() != sku_architecture.lower():
                    issue("vm.image", "IMAGE_VM_ARCHITECTURE_MISMATCH")
            state = properties.get("imageDeprecationStatus", {}).get("imageState")
            if state == "Deprecated":
                issue("vm.image", "IMAGE_DEPRECATED")
        except (Unverified, KeyError, TypeError, AttributeError):
            vm["image"] = {"status": "blocked", "source": "marketplace"}
            issue("vm.image", "IMAGE_COMPATIBILITY_UNVERIFIED")

    if not any(value["target"].startswith("vm") for value in report["issues"]):
        vm["status"] = "verified"
    elif vm.get("image", {}).get("status") == "verified":
        vm["image"]["status"] = "blocked" if any(value["target"] == "vm.image" for value in report["issues"]) else "verified"

    # 3. モデル名だけでなく、版・形式・SKU・必要API機能・利用枠プールまで揃える。
    # Azureにモデルが表示されても、要求したSKUや容量が使えるとは限らない。
    deployments = config.get("services", {}).get("modelDeployments", [])
    if deployments:
        model_location = config["foundryLocation"]
        try:
            available = _entries(query(["az", "cognitiveservices", "model", "list", "--location", model_location]))
        except Unverified:
            available = None
        try:
            usage = _entries(query(["az", "cognitiveservices", "usage", "list", "--location", model_location]))
        except Unverified:
            usage = None
        requested_by_usage = {}
        capacity_cache = {}
        for deployment in deployments:
            model = {"status": "blocked", "location": model_location, "name": deployment["modelName"],
                     "deploymentName": deployment["name"], "version": deployment["modelVersion"],
                     "sku": deployment["skuName"], "capacity": deployment["capacity"],
                     "capability": {"status": "unverified"}, "quota": {"status": "unverified"},
                     "availability": {"status": "unverified"}, "capacityRules": {"status": "unverified"}}
            report["models"].append(model)
            target = "model." + deployment["name"]
            try:
                if available is None:
                    raise Unverified()
                matches = []
                for entry in available:
                    metadata = entry.get("model") or {}
                    if metadata.get("name") != deployment["modelName"] or metadata.get("version") != deployment["modelVersion"] or metadata.get("format") != deployment["modelFormat"]:
                        continue
                    choices = entry.get("skus") or metadata.get("skus")
                    if not isinstance(choices, list):
                        continue
                    for choice in choices:
                        if choice.get("name") == deployment["skuName"]:
                            matches.append((metadata, choice))
                if not matches:
                    # 設定した版とSKUが一覧にない場合、提供容量の不足とは区別する。
                    # 別の版へ自動で切り替えず、公開カタログの候補だけを理由に残す。
                    same_model = [entry for entry in available if isinstance(entry, dict)
                                  and isinstance(entry.get("model"), dict)
                                  and entry["model"].get("name") == deployment["modelName"]
                                  and entry["model"].get("format") == deployment["modelFormat"]]
                    versions = sorted({entry["model"]["version"] for entry in same_model
                                       if isinstance(entry["model"].get("version"), str)})
                    model["catalog"] = {
                        "status": "unmatched",
                        "reason": ("model-not-listed" if not same_model else
                                   "configured-version-not-listed" if deployment["modelVersion"] not in versions else
                                   "configured-sku-not-listed"),
                        "availableVersions": versions,
                    }
                    raise Unverified()
                required = _model_requirements(config, deployment)
                capabilities_seen = []
                for metadata, _ in matches:
                    capabilities = _attributes(metadata.get("capabilities"))
                    capabilities_seen.append({capability: _true(capabilities.get(capability)) for capability in sorted(required)})
                model["capability"] = {"status": "verified" if all(all(value.values()) for value in capabilities_seen) else "unsupported",
                                       "required": sorted(required)}
                if model["capability"]["status"] != "verified":
                    issue(target, "MODEL_API_UNSUPPORTED")

                usage_names = {_identifier(choice.get("usageName")) for _, choice in matches}
                explicit_usage = deployment.get("quotaUsageName")
                selected = matches
                usage_name = None
                if explicit_usage is not None:
                    explicit_usage = _identifier(explicit_usage)
                    selected = [(metadata, choice) for metadata, choice in matches if choice.get("usageName") == explicit_usage]
                    if selected:
                        usage_name = explicit_usage
                    else:
                        model["quota"] = {"status": "unverified", "requestedUsageName": explicit_usage, "candidates": sorted(usage_names)}
                        issue(target, "MODEL_QUOTA_USAGE_NOT_FOUND")
                elif len(usage_names) == 1:
                    usage_name = next(iter(usage_names))
                else:
                    selected = []
                    model["quota"] = {"status": "ambiguous", "candidates": sorted(usage_names)}
                    issue(target, "MODEL_QUOTA_USAGE_AMBIGUOUS")

                if selected:
                    capacity_rules = [_capacity_rules(choice.get("capacity"), _number(deployment["capacity"])) for _, choice in selected]
                    # OpenAI/AIServicesで同じモデルが重複表示される場合も、容量制約は一致が必要。
                    # 省略とnullは同じ未提示の意味として比較する。
                    if any(value != capacity_rules[0] for value in capacity_rules[1:]):
                        issue(target, "MODEL_CAPACITY_METADATA_CONFLICT")
                        model["capacityRules"] = {"status": "conflicting"}
                    else:
                        model["capacityRules"] = capacity_rules[0]
                        if not capacity_rules[0]["sufficient"]:
                            issue(target, "MODEL_CAPACITY_UNSUPPORTED")
                else:
                    model["capacityRules"] = {"status": "unverified", "reason": "quota-usage-not-selected"}
                existing = existing_models.get(deployment["name"].lower())
                existing_capacity = 0.0
                if existing is not None:
                    previous_properties, previous_sku = existing.get("properties", {}), existing.get("sku", {})
                    previous_model = previous_properties.get("model", {})
                    if (previous_properties.get("provisioningState") == "Succeeded"
                            and previous_model.get("name") == deployment["modelName"]
                            and previous_model.get("version") == deployment["modelVersion"]
                            and previous_model.get("format") == deployment["modelFormat"]
                            and previous_sku.get("name") == deployment["skuName"]):
                        existing_capacity = _number(previous_sku.get("capacity"))
                additional_capacity = max(0.0, deployment["capacity"] - existing_capacity)
                model.update({"existingCapacity": existing_capacity, "additionalCapacity": additional_capacity})
                if usage_name is not None:
                    model["usageName"] = usage_name
                    requested_by_usage[usage_name] = requested_by_usage.get(usage_name, 0) + additional_capacity
                    try:
                        if usage is None:
                            raise Unverified()
                        model["quota"] = _usage(usage, usage_name, additional_capacity)
                        model["quota"]["status"] = "verified" if model["quota"]["sufficient"] else "insufficient"
                    except Unverified:
                        model["quota"] = {"status": "unverified", "name": usage_name}
                        issue(target, "MODEL_QUOTA_UNVERIFIED")

                if existing_capacity == deployment["capacity"]:
                    # 正常な既存配備とモデル・版・形式・SKU・容量が同じなら、割当を再利用します。
                    # 新規配備用の空き容量は不要です。カタログ・利用枠の確認は上で行います。
                    model["availability"] = {"status": "not-required", "reason": "existing-deployment-unchanged",
                                             "source": "existing-deployment", "requested": 0.0, "reservation": False}
                else:
                    key = tuple(deployment["model" + name] for name in ("Format", "Name", "Version"))
                    try:
                        if key not in capacity_cache:
                            query_string = urlencode({"api-version": "2025-06-01", "modelFormat": key[0], "modelName": key[1], "modelVersion": key[2]})
                            url = f"https://management.azure.com/subscriptions/{subscription}/providers/Microsoft.CognitiveServices/modelCapacities?{query_string}"
                            capacity_cache[key] = _entries(query(["az", "rest", "--method", "get", "--url", url]))
                        model["availability"] = _deployment_capacity(capacity_cache[key], deployment, model_location, additional_capacity)
                        if not model["availability"]["sufficient"]:
                            issue(target, "MODEL_DEPLOYMENT_CAPACITY_INSUFFICIENT")
                    except (Unverified, KeyError, TypeError, AttributeError):
                        model["availability"] = {"status": "unverified"}
                        issue(target, "MODEL_DEPLOYMENT_CAPACITY_UNVERIFIED")
                model["status"] = "verified" if not any(value["target"] == target for value in report["issues"]) else "blocked"
            except (Unverified, KeyError, TypeError, AttributeError):
                issue(target, "MODEL_AVAILABILITY_UNVERIFIED")
        # 同じ利用枠を共有する配備を合計して最終判定する。1件ずつ空きに収まっても、
        # 全件を同時に作ると超過するケースをここで検出する。
        for model in report["models"]:
            if "usageName" not in model:
                continue
            target = "model." + model["deploymentName"]
            try:
                if usage is None:
                    raise Unverified()
                model["quota"] = _usage(usage, model["usageName"], requested_by_usage[model["usageName"]])
                model["quota"]["status"] = "verified" if model["quota"]["sufficient"] else "insufficient"
                if not model["quota"]["sufficient"]:
                    model["status"] = "blocked"
                    issue(target, "MODEL_QUOTA_INSUFFICIENT")
            except Unverified:
                model["status"] = "blocked"
                model["quota"]["status"] = "unverified"
                issue(target, "MODEL_QUOTA_UNVERIFIED")
    else:
        report["modelStatus"] = "not-configured"
    if not report["issues"]:
        report["status"] = "verified"
    return report

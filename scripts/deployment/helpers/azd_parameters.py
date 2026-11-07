"""native azdと静的Bicep bridgeが共有するARM入力の契約。

実値は呼出元が.local/<environment>/generatedへ保護保存する。このモジュールは
ファイルや環境を書き換えず、値を表示せず、Azureにも接続しない。
"""
from __future__ import annotations

import copy
import re


ARM_PARAMETERS_ENV = "EGAKUMI_ARM_PARAMETERS_FILE"
ARM_PARAMETERS_SCHEMA = "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#"

# infra/main.bicepの省略可能な入力と同じ既定値。bridgeとの一致は合成テストで検査する。
ARM_PARAMETER_DEFAULTS = {
    "phase": "core",
    "sshPublicKey": "",
    "dnsPluginArgumentsJson": "",
    "preparedPfxBase64": "",
    "preparedPfxPassword": "",
    "credentialProviderSecrets": {},
    "l7IngressSecret": "",
    "realtimeTicketKey": "",
    "manageAuditImmutabilityPolicies": True,
    "retainedAuditImmutabilityPolicyIds": [],
    "preserveAuditCollection": False,
    "existingNetworkSecurityGroupNames": [],
    "existingRouteTableRoutes": {},
    "existingApimHostnameConfigurations": {},
    "existingPortalApp": False,
    "existingWorkspaceDataCollectionRuleResourceId": "",
    "diagnosticSettingProperties": {},
}


def arm_parameters_reference(environment_name: str) -> str:
    """azd環境へ保存する、実行ルート基準の非秘密の相対パスを返す。"""
    if (not isinstance(environment_name, str) or len(environment_name) > 12
            or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", environment_name)):
        raise ValueError("ARM parameter reference requires a valid environment name")
    return f".local/{environment_name}/generated/main.parameters.json"


def complete_arm_parameters(document: dict) -> dict:
    """正規ARMドキュメントに、秘密を含まないBicep既定値を補完する。

    schemaとcontentVersionはproducerが指定し、configurationは構成ローダーで
    検査済みのobjectを必須とする。入力を変更せず、
    未知のparameterやKey Vault reference等の別形式を黙って無視しない。
    返り値に秘密があれば、呼出元は保護した生成入力としてだけ扱う。
    """
    if not isinstance(document, dict) or set(document) - {"$schema", "contentVersion", "parameters"}:
        raise ValueError("ARM parameter document has unsupported fields")
    if document.get("$schema") != ARM_PARAMETERS_SCHEMA:
        raise ValueError("ARM parameter document requires the deployment parameters schema")
    version = document.get("contentVersion")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", version):
        raise ValueError("ARM parameter document requires a four-part contentVersion")
    parameters = document.get("parameters")
    if not isinstance(parameters, dict) or set(parameters) - {"configuration", *ARM_PARAMETER_DEFAULTS}:
        raise ValueError("ARM parameter document has unsupported parameters")
    if "configuration" not in parameters:
        raise ValueError("ARM parameter document requires configuration")
    for entry in parameters.values():
        if not isinstance(entry, dict) or set(entry) != {"value"}:
            raise ValueError("ARM parameters must use value entries")
    if not isinstance(parameters["configuration"]["value"], dict):
        raise ValueError("ARM configuration must be an object")
    result = {
        "$schema": document["$schema"],
        "contentVersion": version,
        "parameters": {name: {"value": copy.deepcopy(value)}
                       for name, value in ARM_PARAMETER_DEFAULTS.items()},
    }
    result["parameters"].update(copy.deepcopy(parameters))
    return result

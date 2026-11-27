#!/usr/bin/env python3
"""azd up/downの前後をつなぎ、構築と撤去を同じ設定から実行する。

①Entra・公開DNSの事前準備、②params設定、③azd up/downによる基盤構築、
④企業ごとのハブ＆スポーク対応資料、という作業分担の③を担当する。
azdのhookから呼ばれ、設定・秘密ファイル・ログイン先・利用枠を確認し、
監査ログの保存先を先に作ってから、通常のazdによるコア構築へ引き渡す。
構築後は実Resource IDを記録し、証明書参照などを確認する。この記録を
撤去時にも使い、別環境や保存義務のある監査ログを誤って削除しない。
validateはローカル検査、capacityはAzure読取、構築hookは
Azure変更を伴う。作成後の資源には料金が発生し得る。Graph操作や試験専用の
自動撤去タイマーは実装せず、撤去は明示的なazd downで行う。

preprovision引数（python3 scripts/deployment/lifecycle.py preprovision）の目的と実行内容:
azd upとazd provisionは同じ構築フローを使う。本体構築の前提条件を確認し、
監視・監査の保存先を先に構築してから、
.local/<environment>/generated/main.parameters.jsonを生成する。
  1. 設定を読み込み、必要なツール・環境設定・秘密情報・管理VM用SSH鍵を
     確認・準備する。準備済みの入力や鍵は再利用する。
  2. Azureのログイン先を照合し、必要なリソースプロバイダーと、構成で利用する
     EncryptionAtHost機能を登録する。
  3. APIM名の利用可否と、VM・モデルの利用枠などを確認する。
  4. 既存の監査ログ保存先・保持設定・ログ収集状態を確認し、再実行によって
     保護済みの保持設定や有効なログ収集を弱めないようにする。
  5. infra/main.bicepをmonitoringフェーズで実行し、5つのリソースグループ、
     証明書参照用マネージドID、監視・監査の保存先を先行構築する。
     完了後、実際のリソースIDなどを.local/<environment>/state/latest.jsonへ記録する。
  6. 監査ログの保持ロックを設定に応じて適用・確認し、Application Insightsの
     日次上限を設定する。API Centerの利用可否も確認し、同名の対象が
     論理削除されていれば復元する。
  7. 既存のNSG・ルート・APIM設定・PortalのCAの実在を再確認する。API公開時に
     追加されたFirewallの宛先も読み、今回の構築入力へ引き継ぐ。秘密情報を含む
     一時ファイルとして、アクセスを制限した.local/<environment>/generated/main.parameters.jsonを再生成する。
     保持確認の前にAzure提供DNSを照合し、不要な既知のDNS用NSG子規則だけを
     回収する。独自DNSや意味の異なる同名規則がある場合は変更せず停止する。
正常終了すると、azd provisionが続いてVM・VNetなどの本体構築を行う。
この処理の途中で失敗した場合は、生成済みのmain.parameters.jsonを削除して停止する。

postprovision引数（python3 scripts/deployment/lifecycle.py postprovision）の目的と実行内容:
main.bicepのcoreフェーズによる本体配備が成功した後、実際の構築結果を記録し、
必要な入口設定を仕上げる。このhookの完了までがazd provisionの処理に含まれる。
  1. azdの環境設定から、今回の本体配備名CORE_DEPLOYMENT_NAMEを取得する。
  2. その配備のoutputsをAzureから読み、リソースグループが設定した5つのRGと
     一致することを確認する。他の配備を名前から推測して利用しない。
  3. 実際のリソースIDなどを.local/<environment>/state/latest.jsonへ保存する。入口の仕上げが
     失敗しても、後の更新・撤去に必要な構築記録を残すため、先に保存する。
  4. deployment/helpers/grpc_lifecycle.pyのconfigure_endpoint()を呼び、
     所有する旧gRPC用ドメインだけを撤去し、標準名のHTTPS保守を維持する。
     APIM受信HTTP/2をfalseへ限定更新し、他設定の保全と反映完了を確認する。
     Named Valueの配置・Key Vault参照の確認は後続のGateway配備が担当する。
  5. 仕上げの確認結果を.local/<environment>/state/ingress-bootstrap.jsonへ保存する。
  6. Azure Public DNSを選んだ場合は、DNS用Subscriptionの公開ゾーンを照合し、
     GatewayとPortalのAエイリアスを今回のApp Gateway Public IPへ接続する。
     保存した参照先を読み直し、結果を.local/<environment>/state/public-dns.jsonへ記録する。
  7. 入口設定の処理はfinallyで.local/<environment>/generated/main.parameters.jsonを削除する。仕上げが
     失敗しても生成した秘密入り入力は回収し、元の保護ファイルは保持する。
正常終了すると、公開URLと基盤工程の完了を表示する。
PortalのCAはcoreフェーズで初期イメージを使って作成する。
azd upとazd provisionは基盤工程で終了する。認証用の公開証明書をEntraへ登録してから
GatewayとPortalを順に個別配備する。
入口設定の反映確認と、通常API・Realtimeなどの実際の疎通試験も別の確認として扱う。
"""
from __future__ import annotations

import os
import sys

# CLI自身と子のPythonが公開ソースの横へbytecodeを書かないようにします。
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import base64
import copy
import hashlib
from datetime import datetime, timezone
from http.client import IncompleteRead
import ipaddress
import json
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET

# azd hookからファイルを直接実行する場合も、呼出元の作業ディレクトリーに依存させない。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "deployment"

from .helpers.paths import REPOSITORY, configuration_path, local_paths, private_parent
from .helpers.aca_egress import validate_aca_route_snapshot
from .helpers.azd_parameters import ARM_PARAMETERS_ENV, arm_parameters_reference, complete_arm_parameters
from .helpers.audit_evidence import evidence_container_id, evidence_policy_id
from .helpers.configuration import ConfigurationError, build_bicep_configuration, entra_tenant_id, load_configuration, load_firewall_tls_inspection, load_secret_inputs
from .helpers.diagnostics import DiagnosticPreservationError, canonical_settings, prepare_settings
from .helpers.prepare_secrets import SecretPreparationError, prepare_secrets
from .helpers.dns_selection import DnsSelectionError, select_dns_provider
from .helpers.public_dns import configure_public_dns
from .helpers.log_privacy import LogPrivacyError, existing_workspace_link as read_workspace_privacy_link, ensure_workspace_link, resource_ids as log_privacy_resource_ids
from .helpers.ssh_keys import ensure_management_ssh_key
from .helpers.apim_preflight import verify_apim_name, ApimPreflightError
from .helpers.apim_naming import apim_instance_id
from .helpers.apim_egress import APIM_EGRESS_RULE_NAMES, validate_apim_egress_rule
from .helpers.apim_generations import assert_current_instance, verify_state as verify_apim_state
from .helpers.capacity import verify_capacity
from .helpers.powershell_runtime import locate_powershell, powershell_environment
from .helpers.grpc_lifecycle import apim_name_matches, configure_endpoint as configure_grpc_endpoint, plan_secret_registration, prepare_secret as prepare_grpc_secret, remove_secret as remove_grpc_secret
from .helpers.operator_access import OperatorAccessError, cleanup_disabled_operator
from .helpers.dns_nsg import DnsNsgError, retire_dns_rules
from .helpers.appgw_egress_nsg import AppgwEgressNsgError, retire_appgw_egress_rules
from .helpers.fixed_nsg import FixedNsgError, prepare_fixed_nsg_rules, complete_fixed_nsg_rules
from .helpers.portal_gateway import gateway_id, require_appgw_backend_tls_ready
from .helpers.management_host_time import ManagementHostTimeError, require_management_host_time_core_ready
from .helpers import serial_initialization
from .helpers.serial_credentials import (
    SerialCredentialError, prepare_initial_serial_password, bind_initial_serial_password, prepared_initial_serial_generation,
)
from .helpers.private_permissions import (
    check_private_permissions as require_serial_storage_permissions,
    protect_new_path as protect_serial_storage,
)

ROOT = REPOSITORY
PROJECT_NAME = "ai-workforce-core"
PROVIDERS = (
    "Microsoft.Network", "Microsoft.ManagedIdentity", "Microsoft.KeyVault",
    "Microsoft.CognitiveServices", "Microsoft.ApiManagement", "Microsoft.ApiCenter",
    "Microsoft.OperationalInsights", "Microsoft.Insights", "Microsoft.Storage",
    "Microsoft.Compute", "Microsoft.AlertsManagement",
    "Microsoft.App", "Microsoft.ContainerRegistry", "Microsoft.Sql",
)


class DeploymentError(RuntimeError):
    """構築の停止理由を、秘密値を含めず呼出元へ返す例外。"""
    pass


def cleanup_dns_nsg_rules(config: dict, transport) -> dict:
    """通常構築でも不要な DNS 子規則を回収し、保護記録を残す。"""
    def record(event, value):
        path = (local_paths(config, ROOT).state / "dns-nsg-retirement"
                / (str(time.time_ns()) + "-" + event + ".json"))
        protected_json(path, value)
    try:
        return retire_dns_rules(config, transport, execute=True, record=record)
    except DnsNsgError as error:
        record("failed", {"status": "stopped", "responseMayBeUnknown": True})
        raise DeploymentError(str(error)) from None


def cleanup_appgw_egress_nsg_rules(config: dict, transport) -> dict:
    """通常構築でも AppGW の不要な 3 子規則だけを保護記録付きで回収する。"""
    def record(event, value):
        path = (local_paths(config, ROOT).state / "appgw-egress-nsg-retirement"
                / (str(time.time_ns()) + "-" + event + ".json"))
        protected_json(path, value)
    try:
        return retire_appgw_egress_rules(
            config, transport, execute=True, record=record)
    except AppgwEgressNsgError as error:
        record("failed", {"status": "stopped", "responseMayBeUnknown": True})
        raise DeploymentError(str(error)) from None


def upgrade_journal(config: dict, category: str):
    """一回の操作ごとに分離し、同じイベントも上書きせず保護記録へ保存する。"""
    if not re.fullmatch(r"[a-z0-9-]+", category):
        raise ValueError("Upgrade journal category is invalid")
    directory = local_paths(config, ROOT).state / category / uuid.uuid4().hex
    sequence = 0
    def record(event, value):
        nonlocal sequence
        if not isinstance(event, str) or not re.fullmatch(r"[a-zA-Z0-9-]+", event):
            raise ValueError("Upgrade journal event is invalid")
        sequence += 1
        protected_json(directory / f"{sequence:04d}-{event}.json", value)
    return record


def require_core_appgw_ready(config, transport):
    """通常のHTTPS検査を行い、初回B2の所有済み未完了要求だけ再開できるようにする。"""
    try:
        return require_appgw_backend_tls_ready(config, transport, allow_new=True)
    except ValueError as tls_error:
        workspace = local_paths(config, ROOT)
        path = workspace.checked(workspace.state / "public-ingress-pending.json")
        try:
            require_serial_storage_permissions(path)
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise tls_error from None
        if not isinstance(state, dict):
            raise
        plan = state.get("gatewayPlan")
        monitoring = state.get("ingressMonitoringPlan")
        if (any(state.get(key) != config[key] for key in ("subscriptionId", "azureTenantId", "resourceStem"))
                or state.get("resourceGroupNames") != config["resourceGroupNames"]
                or state.get("apimInstanceId") != apim_instance_id(config)
                or not isinstance(plan, dict) or not isinstance(monitoring, dict)):
            raise
        resource = public_ingress_deployment_id(config)
        request = transport.request("GET", resource + "?api-version=2022-09-01")
        observed = request.get("body") or {}
        properties = observed.get("properties", {})
        saved = properties.get("parameters") or {}
        if (request.get("statusCode") != 200
                or str(observed.get("id", "")).casefold() != resource.casefold()
                or observed.get("tags", {}).get("azd-env-name") != config["environmentName"]
                or properties.get("provisioningState") not in ("Accepted", "Running", "Creating", "Updating", "Failed")
                or saved.get("gatewayPlan", {}).get("value") != plan
                or saved.get("ingressMonitoringPlan", {}).get("value") != monitoring):
            raise
        identity = gateway_id(config)
        current = transport.request("GET", identity + "?api-version=2024-05-01")
        gateway = current.get("body") or {}
        if (current.get("statusCode") != 200 or str(gateway.get("id", "")).casefold() != identity.casefold()
                or (gateway.get("tags") or {}).get("environment") != config["environmentName"]
                or (gateway.get("tags") or {}).get("product") != config["productName"]
                or gateway.get("properties", {}).get("provisioningState") not in ("Creating", "Updating", "Failed")):
            raise
        return {"newGateway": True, "certificateReleasePending": True}


def require_certificate_registry_ready(config, transport):
    """既存の非公開ACRの設定を変える前にB2の対応条件を照合する。"""
    from .helpers.certificate_image import REGISTRY_API, ensure_private_registry

    stem = config["resourceStem"]
    registry_name = "acr" + stem.replace("-", "")
    resource = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}"
                f"/providers/Microsoft.ContainerRegistry/registries/{registry_name}")
    response = transport.request("GET", resource + "?api-version=" + REGISTRY_API)
    observed = require_success(response, "Inspect existing private certificate registry", allowed=(200, 404))
    if response["statusCode"] == 404:
        return
    if not isinstance(observed, dict) or str(observed.get("id", "")).casefold() != resource.casefold():
        raise DeploymentError("The existing private registry response identifies another resource")
    tags = observed.get("tags")
    if (not isinstance(tags, dict) or tags.get("product") != config["productName"]
            or tags.get("environment") != config["environmentName"]):
        raise DeploymentError("The existing private registry does not match the selected environment")
    try:
        ensure_private_registry(observed)
    except ValueError as error:
        raise DeploymentError(str(error)) from None


def prepare_core_network_upgrade(config: dict, transport, *, execute=True) -> dict:
    """VMのPHC同期とApp GatewayのHTTPS構成を確認してからNSGを更新する。"""
    try:
        require_management_host_time_core_ready(config, transport)
    except ManagementHostTimeError as error:
        raise DeploymentError(str(error)) from None
    require_core_appgw_ready(config, transport)
    record = upgrade_journal(config, "fixed-nsg-prepare")
    try:
        return prepare_fixed_nsg_rules(config, transport, execute=execute, record=record)
    except FixedNsgError as error:
        raise DeploymentError(str(error)) from None


def complete_core_network_upgrade(config: dict, transport) -> dict:
    """Bicepで設定したNSGを読み戻し、照合済み旧子規則だけを回収する。"""
    record = upgrade_journal(config, "fixed-nsg-complete")
    try:
        return complete_fixed_nsg_rules(config, transport, execute=True, record=record)
    except FixedNsgError as error:
        raise DeploymentError(str(error)) from None


def same_azure_location(actual: str, expected: str) -> bool:
    """ARMの表示名（Japan East）と設定コード（japaneast）を照合する。

    APIによって同じリージョンが空白付きで返るため、ASCII空白と大小文字だけを
    正規化する。別リージョン・記号の違いは一致にせず、所有IDやタグの検査も維持する。
    """
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    return actual.replace(" ", "").casefold() == expected.replace(" ", "").casefold()


class NoRedirect(HTTPRedirectHandler):
    """ARMトークンを別の接続先へ転送しないHTTPハンドラー。"""
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # ARMトークンを別のURLへ送る必要はないため、転送を許可しない。
        """リダイレクトを拒否し、最初に検査したARM接続先だけへ要求する。"""
        return None


def _application_insights_failure(arguments: list[str], stdout: str) -> dict | None:
    """既知の日次上限helperの固定失敗形式だけを受け取り、外部値を返さない。"""
    expected = ["-NoLogo", "-NoProfile", "-File",
                str(ROOT / "scripts/deployment/helpers/set-app-insights-cap.ps1")]
    if (arguments[1:] != expected or Path(arguments[0]).name.lower() not in {"pwsh", "pwsh.exe"}
            or not isinstance(stdout, str)):
        return None
    try:
        if len(stdout.encode("utf-8")) > 4096:
            return None
        def unique_fields(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate failure field")
                result[key] = value
            return result
        failure = json.loads(stdout, object_pairs_hook=unique_fields)
    except (ValueError, UnicodeError):
        return None
    allowed = {"status", "stage", "code", "requiredDependency"}
    if (not isinstance(failure, dict) or not {"status", "stage", "code"}.issubset(failure)
            or set(failure) - allowed or failure["status"] != "failed"
            or failure["code"] != "ApplicationInsightsDailyCapFailed"
            or not isinstance(failure["stage"], str)
            or failure["stage"] not in {"dependencies", "input", "authentication", "configure", "readback"}):
        return None
    if "requiredDependency" in failure:
        dependencies = {"Az.Accounts", "Az.ApplicationInsights", "Connect-AzAccount",
                        "Disable-AzContextAutosave", "Get-AzContext", "Disconnect-AzAccount",
                        "Invoke-AzRestMethod", "Set-AzApplicationInsightsDailyCap",
                        "Connect-AzAccount.AccessToken", "Connect-AzAccount.Scope",
                        "Set-AzApplicationInsightsDailyCap.ResourceGroupName",
                        "Set-AzApplicationInsightsDailyCap.Name",
                        "Set-AzApplicationInsightsDailyCap.SubscriptionId",
                        "Set-AzApplicationInsightsDailyCap.DailyCapGB"}
        if (failure["stage"] != "dependencies" or not isinstance(failure["requiredDependency"], str)
                or failure["requiredDependency"] not in dependencies):
            return None
    return {"code": "ApplicationInsightsDailyCapFailed", "stage": failure["stage"]}


def run(arguments: list[str], *, payload: dict | None = None, environment: dict | None = None) -> dict:
    """子プロセスのJSON出力を受け取る。失敗時に資格情報を含み得る生出力を表示しない。"""
    if os.name == "nt" and arguments[0] == "az":
        compiler = None
        if arguments[1:3] == ["bicep", "build"]:
            # Azure CLIのWindows console変換を通すと日本語JSONがCP932になる版がある。
            # 同じ公式compilerを直接使い、Azure操作のCLI・Entra認証はWindows側に保つ。
            candidate = (environment or {}).get("BICEP_CLI") or os.environ.get("BICEP_CLI") or shutil.which("bicep")
            candidate = Path(candidate) if candidate else Path.home() / ".azure/bin/bicep.exe"
            if candidate.is_file():
                compiler = str(candidate)
        if compiler:
            build_arguments = list(arguments[3:])
            # standalone Bicepは--fileを持たず、同じファイルを位置引数で受け取る。
            if "--file" in build_arguments:
                build_arguments.remove("--file")
            arguments = [compiler, "build", *build_arguments]
        else:
            executable = shutil.which("az")
            if not executable:
                raise DeploymentError("Install Azure CLI on Windows before deployment")
            arguments = [executable, *arguments[1:]]
    if os.name == "nt":
        environment = {**os.environ, **(environment or {}),
                       "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(arguments, cwd=ROOT, text=True, capture_output=True, env=environment,
                            encoding="utf-8" if os.name == "nt" else None,
                            input=None if payload is None else json.dumps(payload))
    if result.returncode:
        if "-CheckDependencies" in arguments:
            try:
                dependency = json.loads(result.stdout).get("requiredDependency")
            except (ValueError, AttributeError):
                dependency = None
            if dependency in ("Az.Accounts", "Az.ApplicationInsights"):
                raise DeploymentError(f"Install the required PowerShell module: {dependency}")
        failure = _application_insights_failure(arguments, result.stdout)
        if failure is not None:
            raise DeploymentError(
                f"Application Insights daily-cap helper failed: {failure['code']} "
                f"(stage={failure['stage']}); external command output suppressed"
            )
        raise DeploymentError(f"{Path(arguments[0]).name} operation failed; review the corresponding deployment in Azure (secret output suppressed)")
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as error:
        raise DeploymentError("Command returned an unexpected non-JSON response") from error


def protected_json(path: Path, value: dict) -> None:
    """所有者だけが読める一時ファイルへ書き、置換によって途中までのJSONを残さない。"""
    path = private_parent(path)
    if os.name == "nt":
        require_serial_storage_permissions(path.parent, directory=True)
    handle, temporary = tempfile.mkstemp(prefix=".core-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            # Establish the owning context before ACL/chmod checks, so an ACL
            # failure closes the descriptor before the empty node is removed.
            if os.name != "nt":
                os.fchmod(stream.fileno(), 0o600)
            else:
                protect_serial_storage(Path(temporary))
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_arm_parameters(config: dict, document: dict) -> None:
    """秘密を保護生成物へ保存し、azdへは環境別の相対パスだけを渡す。"""
    protected_json(local_paths(config, ROOT).parameters, complete_arm_parameters(document))
    run(["azd", "env", "set", ARM_PARAMETERS_ENV, arm_parameters_reference(config["environmentName"]),
         "--environment", config["environmentName"]])


def check_parameter_bridge(config: dict) -> None:
    """Azure変更前に、秘密なしの合成入力でBicep bridgeの実読取を確かめる。"""
    override = os.environ.get("BICEP_CLI")
    executable = override or shutil.which("bicep") or str(Path.home() / ".azure/bin" / ("bicep.exe" if os.name == "nt" else "bicep"))
    if not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise DeploymentError("Install Bicep CLI and set BICEP_CLI before deployment")
    target = local_paths(config, ROOT).parameters
    expected = complete_arm_parameters(parameters(config, "core"))
    protected_json(target, expected)
    try:
        environment = {**os.environ, ARM_PARAMETERS_ENV: arm_parameters_reference(config["environmentName"])}
        result = run([executable, "build-params", str(ROOT / "infra/main.bicepparam"),
                      "--stdout", "--no-restore"], environment=environment)
        if json.loads(result.get("parametersJson", "{}")) != expected:
            raise DeploymentError("Bicep parameter bridge did not reproduce the selected inputs; use a compatible Bicep CLI")
    finally:
        target.unlink(missing_ok=True)


def arm_access_token(subscription: str, tenant: str) -> dict:
    # Azure CLIでは--subscriptionと--tenantを同時指定できない。サブスクを指定して
    # 取得した後に両方のIDを照合し、認証先を曖昧にしない。
    """指定サブスクのARMトークンを取得し、返されたテナントとサブスクを再照合する。"""
    auth = run(["az", "account", "get-access-token", "--subscription", subscription,
                "--resource", "https://management.azure.com/", "-o", "json"])
    if (str(auth.get("subscription", "")).lower() != subscription.lower()
        or str(auth.get("tenant", "")).lower() != tenant.lower()
        or not isinstance(auth.get("accessToken"), str) or not auth["accessToken"]):
        raise DeploymentError("Azure token does not match the configured tenant/subscription")
    return auth


def parameters(config: dict, phase: str, *, secrets: dict | None = None,
               manage_policies: bool = True, preserve_collection: bool = False,
               retained_policy_ids: list[str] | None = None,
               existing_nsg_names: list[str] | None = None,
               existing_route_table_routes: dict | None = None,
               existing_apim_hostname_configurations: dict | None = None,
               existing_apim_custom_properties: dict | None = None,
               existing_portal_app: bool = False,
               existing_apim_egress_fqdns: list[str] | None = None,
               existing_workspace_privacy_dcr_id: str = "",
               diagnostic_setting_properties: dict | None = None) -> dict:
    """公開設定と必要時だけ秘密入力をまとめ、Bicep用パラメーター形式へ変換する。"""
    configuration = build_bicep_configuration(config)
    # 検証済みの既存宛先は内部の生成集合だけへ引き継ぐ。元のparamsやAPI定義を
    # 書き換えず、基盤再up時も公開済みAPI・構築後の手動追加先を維持する。
    if phase == "core":
        allowed = configuration["generatedApimServiceFqdns"]
        known = {host.casefold() for host in allowed}
        for host in existing_apim_egress_fqdns or []:
            if host.casefold() not in known:
                allowed.append(host)
                known.add(host.casefold())
    canonical_diagnostics = canonical_settings(configuration, phase, preserve_collection)
    diagnostics = ({key: item["properties"] for key, item in canonical_diagnostics.items()}
                   if diagnostic_setting_properties is None else copy.deepcopy(diagnostic_setting_properties))
    if not isinstance(diagnostics, dict) or set(diagnostics) != set(canonical_diagnostics):
        raise DeploymentError("Core diagnostic generated properties are incomplete")
    secure_inputs = {key: value for key, value in (secrets or {}).items() if key != "dnsPluginArgumentsJson"}
    values = {"configuration": configuration, "phase": phase,
              "manageAuditImmutabilityPolicies": manage_policies,
              "retainedAuditImmutabilityPolicyIds": list(retained_policy_ids or []),
              "preserveAuditCollection": preserve_collection, "diagnosticSettingProperties": diagnostics, **secure_inputs,
              "existingNetworkSecurityGroupNames": list(existing_nsg_names or []),
              "existingRouteTableRoutes": copy.deepcopy(existing_route_table_routes or {}),
              "existingApimHostnameConfigurations": copy.deepcopy(existing_apim_hostname_configurations or {}),
              "existingApimCustomProperties": copy.deepcopy(existing_apim_custom_properties or {}),
              "existingPortalApp": existing_portal_app,
              "existingWorkspaceDataCollectionRuleResourceId": existing_workspace_privacy_dcr_id}
    return {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
            "contentVersion": "1.0.0.0", "parameters": {key: {"value": value} for key, value in values.items()}}


def _management_serial_vm_response(config: dict, transport: ArmTransport) -> dict:
    """管理RGと正確なVMだけを読み、404と所有確認済み200を区別する。"""
    group_id = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['maint']}"
    response = transport.request("GET", group_id + "?api-version=2024-03-01")
    group = require_success(response, "Read management VM parent ownership", allowed=(200, 404))
    if response["statusCode"] == 200:
        tags = group.get("tags") if isinstance(group, dict) else None
        if (not isinstance(group, dict) or str(group.get("id", "")).casefold() != group_id.casefold()
                or not same_azure_location(group.get("location"), config["location"])
                or not isinstance(tags, dict)
                or any(tags.get(key) != value for key, value in {
                    "product": config["productName"], "environment": config["environmentName"],
                    "azd-env-name": config["environmentName"], "layer": "maint"}.items())):
            raise DeploymentError("The management VM parent does not belong to the selected environment")
    vm_id = group_id + f"/providers/Microsoft.Compute/virtualMachines/vm-{config['resourceStem']}-mgmt"
    vm_response = transport.request("GET", vm_id + "?api-version=2024-11-01")
    body = require_success(vm_response, "Read initial management VM identity", allowed=(200, 404))
    if vm_response["statusCode"] == 200:
        if (response["statusCode"] != 200 or not isinstance(body, dict)
                or not same_azure_location(body.get("location"), config["location"])):
            raise DeploymentError("The management VM ownership or location could not be verified")
    return vm_response


def _serial_core_response(config: dict, transport: ArmTransport, provenance: dict) -> dict:
    """元の内部世代を持つ、選択したCore配備だけを再開時に読む。"""
    deployment = provenance.get("coreDeploymentId")
    if deployment is None and provenance.get("coreMode") == "native":
        environment = run(["azd", "env", "get-values", "--environment", config["environmentName"], "-o", "json"])
        name = environment.get("CORE_DEPLOYMENT_NAME")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.()-]{1,64}", name):
            raise DeploymentError("The original native Core deployment reference is missing")
        deployment = f"/subscriptions/{config['subscriptionId']}/providers/Microsoft.Resources/deployments/{name}"
    if not isinstance(deployment, str):
        raise DeploymentError("The original Core deployment reference is missing")
    response = transport.request("GET", deployment + "?api-version=2022-09-01")
    body = response.get("body")
    properties = body.get("properties", {}) if isinstance(body, dict) else {}
    tags = body.get("tags", {}) if isinstance(body, dict) else {}
    if (response.get("statusCode") == 200 and properties.get("provisioningState") in {"Failed", "Canceled"}
            and properties.get("parameters", {}).get("managementInitialSerialGeneration", {}).get("value") == provenance["credentialGeneration"]
            and isinstance(tags, dict) and all(tags.get(k) == v for k, v in {"azd-env-name": config["environmentName"], "azd-project-name": PROJECT_NAME, "azd-layer-name": ""}.items())):
        child_id = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['maint']}/providers/Microsoft.Resources/deployments/{config['resourceStem']}-management"
        child = transport.request("GET", child_id + "?api-version=2022-09-01")
        child_body = child.get("body")
        child_props = child_body.get("properties", {}) if isinstance(child_body, dict) else {}
        original = child_props.get("outputs", {}).get("initialSerialCreation", {}).get("value")
        if (child.get("statusCode") == 200 and str(child_body.get("id", "")).casefold() == child_id.casefold()
                and child_props.get("provisioningState") == "Succeeded"
                and child_props.get("parameters", {}).get("initialSerialGeneration", {}).get("value") == provenance["credentialGeneration"]
                and isinstance(original, dict) and set(original) == {"generation", "managementVm"}
                and original.get("generation") == provenance["credentialGeneration"]):
            response = {**response, "managementCreation": {**original, "coreDeploymentId": deployment}}
    return response


def _prepare_management_serial_password(config: dict, transport: ArmTransport) -> str:
    """初回404だけ生成し、既存200の回復は元Core作成証跡の確認後だけ秘密を読む。"""
    try:
        workspace = local_paths(config, ROOT)
        response = _management_serial_vm_response(config, transport)
        password = prepare_initial_serial_password(config, workspace, response)
        if response["statusCode"] == 200:
            return serial_initialization.recover_password(config, workspace, response["body"],
                lambda provenance: _serial_core_response(config, transport, provenance), protected_json)
        return password
    except SerialCredentialError as error:
        raise DeploymentError(str(error)) from None


def _management_serial_generation(config: dict, password: str) -> str:
    try:
        if password:
            return prepared_initial_serial_generation(config, local_paths(config, ROOT), password)
        pending = serial_initialization.journal(config, local_paths(config, ROOT))
        return pending["credentialGeneration"] if pending is not None and pending["status"] != "complete" else ""
    except SerialCredentialError as error:
        raise DeploymentError(str(error)) from None


def _record_management_serial_creation(config: dict, password: str, *, native: bool = False) -> None:
    try:
        serial_initialization.record_core_request(config, local_paths(config, ROOT), password,
                                                   "native" if native else "direct", protected_json)
    except SerialCredentialError as error:
        raise DeploymentError(str(error)) from None


def _confirm_management_serial_target(config: dict, transport: ArmTransport, password: str) -> None:
    """Core引渡し直前に不存在または元作成証跡で固定したUUIDを再確認する。"""
    response = _management_serial_vm_response(config, transport)
    if response["statusCode"] == 200:
        try:
            prepare_initial_serial_password(config, local_paths(config, ROOT), response)
            value = serial_initialization.journal(config, local_paths(config, ROOT))
            if password or value is not None and value["target"] is not None and value["status"] != "complete":
                vm_generation = str(response["body"].get("properties", {}).get("vmId", "")).lower()
                if value is None or value["target"] is None or value["target"]["vmId"] != vm_generation:
                    raise SerialCredentialError("The management VM changed during initial preparation; no Core deployment was started")
        except SerialCredentialError as error:
            raise DeploymentError(str(error)) from None
    elif not password:
        raise DeploymentError("The management VM changed during initial preparation; no Core deployment was started")


def _bind_management_serial_password(config: dict, outputs: dict, transport: ArmTransport, password: str) -> None:
    """元VM UUIDの永続化→公開初期化資材→exact完了証跡→資格情報bindingの順で処理する。"""
    try:
        workspace = local_paths(config, ROOT)
        pending = serial_initialization.journal(config, workspace)
        if not password and (pending is None or pending["status"] == "complete"):
            return
        response = _management_serial_vm_response(config, transport)
        if response["statusCode"] != 200:
            raise SerialCredentialError("The original management VM could not be read; retain its pending credential")
        value = serial_initialization.confirm_creation(config, workspace, response["body"],
            outputs.get("managementInitialSerialCreation"), protected_json)
        if not password:
            password = serial_initialization.saved_password(config, workspace, value)
        template = run(["az", "bicep", "build", "--file", str(ROOT / "infra/modules/management-serial-initialization.bicep"), "--stdout"])
        serial_initialization.initialize_guest(config, workspace, value, password, transport, template, protected_json)
        bind_initial_serial_password(config, workspace, response["body"], initial_password=password)
        serial_initialization.mark_complete(config, workspace, protected_json)
    except SerialCredentialError as error:
        raise DeploymentError(str(error)) from None


def _read_initial_serial_deployment_input(config: dict) -> str:
    """生成ARM入力の回収後も、保護した元Core作成証跡の照合後だけ回復する。"""
    try:
        workspace = local_paths(config, ROOT)
        value = serial_initialization.journal(config, workspace)
        if value is None or value["status"] == "complete":
            return ""
        transport = ArmTransport(config)
        response = _management_serial_vm_response(config, transport)
        if response["statusCode"] != 200:
            raise SerialCredentialError("The original management VM is not available for completion")
        return serial_initialization.recover_password(config, workspace, response["body"],
            lambda provenance: _serial_core_response(config, transport, provenance), protected_json)
    except SerialCredentialError as error:
        raise DeploymentError(str(error)) from None


def _arm_success_body(method, parsed, request_body, response_headers, content):
    """明示したAPIM rawxml読取だけを、既存検証が使うproperties形式へそろえる。"""
    content_types = [value.split(";", 1)[0].strip().lower() for name, value in response_headers.items()
                     if name.lower() == "content-type" and isinstance(value, str)]
    raw_path = re.fullmatch(
        r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.ApiManagement/service/[^/]+/"
        r"(?:(?:apis/[^/]+/(?:operations/[^/]+/)?|products/[^/]+/)?policies/policy|policyFragments/[^/]+)",
        parsed.path, re.IGNORECASE)
    raw_requested = (method == "GET" and request_body is None and raw_path is not None
                     and parse_qs(parsed.query).get("format") == ["rawxml"])
    if raw_requested and content_types == ["application/vnd.ms-azure-apim.policy.raw+xml"]:
        try:
            value = content.decode("utf-8")
            if "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
                raise ValueError("Unsafe policy XML")
            root = ET.fromstring(value)
            expected_root = "fragment" if "/policyfragments/" in parsed.path.lower() else "policies"
            if root.tag != expected_root:
                raise ValueError("Unexpected policy XML root")
        except (UnicodeError, ValueError, ET.ParseError):
            raise DeploymentError("ARM returned invalid APIM raw policy XML") from None
        return {"properties": {"format": "rawxml", "value": value}}
    # 別providerのXMLや不正なJSONを、APIM policyとして受け入れない。
    return json.loads(content) if content else None


class ArmTransport:
    """指定サブスクリプションのARMだけへ接続する。トークンはメモリー内に保持する。"""
    # 標準handlerは要求・接続・応答を呼出しごとに作り、token更新だけをlockします。
    # 独立GETの照合を並行化しても、変更要求は呼出し側で順に実行します。
    supports_concurrent_reads = True

    def __init__(self, config: dict):
        """接続先のサブスク・テナントを固定し、リダイレクトしないクライアントを用意する。"""
        self.subscription = config["subscriptionId"]
        self.tenant = config["azureTenantId"]
        self.token = None
        self.expires = 0
        self._token_lock = threading.Lock()
        self.opener = build_opener(NoRedirect())

    def request(self, method: str, path: str, body=None, headers=None) -> dict:
        """対象ホストとサブスクを検査し、必要時だけトークンを更新してARM要求を送る。

        HTTPエラーは状態と構造化した応答として返す。本文のないGETだけは、通信断と
        一時的なHTTPエラーを合わせて最大3回試す。変更要求の応答が消えた場合は、
        資源状態を確認できるよう停止する。
        """
        uri = path if path.startswith("https://") else "https://management.azure.com" + path
        parsed = urlparse(uri)
        if parsed.scheme != "https" or parsed.hostname != "management.azure.com" or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443) or parsed.fragment:
            raise DeploymentError("An ARM operation targeted an unsupported host")
        if not parsed.path.lower().startswith(f"/subscriptions/{self.subscription}/".lower()):
            raise DeploymentError("An ARM operation targeted a different subscription")
        if time.time() + 120 >= self.expires:
            # 並行する独立GETでもtoken取得は1回。HTTP要求そのものはこのlockで囲みません。
            with self._token_lock:
                if time.time() + 120 >= self.expires:
                    auth = arm_access_token(self.subscription, self.tenant)
                    self.token = auth["accessToken"]
                    self.expires = float(auth.get("expires_on", time.time() + 600))
        request_headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json", **(headers or {})}
        request = Request(uri, method=method, headers=request_headers,
                          data=None if body is None else json.dumps(body).encode())
        # 配備開始後の状態確認もGETなので、通信断から復帰して同じ配備を確認できます。
        # PUT/POST/PATCH/DELETEは受付済みの可能性があるため、この層では再送しません。
        attempts = 3 if method == "GET" and body is None else 1
        for attempt in range(attempts):
            try:
                with self.opener.open(request, timeout=120) as response:
                    content = response.read()
                    response_headers = dict(response.headers)
                    return {"statusCode": response.status, "headers": response_headers,
                            "body": _arm_success_body(method, parsed, body, response_headers, content)}
            except HTTPError as error:
                response_headers = dict(error.headers or {})
                if error.code in (408, 429, 500, 502, 503, 504) and attempt + 1 < attempts:
                    delay = attempt + 1
                    retry_after = [value for name, value in response_headers.items()
                                   if name.lower() == "retry-after"]
                    if retry_after:
                        hint = retry_after[0]
                        digits = hint.strip(" \t") if isinstance(hint, str) else ""
                        if not re.fullmatch(r"[0-9]+", digits):
                            delay = None
                        else:
                            digits = digits.lstrip("0")
                            seconds = int(digits or "0") if len(digits) <= 2 else 11
                            # 長い待機指示を短縮しない。未対応の指示も元応答を返す。
                            delay = max(delay, seconds) if seconds <= 10 else None
                    if delay is not None:
                        # 読取だけの共通attempt予算を使い、指定された秒数以上待つ。
                        # 再試行するエラー本文は読まない。
                        error.close()
                        print(f"Azure管理APIの読み取りを再試行します（{attempt + 2}/{attempts}、"
                              f"HTTP {error.code}、{delay}秒後）。", file=sys.stderr, flush=True)
                        time.sleep(delay)
                        continue
                # 応答メッセージには入力が含まれ得るため、生のエラー本文を表示しない。
                try:
                    content = json.loads(error.read())
                except (ValueError, OSError):
                    content = None
                finally:
                    error.close()
                return {"statusCode": error.code, "headers": response_headers, "body": content}
            except (URLError, TimeoutError, ConnectionError, IncompleteRead, ssl.SSLError) as error:
                reason = error.reason if isinstance(error, URLError) else error
                certificate_failure = isinstance(reason, ssl.SSLCertVerificationError)
                cause = ("certificate-verification" if certificate_failure else
                         "timeout" if isinstance(reason, TimeoutError) else
                         "tls" if isinstance(reason, ssl.SSLError) else
                         "connection" if isinstance(reason, ConnectionError) else
                         "incomplete-response" if isinstance(reason, IncompleteRead) else "transport")
                if not certificate_failure and attempt + 1 < attempts:
                    print(f"Azure管理APIの読み取りを再試行します（{attempt + 2}/{attempts}、{cause}）。",
                          file=sys.stderr, flush=True)
                    time.sleep(attempt + 1)
                    continue
                # URLクエリー・HTTPヘッダー・要求本文・外部例外本文には秘密があり得ます。
                # 表示するのはARM資源のパスと固定の原因区分だけです。
                action = ("read request could not be completed" if method == "GET" else
                          "resource state must be checked before retry")
                raise DeploymentError(f"ARM {method} {parsed.path} connection failed ({cause}); {action}") from error


def require_success(response: dict, operation: str, allowed=(200, 201, 204)) -> dict:
    """許可したHTTP状態か確認し、エラー本文を表示せず構造化したbodyを返す。"""
    if response["statusCode"] not in allowed:
        raise DeploymentError(f"{operation} failed (HTTP {response['statusCode']})")
    return response.get("body") or {}


def discover_existing_network_security_groups(config: dict, transport: ArmTransport) -> list[str]:
    """今回の所有NSGだけを読み、同じ環境の既存資源を名前で返す。

    再upでNSG本体を空のpropertiesで更新すると、AppGWが必要とする既存規則を
    Azureが認識できず拒否する場合がある。本体の新規作成が必要かを先に確認し、
    規則自体の更新は従来の子リソース配備へ任せる。手動で指定する公開設定にはしない。
    同名でも所有タグや配置先が違う資源は再利用せず、確認できない応答でも停止する。
    """
    prefix = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
              f"{config['resourceGroupNames']['nw']}/providers/Microsoft.Network/networkSecurityGroups/")
    expected_tags = {"product": config["productName"], "environment": config["environmentName"],
                     "azd-env-name": config["environmentName"]}
    existing = []
    suffixes = ("agw", "apim", "pep", "vm", "aca", "certificate", "broker")
    if config.get("auditEvidence", {}).get("enabled") is True:
        suffixes += ("audit",)
    for suffix in suffixes:
        name = f"nsg-{config['resourceStem']}-{suffix}"
        resource_id = prefix + name
        response = transport.request("GET", resource_id + "?api-version=2025-01-01")
        body = require_success(response, "Read existing core network security group", allowed=(200, 404))
        if response["statusCode"] == 404:
            continue
        tags = body.get("tags") if isinstance(body, dict) else None
        if (not isinstance(body, dict)
                or str(body.get("id", "")).casefold() != resource_id.casefold()
                or str(body.get("name", "")).casefold() != name.casefold()
                or str(body.get("type", "")).casefold() != "microsoft.network/networksecuritygroups"
                or not same_azure_location(body.get("location"), config["location"])
                or not isinstance(tags, dict)
                or any(tags.get(key) != value for key, value in expected_tags.items())):
            raise DeploymentError("Existing network security group does not match this environment's identity, location or ownership tags")
        existing.append(name)
    return existing


def discover_existing_route_table_routes(config: dict, transport: ArmTransport) -> dict:
    """今回の基本経路表と有効なACA経路表を読み、親の再配備へ引き継ぐ既存経路だけを返す。

    親の PUT で routes を省略すると既存 UDR が消えるため、動的経路の子が
    新しい値を上書きするまで現在の経路を保持する。ルート伝搬の設定はコピーせず、
    Bicep が今回の config を適用する。利用者が指定する公開パラメーターではない。
    経路の宛先・次ホップ・所属先を確認し、書込対象の設定だけを引き継ぐ。
    """
    prefix = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
              f"{config['resourceGroupNames']['nw']}/providers/Microsoft.Network/routeTables/")
    expected_tags = {"product": config["productName"], "environment": config["environmentName"],
                     "azd-env-name": config["environmentName"]}
    route_properties = {"addressPrefix", "nextHopType", "nextHopIpAddress", "hasBgpOverride"}
    existing = {}
    suffixes = ("agw", "apim", "vm", "aca", "certificate", "broker")
    if config.get("auditEvidence", {}).get("enabled") is True:
        suffixes += ("audit",)
    for suffix in suffixes:
        name = f"rt-{config['resourceStem']}-{suffix}"
        resource_id = prefix + name
        response = transport.request("GET", resource_id + "?api-version=2025-01-01")
        body = require_success(response, "Read existing core route table", allowed=(200, 404))
        if response["statusCode"] == 404:
            existing[name] = []
            continue
        tags = body.get("tags") if isinstance(body, dict) else None
        if (not isinstance(body, dict)
                or str(body.get("id", "")).casefold() != resource_id.casefold()
                or str(body.get("name", "")).casefold() != name.casefold()
                or str(body.get("type", "")).casefold() != "microsoft.network/routetables"
                or not same_azure_location(body.get("location"), config["location"])
                or not isinstance(tags, dict)
                or any(tags.get(key) != value for key, value in expected_tags.items())):
            raise DeploymentError("Existing route table does not match this environment's identity, location or ownership tags")
        properties = body.get("properties")
        if (not isinstance(properties, dict)
                or not isinstance(properties.get("routes"), list)
                or properties.get("provisioningState", "Succeeded") != "Succeeded"):
            raise DeploymentError("Existing route table properties or current routes could not be verified")
        sanitized = []
        seen = set()
        for route in properties["routes"]:
            if (not isinstance(route, dict)
                    or not isinstance(route.get("name"), str) or not route["name"]
                    or route["name"] != route["name"].strip() or "/" in route["name"]
                    or route["name"].casefold() in seen):
                raise DeploymentError("Existing route child identity or properties could not be verified")
            route_name = route["name"]
            if (("id" in route and str(route["id"]).casefold() != (resource_id + "/routes/" + route_name).casefold())
                    or ("type" in route and str(route["type"]).casefold() != "microsoft.network/routetables/routes")):
                raise DeploymentError("Existing route child does not belong to the expected route table")
            fields = route.get("properties")
            if (not isinstance(fields, dict)
                    or any(not isinstance(fields.get(key), str) or not fields[key] for key in ("addressPrefix", "nextHopType"))
                    or (fields.get("nextHopIpAddress") is not None and not isinstance(fields["nextHopIpAddress"], str))
                    or (fields.get("hasBgpOverride") is not None and not isinstance(fields["hasBgpOverride"], bool))
                    or fields.get("provisioningState", "Succeeded") != "Succeeded"):
                raise DeploymentError("Existing route semantics could not be verified; no route values were logged")
            seen.add(route_name.casefold())
            # id/type/etag/進行状態は読取用。既存の全書込可能値だけを複写する。
            sanitized.append({"name": route_name, "properties": {
                key: copy.deepcopy(value) for key, value in fields.items() if key in route_properties}})
        if suffix in ("aca", "certificate", "broker", "audit"):
            if properties.get("disableBgpRoutePropagation") is not True:
                raise DeploymentError("ACA public HTTPS requires BGP propagation to be disabled")
            if suffix == "certificate" and any(route["name"] != "default-egress" for route in sanitized):
                raise DeploymentError("Certificate ACA uses only the owned default Firewall route")
            try:
                subnets = config["network"]["subnetPrefixes"]
                validate_aca_route_snapshot(sanitized, subnets["firewall"], subnets["agw"])
            except ValueError as error:
                raise DeploymentError(str(error)) from None
        existing[name] = sanitized
    return existing


def discover_existing_apim_egress_fqdns(config: dict, transport: ArmTransport) -> list[str]:
    """公開済みAPIのHTTPS宛先を読み、次の基盤配備へ引き継ぐ。

    API公開処理は同じFirewall規則へ宛先を追加する。親の規則群を再配備すると
    配列全体が置き換わるため、既存の許可先を生成入力にだけ含める。
    所有者・送信元・HTTPS境界が一致した規則だけを採用し、取得失敗は停止する。
    """
    name = "afwp-" + config["resourceStem"]
    policy_id = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
                 f"{config['resourceGroupNames']['nw']}/providers/Microsoft.Network/firewallPolicies/{name}")
    response = transport.request("GET", policy_id + "?api-version=2025-01-01")
    policy = require_success(response, "Read existing API egress policy", allowed=(200, 404))
    if response["statusCode"] == 404:
        return []
    expected_tags = {"product": config["productName"], "environment": config["environmentName"],
                     "azd-env-name": config["environmentName"]}
    if (not isinstance(policy, dict)
            or str(policy.get("id", "")).casefold() != policy_id.casefold()
            or str(policy.get("name", "")).casefold() != name.casefold()
            or str(policy.get("type", "")).casefold() != "microsoft.network/firewallpolicies"
            or not same_azure_location(policy.get("location"), config["location"])
            or not isinstance(policy.get("tags"), dict)
            or any(policy["tags"].get(key) != value for key, value in expected_tags.items())):
        raise DeploymentError("Existing API egress policy does not match this environment's identity, location or ownership tags")
    group_id = policy_id + "/ruleCollectionGroups/rcg-egress"
    response = transport.request("GET", group_id + "?api-version=2025-01-01")
    group = require_success(response, "Read existing API egress rules", allowed=(200, 404))
    if response["statusCode"] == 404:
        return []
    properties = group.get("properties") if isinstance(group, dict) else None
    if (not isinstance(properties, dict)
            or str(group.get("id", "")).casefold() != group_id.casefold()
            or properties.get("provisioningState", "Succeeded") != "Succeeded"
            or not isinstance(properties.get("ruleCollections"), list)
            or any(not isinstance(item, dict) for item in properties["ruleCollections"])):
        raise DeploymentError("Existing API egress rule inventory could not be verified")
    collections = [item for item in properties["ruleCollections"] if item.get("name") == "allow-apim-egress"]
    if not collections:
        return []
    if (len(collections) != 1
            or collections[0].get("ruleCollectionType") != "FirewallPolicyFilterRuleCollection"
            or collections[0].get("action") != {"type": "Allow"}
            or not isinstance(collections[0].get("rules"), list)
            or any(not isinstance(item, dict) for item in collections[0]["rules"])):
        raise DeploymentError("Existing API egress collection has a different permission boundary")
    rules = [item for item in collections[0]["rules"] if item.get("name") in APIM_EGRESS_RULE_NAMES]
    if not rules:
        return []
    if len(rules) != 1:
        raise DeploymentError("Existing API egress rule is duplicated across current or legacy names")
    rule = rules[0]
    try:
        validate_apim_egress_rule(rule, config["network"]["subnetPrefixes"]["apim"])
    except ValueError as exc:
        raise DeploymentError(str(exc)) from None
    hosts = []
    for host in rule["targetFqdns"]:
        if host.casefold() not in {value.casefold() for value in hosts}:
            hosts.append(host)
    return hosts


def discover_existing_portal_app(config: dict, transport: ArmTransport) -> bool:
    """対象 CA の実在と配置先を照合し、初回作成が必要かを基盤配備へ伝える。

    初回は Bicep が初期イメージで作成し、Actions が Portal 本体へ更新する。
    再 up で本体のイメージ・Ingress・環境変数を初期設定へ戻さないよう、
    同じ CAE 内の既存 CA は基盤からの PUT を省く。設定や秘密値は複写しない。
    実在確認の 404 だけを未作成とし、権限エラーや状態不明の応答では停止する。
    """
    prefix = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
              f"{config['resourceGroupNames']['svc']}/providers/Microsoft.App/")
    application_name = f"ca-{config['resourceStem']}-portal"
    resource_id = prefix + "containerApps/" + application_name
    expected_environment = prefix + f"managedEnvironments/cae-{config['resourceStem']}"
    response = transport.request("GET", resource_id + "?api-version=2025-01-01")
    body = require_success(response, "Read existing Portal Container App", allowed=(200, 404))
    if response["statusCode"] == 404:
        return False
    if (not isinstance(body, dict)
            or str(body.get("id", "")).casefold() != resource_id.casefold()
            or str(body.get("name", "")).casefold() != application_name.casefold()
            or str(body.get("type", "")).casefold() != "microsoft.app/containerapps"
            or not same_azure_location(body.get("location"), config["location"])):
        raise DeploymentError("Existing Portal Container App does not match the configured identity or location")
    properties = body.get("properties")
    if (not isinstance(properties, dict)
            or str(properties.get("managedEnvironmentId", "")).casefold() != expected_environment.casefold()):
        raise DeploymentError("Existing Portal Container App belongs to a different managed environment")
    if properties.get("provisioningState") != "Succeeded":
        raise DeploymentError("Existing Portal Container App is not ready; check its deployment before retrying")
    return True


def discover_existing_apim_hostnames(config: dict, transport: ArmTransport, *, custom_properties_out: dict | None = None) -> dict:
    """同じ環境の APIM を読み、再 PUT で消してはいけない公開ドメイン設定を引き継ぐ。

    証明書の初回発行は APIM 作成後なので、ドメインは postprovision が設定する。
    次の up では本体 PUT でもその設定を明示する必要がある。Bicep が決める名前末尾を
    Python で再計算せず、所有 RG の一覧から名前・配置・タグを照合して本体を読み直す。
    戻り値は実 APIM 名ごとの公開情報だけで、秘密証明書や観測した証明書情報・状態は渡さない。
    """
    prefix = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
              f"{config['resourceGroupNames']['svc']}/providers/Microsoft.ApiManagement/service")
    listed = transport.request("GET", prefix + "?api-version=2024-05-01")
    inventory = require_success(listed, "Read existing core API Management inventory", allowed=(200, 404))
    if listed["statusCode"] == 404:
        return {}
    if not isinstance(inventory, dict) or not isinstance(inventory.get("value"), list) or inventory.get("nextLink"):
        raise DeploymentError("Existing API Management inventory could not be verified")
    candidates = []
    for item in inventory["value"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise DeploymentError("Existing API Management inventory could not be verified")
        if apim_name_matches(config, item["name"]):
            candidates.append(item)
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise DeploymentError("Existing API Management identity is ambiguous; no domain values were logged")
    name = candidates[0]["name"]
    resource_id = prefix + "/" + name
    response = transport.request("GET", resource_id + "?api-version=2024-05-01")
    body = require_success(response, "Read existing API Management hostname configuration", allowed=(200,))
    expected_tags = {"product": config["productName"], "environment": config["environmentName"],
                     "azd-env-name": config["environmentName"]}
    tags = body.get("tags") if isinstance(body, dict) else None
    if (not isinstance(body, dict) or str(body.get("id", "")).casefold() != resource_id.casefold()
            or str(body.get("name", "")).casefold() != name.casefold()
            or str(body.get("type", "")).casefold() != "microsoft.apimanagement/service"
            or not same_azure_location(body.get("location"), config["location"])
            or not isinstance(tags, dict) or any(tags.get(key) != value for key, value in expected_tags.items())):
        raise DeploymentError("Existing API Management does not match this environment's identity, location or ownership tags")
    properties = body.get("properties")
    if (not isinstance(properties, dict) or properties.get("provisioningState") != "Succeeded"
            or not isinstance(properties.get("hostnameConfigurations"), list)):
        raise DeploymentError("Existing API Management hostname configuration is not ready for a new deployment")
    custom = properties.get("customProperties", {})
    if (not isinstance(custom, dict) or any(
            not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9.]{0,255}", key)
            or not isinstance(value, str) or value.lower() not in ("true", "false")
            for key, value in custom.items())):
        raise DeploymentError("Existing API Management protocol settings cannot be safely preserved")
    sanitized, seen = [], set()
    writable = {"type", "hostName", "keyVaultId", "identityClientId", "defaultSslBinding",
                "negotiateClientCertificate", "certificateSource"}
    for domain in properties["hostnameConfigurations"]:
        # ARM GETは証明書本文/パスワードの欄をnullで返すため、nullだけ許容して捨てます。
        # 非nullの値が返った場合は停止し、公開設定の保持処理へ秘密値をコピーしません。
        private_response_fields = {"encodedCertificate", "certificatePassword"}
        if (not isinstance(domain, dict)
                or any(domain.get(key) is not None for key in private_response_fields)
                or domain.get("type") not in ("Proxy", "Portal", "Management", "Scm", "DeveloperPortal", "ConfigurationApi")
                or not isinstance(domain.get("hostName"), str) or not domain["hostName"]
                or len(domain["hostName"]) > 253
                or not re.fullmatch(r"(?:\*\.)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", domain["hostName"])
                or any(key in domain and not isinstance(domain[key], bool) for key in ("defaultSslBinding", "negotiateClientCertificate"))
                or (domain.get("identityClientId") is not None and not re.fullmatch(r"[0-9a-fA-F-]{36}", str(domain["identityClientId"])))):
            raise DeploymentError("Existing API Management domain cannot be preserved without unsupported or private input")
        identity = (domain["type"], domain["hostName"].casefold())
        if identity in seen:
            raise DeploymentError("Existing API Management domain identity is ambiguous")
        seen.add(identity)
        source, vault_uri = domain.get("certificateSource"), domain.get("keyVaultId")
        if vault_uri:
            # 参照 URL だけを保持し、Key Vault の証明書・Secret 本文を読むことはない。
            if not isinstance(vault_uri, str) or any(ord(char) <= 32 for char in vault_uri):
                raise DeploymentError("Existing API Management certificate reference could not be verified")
            uri = urlparse(vault_uri)
            if (source not in (None, "KeyVault") or uri.scheme != "https"
                    or not re.fullmatch(r"[a-z0-9-]+\.vault\.azure\.net(?::443)?", uri.netloc, re.IGNORECASE)
                    or uri.query or uri.fragment or uri.params
                    or not re.fullmatch(r"/secrets/[A-Za-z0-9-]+(?:/[A-Za-z0-9-]+)?/?", uri.path)):
                raise DeploymentError("Existing API Management certificate reference could not be verified")
        elif (source not in (None, "BuiltIn")
                or domain["hostName"].casefold() != (name + ".azure-api.net").casefold()
                or domain["type"] != "Proxy"):
            # Managed/inline Custom 証明書の再 PUT を秘密情報なしで保証できないため停止する。
            raise DeploymentError("Existing API Management certificate requires explicit review before redeployment")
        sanitized.append({key: copy.deepcopy(value) for key, value in domain.items()
                          if key in writable and (value is not None or key == "identityClientId")})
    if custom_properties_out is not None:
        custom_properties_out[name.casefold()] = copy.deepcopy(custom)
    return {name.casefold(): sanitized}


def local_preflight(config: dict, *, check_power_shell: bool = True, configuration_source: Path | None = None) -> None:
    """選択環境・依存関係を検査し、必要な秘密とSSH鍵をAzure操作前にローカル準備する。"""
    # Bicep consumes the reviewed static rendering. Stop before any Azure write
    # if input types/API routes changed without regenerating that source asset.
    from waf_contracts import render as check_waf_contract_manifest
    try:
        check_waf_contract_manifest(check=True)
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise DeploymentError("Regenerate and review the WAF input-contract manifest before deployment") from error
    load_firewall_tls_inspection(config)
    # 選択先の食い違いはプロセス起動より先に検出する。誤った環境での
    # Azure要求だけでなく、不要なpackage buildも開始させない。
    environment_name = os.environ.get("AZURE_ENV_NAME")
    if environment_name and environment_name != config["environmentName"]:
        raise DeploymentError("The selected azd environment name differs from configuration.environmentName")
    for variable, key in (("AZURE_SUBSCRIPTION_ID", "subscriptionId"), ("AZURE_TENANT_ID", "azureTenantId")):
        if os.environ.get(variable) and os.environ[variable].lower() != config[key].lower():
            raise DeploymentError(f"{variable} differs from the selected configuration")
    load_firewall_tls_inspection(config)
    for command in ("az", "azd"):
        if shutil.which(command) is None:
            raise DeploymentError(f"Install {command} before deployment")
    if check_power_shell:
        try:
            executable = locate_powershell()
        except RuntimeError as error:
            raise DeploymentError(str(error)) from error
        run([executable, "-NoLogo", "-NoProfile", "-File", str(ROOT / "scripts/deployment/helpers/set-app-insights-cap.ps1"), "-CheckDependencies"], environment=powershell_environment(executable))

    # パラメーター読取の互換性を、RP登録や監視資源作成より前に実測する。
    check_parameter_bridge(config)

    # 初回に必要な秘密は、対象と取得先を短く案内してから端末で非表示入力します。
    # ローカル準備に失敗・中断した場合はSSH鍵もAzure資源も作りません。
    # 既に準備済みなら秘密を表示せず再利用し、azdの次のhookで再入力させません。
    try:
        if configuration_source is not None:
            # Azure Public DNSの前提を案内します。事業者やIDは構築前のparamsで
            # 確定済みであり、azd upでは選択・入力・params更新を行いません。
            select_dns_provider(configuration_source, config)
        prepare_secrets(config, local_paths(config, ROOT).root)
    except (KeyboardInterrupt, EOFError):
        raise DeploymentError("Secret input was cancelled; no Azure resources were changed.") from None
    except (SecretPreparationError, ConfigurationError, DnsSelectionError) as error:
        # これらは入力値を埋め込まない専用エラーです。不足している設定名や
        # 対話端末が必要という、安全で対処可能な案内だけを呼出元へ渡します。
        raise DeploymentError(str(error)) from None
    except (ValueError, OSError, UnicodeError):
        # 入力値を含み得る例外の本文は、構築ログへ持ち出さないよう抑制します。
        raise DeploymentError("Secret preparation did not complete. Review the preparation instructions above; no Azure resources were changed.") from None

    # Azureへの要求より前に行う構築の準備です。
    # validate/renderは鍵を作らず、既存の公開鍵は従来どおりそのまま使います。
    # 秘密鍵はこの端末のsecrets内だけに保ち、後段は公開鍵だけを読み込みます。
    try:
        prepared = ensure_management_ssh_key(config, local_paths(config, ROOT).root)
    except ValueError as error:
        raise DeploymentError(str(error)) from None
    if prepared["created"]:
        print("Management SSH key pair created locally. Retain the private key in secrets; only its public key is sent to Azure.")


def azure_preflight(config: dict) -> None:
    """Azure CLIのログイン先が設定のテナント・サブスクと一致することを読む。"""
    account = run(["az", "account", "show", "--subscription", config["subscriptionId"], "-o", "json"])
    if account["id"].lower() != config["subscriptionId"].lower() or account["tenantId"].lower() != config["azureTenantId"].lower():
        raise DeploymentError("Azure login does not match the configured tenant/subscription")


def capacity_preflight(config: dict, transport: ArmTransport, previous_outputs: dict) -> dict:
    """有料資源を作る前に、同一環境の現在割当と追加必要量を調べて保存する。

    既存割当を差し引くのは、今回の正確なVMと配備記録で特定したFoundryだけ。
    記録のないモデルを推測で自分の割当として扱わず、不明なら利用可と判断しない。
    """
    scope = f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
    vm_id = scope + config['resourceGroupNames']['maint'] + f"/providers/Microsoft.Compute/virtualMachines/vm-{config['resourceStem']}-mgmt"
    vm_response = transport.request("GET", vm_id + "?api-version=2024-07-01")
    vm_body = require_success(vm_response, "Read current management VM allocation", allowed=(200, 404))
    vm = vm_body if vm_response["statusCode"] == 200 else None
    existing = {"vm": vm, "modelDeployments": []}
    cleanup = previous_outputs.get("cleanup", {}).get("value", {})
    foundry_id = cleanup.get("serviceResourceIds", {}).get("foundry")
    if foundry_id and config["services"]["modelDeployments"]:
        expected_foundry_id = scope + config['resourceGroupNames']['svc'] + f"/providers/Microsoft.CognitiveServices/accounts/aif-{config['resourceStem']}"
        # 同じ接頭辞を持つ他の資源を、今回のクォータ割当として数えない。
        if not isinstance(foundry_id, str) or foundry_id.casefold() != expected_foundry_id.casefold():
            raise DeploymentError("Prior Foundry output does not match this environment; quota credit was not attempted")
        expected_path = urlparse(foundry_id + "/deployments").path.lower()
        next_page = foundry_id + "/deployments?api-version=2025-06-01"
        visited = set()
        while next_page:
            if next_page in visited or urlparse(next_page).path.lower() != expected_path:
                raise DeploymentError("The existing model allocation list could not be verified")
            visited.add(next_page)
            response = transport.request("GET", next_page)
            page = require_success(response, "Read existing model allocations", allowed=(200, 404))
            if response["statusCode"] == 404:
                if len(visited) > 1:
                    raise DeploymentError("The existing model allocation list disappeared before all pages were verified")
                break
            entries = page.get("value")
            if not isinstance(entries, list):
                raise DeploymentError("The existing model allocation list was malformed")
            existing["modelDeployments"].extend(entries)
            next_page = page.get("nextLink")
            if next_page is not None and not isinstance(next_page, str):
                raise DeploymentError("The existing model allocation continuation was malformed")
    report = verify_capacity(config, run, existing_resources=existing)
    protected_json((local_paths(config, ROOT).state / "capacity.json"), report)
    if report["status"] != "verified":
        codes = sorted({issue["code"] for issue in report["issues"]})
        raise DeploymentError("VM/model capacity is not ready: " + ", ".join(codes) + "; see .local/<environment>/state/capacity.json")
    return report


def _registration_run(arguments: list[str], *, namespace: str, stage: str) -> dict:
    """登録準備の失敗へ固定の操作文脈だけを加え、外部出力は持ち出さない。"""
    try:
        return run(arguments)
    except (DeploymentError, OSError):
        # この段階では課金資源の配備を開始していない。任意のstderrや応答から
        # 原因を推測せず、確認すべきサインイン・登録権限・状態を案内する。
        raise DeploymentError(
            f"Registration preparation failed: {namespace} ({stage}). "
            "Check Azure CLI sign-in, subscription registration permissions, and provider/feature registration state. "
            "Core deployment was not started by this operation (external command output suppressed)."
        ) from None


def ensure_host_encryption(config: dict) -> None:
    """ホスト暗号化を使う場合だけ機能登録を待ち、Computeへ登録結果を反映する。

    RP登録と個別機能の登録は別です。機能登録済みなら再登録はせず、
    他の実行が登録中なら重ねて登録せず待ちます。失敗時はVM配備へ進みません。
    """
    if not config.get("management", {}).get("encryptionAtHost", False):
        return
    subscription = config["subscriptionId"]
    arguments = ["--namespace", "Microsoft.Compute", "--name", "EncryptionAtHost",
                 "--subscription", subscription, "-o", "json"]

    def feature_state(stage: str) -> str:
        response = _registration_run(["az", "feature", "show", *arguments],
                                     namespace="Microsoft.Compute/EncryptionAtHost", stage=stage)
        properties = response.get("properties") if isinstance(response, dict) else None
        state = properties.get("state") if isinstance(properties, dict) else None
        if not isinstance(state, str) or state not in {"Registered", "Registering", "NotRegistered", "Unregistered", "Unregistering"}:
            raise DeploymentError("EncryptionAtHost registration state is not supported or could not be verified; no core resources were deployed")
        return state

    deadline = time.monotonic() + 1800
    state = feature_state("feature-show")
    if state != "Registered":
        print("Waiting for Microsoft.Compute/EncryptionAtHost registration before creating core resources.")
    requested = False
    while state != "Registered":
        if time.monotonic() >= deadline:
            raise DeploymentError("EncryptionAtHost registration is still pending; retry after it completes; no core resources were deployed")
        if state in {"NotRegistered", "Unregistered"} and not requested:
            _registration_run(["az", "feature", "register", *arguments],
                              namespace="Microsoft.Compute/EncryptionAtHost", stage="feature-register")
            requested = True
        time.sleep(10)
        state = feature_state("feature-poll")

    # Refresh even if the feature was already registered: a previous execution
    # may have stopped between feature registration and provider propagation.
    # Do not use CLI --wait: bound both feature and RP polling by the same deadline.
    provider_arguments = ["--namespace", "Microsoft.Compute", "--subscription", subscription, "-o", "json"]
    if time.monotonic() >= deadline:
        raise DeploymentError("EncryptionAtHost provider refresh timed out; no core resources were deployed")
    _registration_run(["az", "provider", "register", *provider_arguments],
                      namespace="Microsoft.Compute", stage="provider-refresh-register")
    while True:
        response = _registration_run(["az", "provider", "show", *provider_arguments],
                                     namespace="Microsoft.Compute", stage="provider-refresh-poll")
        state = response.get("registrationState") if isinstance(response, dict) else None
        if state == "Registered":
            return
        if not isinstance(state, str) or state not in {"NotRegistered", "Registering", "Unregistering"}:
            raise DeploymentError("EncryptionAtHost provider refresh could not be verified; no core resources were deployed")
        if time.monotonic() >= deadline:
            raise DeploymentError("EncryptionAtHost provider refresh is still pending; no core resources were deployed")
        time.sleep(10)


def register_providers(config: dict) -> None:
    """必要なResource Providerとホスト暗号化機能を登録し、登録完了を待つ。

    企業の別サブスクでも前提を取りこぼさないよう、手作業だけに依存しない。
    待機期限はこの操作の上限であり、環境の自動撤去期限ではない。
    """
    required = (*PROVIDERS, 'Microsoft.EventHub') if config.get('auditEvidence', {}).get('enabled') else PROVIDERS
    pending = []
    for namespace in required:
        response = _registration_run(["az", "provider", "show", "--namespace", namespace,
                        "--subscription", config["subscriptionId"], "-o", "json"],
                        namespace=namespace, stage="provider-show")
        if response.get("registrationState") != "Registered":
            _registration_run(["az", "provider", "register", "--namespace", namespace,
                 "--subscription", config["subscriptionId"], "-o", "json"],
                 namespace=namespace, stage="provider-register")
            pending.append(namespace)
    # Provider登録の待機上限であり、環境の寿命や自動撤去を制御する時間ではない。
    deadline = time.monotonic() + 1800
    while pending:
        pending = [namespace for namespace in pending if _registration_run([
            "az", "provider", "show", "--namespace", namespace,
            "--subscription", config["subscriptionId"], "-o", "json"
        ], namespace=namespace, stage="provider-poll").get("registrationState") != "Registered"]
        if pending:
            if time.monotonic() >= deadline:
                raise DeploymentError("Resource provider registration is still pending; no core resources were deployed")
            time.sleep(10)
    ensure_host_encryption(config)


def prepare_diagnostic_settings(config: dict, transport: ArmTransport, phase: str, preserve_collection=False) -> dict:
    """最初の監視PUTの前にも、Core全体の既存診断と転送の追加範囲を確認する。"""
    try:
        return prepare_settings(build_bicep_configuration(config), transport, phase, preserve_collection)
    except DiagnosticPreservationError as error:
        raise DeploymentError(str(error)) from None


def _monitoring_export_reapply_ready(config: dict, template: dict, deployment: dict, transport, phase_deadline: float) -> dict | None:
    """既知のExport単独失敗と、現在の所有・設定一致を確認した場合だけ再適用を認める。"""
    try:
        same = lambda a, b: isinstance(a, str) and isinstance(b, str) and a.casefold() == b.casefold()
        ids = log_privacy_resource_ids(config)
        scope = f"/subscriptions/{config['subscriptionId']}"
        root_id = scope + f"/providers/Microsoft.Resources/deployments/{config['resourceStem']}-monitoring"
        child = scope + f"/resourceGroups/{config['resourceGroupNames']['monitor']}/providers/Microsoft.Resources/deployments/{config['resourceStem']}-monitoring"
        properties = deployment.get('properties', {})
        error = properties.get('error', {})
        details = error.get('details', []) if isinstance(error, dict) else []
        if (not same(deployment.get('id'), root_id) or properties.get('provisioningState') != 'Failed'
                or not isinstance(error, dict) or error.get('code') != 'DeploymentFailed'
                or not isinstance(details, list) or len(details) != 1
                or details[0].get('code') != 'ResourceDeploymentFailure'
                or not same(details[0].get('target'), child) or details[0].get('details') not in (None, [])):
            return None
        nested = [item.get('properties', {}).get('template') for item in template.get('resources', [])
                  if item.get('type', '').casefold() == 'microsoft.resources/deployments'
                  and isinstance(item.get('properties', {}).get('template', {}).get('variables', {}).get('exportTables'), list)]
        if len(nested) != 1:
            return None
        exports = [item for item in nested[0].get('resources', [])
                   if item.get('type', '').casefold() == 'microsoft.operationalinsights/workspaces/dataexports']
        if len(exports) != 1 or exports[0].get('properties', {}).get('tableNames') != "[variables('exportTables')]":
            return None
        variables = nested[0]['variables']; privacy = variables.get('privacy', {})
        if isinstance(privacy, str):
            reference = re.fullmatch(r"\[variables\('(\$fxv#[0-9]+)'\)\]", privacy)
            privacy = variables.get(reference[1]) if reference else None
        if not isinstance(privacy, dict):
            return None
        tables = [privacy.get('table') if name == "[variables('privacy').table]" else name
                  for name in variables['exportTables']]
        if (not 1 <= len(tables) <= 100 or any(not isinstance(name, str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,99}', name) is None for name in tables)
                or len(tables) != len(set(tables))):
            return None
        response = transport.request('GET', child + '/operations?api-version=2022-09-01')
        if response.get('statusCode') != 200 or not isinstance(response.get('body'), dict):
            return None
        page = response['body']; operations = page.get('value')
        if (page.get('nextLink') is not None or not isinstance(operations, list) or not 1 <= len(operations) <= 100
                or any(not isinstance(item, dict) or not isinstance(item.get('properties'), dict)
                       or item['properties'].get('provisioningState') not in ('Succeeded', 'Failed') for item in operations)):
            return None
        failed = [item['properties'] for item in operations if item['properties']['provisioningState'] == 'Failed']
        if len(failed) != 1:
            return None
        operation = failed[0]; target = operation.get('targetResource', {})
        message = operation.get('statusMessage', {})
        if isinstance(message, str):
            if len(message) > 4096:
                return None
            message = json.loads(message)
        failure = message.get('error', message) if isinstance(message, dict) else {}
        text = failure.get('message', '')
        if (not isinstance(target, dict) or not same(target.get('id'), ids['export'])
                or not same(target.get('resourceType'), 'Microsoft.OperationalInsights/workspaces/dataExports')
                or failure.get('code') != 'InvalidParameter' or failure.get('details') not in (None, [])
                or not isinstance(text, str) or len(text) > 4096
                or '404 (Not Found)' not in text or 'The workspace could not be found' not in text):
            return None
        workspace = transport.request('GET', ids['workspace'] + '?api-version=2023-09-01')
        body = workspace.get('body'); props = body.get('properties', {}) if isinstance(body, dict) else {}
        tags = body.get('tags', {}) if isinstance(body, dict) else {}
        if (workspace.get('statusCode') != 200 or not isinstance(body, dict)
                or not same(body.get('id'), ids['workspace']) or body.get('name') != 'law-' + config['resourceStem']
                or not same(body.get('type'), 'Microsoft.OperationalInsights/workspaces')
                or not same_azure_location(body.get('location'), config['location'])
                or tags.get('product') != config['productName'] or tags.get('environment') != config['environmentName']
                or props.get('provisioningState') != 'Succeeded'
                or not isinstance(props.get('customerId'), str) or re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', props['customerId']) is None):
            return None
        previous = local_paths(config, ROOT).state / 'latest.json'
        if previous.exists():
            prior = json.loads(previous.read_text())
            if any(prior.get(key) != config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')):
                return None
            known = prior.get('monitoring', {})
            if known.get('workspaceCustomerId') and (not same(known.get('workspaceId'), ids['workspace'])
                    or not same(known['workspaceCustomerId'], props['customerId'])):
                return None
        # 404だけをGETで待つ。300秒はsoft deadlineで、既存transportの処理中要求は
        # これを超え得る。元フェーズ期限は再PUT直前にも呼出元で再確認する。
        readiness_deadline = min(phase_deadline, time.monotonic() + 300)
        for probe in range(20):
            if time.monotonic() >= readiness_deadline:
                return None
            export = transport.request('GET', ids['export'] + '?api-version=2023-09-01')
            if export.get('statusCode') != 404:
                break
            remaining = readiness_deadline - time.monotonic()
            if probe == 19 or remaining <= 0:
                return None
            time.sleep(min(15, remaining))
        body = export.get('body'); props = body.get('properties', {}) if isinstance(body, dict) else {}
        names = props.get('tableNames', [])
        if (export.get('statusCode') != 200 or not isinstance(body, dict) or not same(body.get('id'), ids['export'])
                or props.get('enable') is not True or not same(props.get('destination', {}).get('resourceId'), ids['storage'])
                or not isinstance(names, list) or any(not isinstance(name, str) for name in names)
                or len(names) != len(set(names)) or set(names) != set(tables)):
            return None
        return {'failureKind': 'owned-export-workspace-404', 'operationCount': len(operations),
                'workspaceOwnedSucceeded': True, 'currentExportExactDesired': True,
                'exportReadinessProbes': probe + 1}
    except (DeploymentError, ValueError, OSError, KeyError, TypeError, AttributeError):
        return None


def _record_monitoring_reapply(config: dict, proof: dict) -> tuple[Path, dict]:
    """再PUTの前に、最初の失敗と一回限りの意図を所有者限定ファイルへ同期保存する。"""
    directory = private_parent(local_paths(config, ROOT).state / 'monitoring-reapply.json').parent
    handle, name = tempfile.mkstemp(prefix='monitoring-reapply-', suffix='.json', dir=directory)
    record = {**proof, 'status': 'reapply-intent', 'initialDeploymentState': 'Failed',
              'maximumAdditionalPuts': 1, 'additionalPutAttempts': 1,
              'observedAtUtc': datetime.now(timezone.utc).isoformat()}
    with os.fdopen(handle, 'w') as stream:
        if os.name != 'nt':
            os.fchmod(stream.fileno(), 0o600)
        json.dump(record, stream, indent=2); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())
    if os.name != 'nt':
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return Path(name), record


def deploy_phase(config: dict, phase: str, *, manage_policies=True, preserve_collection=False,
                 secrets: dict | None = None,
                 retained_policy_ids: list[str] | None = None, diagnostic_setting_properties: dict | None = None) -> dict:
    """Bicepをコンパイルし、azdと同じ所有タグで対象フェーズをARM配備する。

    要求受付だけで成功にせず、Succeededまで状態を読む。失敗や待機超過では
    通常は自動追加せず停止する。監視Export単独の既知失敗が現在の実設定で解消済みの
    場合だけ、最初の失敗を保存して同じ要求を一回再適用する。
    """
    template = run(["az", "bicep", "build", "--file", str(ROOT / "infra/main.bicep"), "--stdout"])
    # 配備タグはARM属性で、az deployment sub createには--tagsがない。ARM経由で
    # azdと同じ所有タグを付け、前処理で中断しても通常のdownが対象を認識できるようにする。
    path = f"/subscriptions/{config['subscriptionId']}/providers/Microsoft.Resources/deployments/{config['resourceStem']}-{phase}?api-version=2022-09-01"
    transport = ArmTransport(config)
    if phase == "core" and secrets is None:
        secrets = {**load_secret_inputs(config), **prepare_grpc_secret(config, ROOT, protected_json)}
    if phase == "core":
        require_certificate_registry_ready(config, transport)
        secrets = {**(secrets or {}), "ingressSecretRegistration": plan_secret_registration(config, ROOT, transport, secrets or {})}
    serial_password = _prepare_management_serial_password(config, transport) if phase == "core" else ""
    if phase == "core":
        prepare_core_network_upgrade(config, transport)
    # monitoring はネットワークを配備しない。core の直接呼出しでも native azd と
    # 同じ既存確認を行い、途中まで作成済みの場合にも残った動的規則を維持する。
    existing_nsg_names = discover_existing_network_security_groups(config, transport) if phase == "core" else []
    existing_routes = discover_existing_route_table_routes(config, transport) if phase == "core" else {}
    existing_apim_custom_properties = {}
    existing_apim_hostnames = discover_existing_apim_hostnames(config, transport, custom_properties_out=existing_apim_custom_properties) if phase == "core" else {}
    existing_apim_egress_fqdns = discover_existing_apim_egress_fqdns(config, transport) if phase == "core" else []
    existing_portal_app = discover_existing_portal_app(config, transport) if phase == "core" else False
    existing_workspace_privacy_dcr_id = read_workspace_privacy_link(config, transport)
    if diagnostic_setting_properties is None:
        diagnostic_setting_properties = prepare_diagnostic_settings(config, transport, phase, preserve_collection)
    if phase == "core":
        # An explicit secrets dict must not override the owned initial generation.
        secrets = {**{key: value for key, value in (secrets or {}).items() if key != "managementAdminPassword"},
                   "managementInitialSerialGeneration": _management_serial_generation(config, serial_password)}

    request_body = {
        "location": config["location"],
        "tags": {"azd-env-name": config["environmentName"], "azd-project-name": PROJECT_NAME, "azd-layer-name": ""},
        "properties": {"mode": "Incremental", "template": template,
                       "parameters": parameters(config, phase, secrets=secrets, manage_policies=manage_policies,
                                                retained_policy_ids=retained_policy_ids,
                                                preserve_collection=preserve_collection,
                                                existing_nsg_names=existing_nsg_names,
                                                existing_route_table_routes=existing_routes,
                                                existing_apim_hostname_configurations=existing_apim_hostnames,
                                                existing_apim_custom_properties=existing_apim_custom_properties,
                                                existing_apim_egress_fqdns=existing_apim_egress_fqdns,
                                                existing_portal_app=existing_portal_app,
                                                existing_workspace_privacy_dcr_id=existing_workspace_privacy_dcr_id,
                                                diagnostic_setting_properties=diagnostic_setting_properties)["parameters"]},
    }
    if phase == "core":
        _confirm_management_serial_target(config, transport, serial_password)
        _record_management_serial_creation(config, serial_password)
    response = transport.request("PUT", path, body=request_body)
    require_success(response, f"Start {phase} deployment", allowed=(200, 201, 202))
    deadline = time.monotonic() + 7200
    reapply = None
    while True:
        deployment = require_success(transport.request("GET", path), f"Read {phase} deployment")
        status = deployment.get("properties", {}).get("provisioningState")
        if status == "Succeeded":
            if reapply is not None:
                protected_json(reapply[0], {**reapply[1], 'status': 'reapply-succeeded'})
            outputs = {key: item["value"] for key, item in deployment["properties"]["outputs"].items()}
            if phase == "core":
                if _body_audit_storage(config)["immutabilityMode"] == "Locked":
                    lock_body_audit_policy(config, outputs, transport)
                if outputs.get("certificateBootstrap"):
                    outputs, _ = finish_certificate_core(
                        config, outputs, transport, serial_password,
                        request_body["properties"]["parameters"]["l7IngressSecret"]["value"],
                        request_body["properties"]["parameters"]["firewallTlsInspection"]["value"].get("rootCaData", ""))
                else:
                    _save_deployment_state(config, outputs)
                    _bind_management_serial_password(config, outputs, transport, serial_password)
                    complete_core_network_upgrade(config, transport)
                    protected_json(local_paths(config, ROOT).state / "ingress-bootstrap.json",
                                   configure_grpc_endpoint(config, outputs, transport,
                                                           mutation_recorder=upgrade_journal(config, "ingress-endpoint")))
            return outputs
        if status == "Failed" and phase == "monitoring" and reapply is None and time.monotonic() < deadline:
            proof = _monitoring_export_reapply_ready(config, template, deployment, transport, deadline)
            if proof is not None and time.monotonic() < deadline:
                reapply = _record_monitoring_reapply(config, proof)
                if time.monotonic() >= deadline:
                    protected_json(reapply[0], {**reapply[1], 'status': 'deadline-expired-no-reapply', 'additionalPutAttempts': 0})
                    raise DeploymentError('Monitoring recovery exceeded the original deadline; inspect its status before retrying')
                print('Monitoring: confirmed Export configuration matches; reapplying the same deployment once. The first failure is retained.', flush=True)
                try:
                    require_success(transport.request('PUT', path, body=request_body), 'Reapply monitoring deployment', allowed=(200, 201, 202))
                except DeploymentError:
                    protected_json(reapply[0], {**reapply[1], 'status': 'reapply-request-failed-or-unconfirmed'})
                    raise
                continue
        if status in ("Failed", "Canceled", "Deleted"):
            if reapply is not None:
                protected_json(reapply[0], {**reapply[1], 'status': 'reapply-' + status.lower()})
            raise DeploymentError(f"The {phase} deployment {status}; inspect that deployment before continuing")
        if time.monotonic() >= deadline:
            raise DeploymentError(f"The {phase} deployment is still pending; inspect its status before retrying")
        time.sleep(10)


def _assert_current_apim_instance(config: dict) -> None:
    try:
        assert_current_instance(config, local_paths(config, ROOT))
    except ValueError as error:
        raise DeploymentError(str(error)) from None


def state_from_outputs(config: dict, outputs: dict) -> dict:
    """実際のBicep出力から、再実行と撤去に使う所有RG・サービスIDの記録を作る。"""
    service_ids = outputs.get("cleanup", {}).get("serviceResourceIds", {})
    service_locations = {"keyVault": config["location"], "foundry": config["foundryLocation"], "apim": config["location"]}
    state = {
        "subscriptionId": config["subscriptionId"], "azureTenantId": config["azureTenantId"],
        "entraTenantId": entra_tenant_id(config),
        "resourceStem": config["resourceStem"], "resourceGroupNames": config["resourceGroupNames"],
        "apimInstanceId": apim_instance_id(config),
        "storage": outputs.get("auditStorage", {}), "monitoring": outputs.get("monitoring", {}),
        "serviceResourceIds": service_ids,
        "serviceLocations": service_locations,
        "outputs": outputs,
    }
    if "certificateVault" in state["serviceResourceIds"]:
        state["serviceLocations"]["certificateVault"] = config["location"]
    try:
        verify_apim_state(config, state)
    except ValueError as error:
        raise DeploymentError(str(error)) from None
    return state


def _read_core_checkpoint(config: dict) -> dict | None:
    """選択中のCoreに対応する状態を読み、Azure環境とAPIM識別子を照合する。"""
    try:
        previous = json.loads((local_paths(config, ROOT).state / "latest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise DeploymentError("Existing Core state could not be verified; preserve it and review it before rebuilding") from None
    if not isinstance(previous, dict):
        raise DeploymentError("Existing Core state could not be verified; preserve it and review it before rebuilding")
    if (any(not isinstance(previous.get(key), str) or previous[key].casefold() != config[key].casefold()
            for key in ("subscriptionId", "azureTenantId", "resourceStem"))
            or previous.get("resourceGroupNames") != config["resourceGroupNames"]):
        raise DeploymentError("Existing application infrastructure checkpoint belongs to a different Core environment")
    try:
        verify_apim_state(config, previous)
    except ValueError as error:
        raise DeploymentError(str(error)) from None
    return previous


def _read_application_infrastructure_checkpoint(config: dict) -> list[dict] | None:
    """同じ環境の撤去記録だけを引き継ぎ、再構築で旧CAEの管理RGを見失わない。"""
    previous = _read_core_checkpoint(config)
    if previous is None or "applicationInfrastructure" not in previous:
        return None
    from .helpers.cleanup import CleanupError, _application_infrastructure
    try:
        return _application_infrastructure(config, previous["applicationInfrastructure"])
    except CleanupError:
        raise DeploymentError("Existing application infrastructure checkpoint is invalid; preserve it and review it before rebuilding") from None


def _save_deployment_state(config: dict, outputs: dict) -> dict:
    """基盤出力を保存し、未回収の管理RGの検証済みcheckpointを維持する。"""
    inventory = _read_application_infrastructure_checkpoint(config)
    state = state_from_outputs(config, outputs)
    if inventory is not None:
        state["applicationInfrastructure"] = inventory
    protected_json((local_paths(config, ROOT).state / "latest.json"), state)
    return state


def _validate_parameter_registrations(config: dict, source: Path, command: str) -> None:
    """配備前に入力と、監査中継が使用できる認証テナントを確認します。"""
    if (config.get("auditEvidence", {}).get("enabled") is True
            and config["entraTenantId"] != config["azureTenantId"]):
        raise DeploymentError("Audit evidence requires entraTenantId to match azureTenantId before Core deployment")
    workspace = local_paths(config, ROOT)
    workspace.checked(source)
    workspace.check_selection(config)


def prepare_immutability(storage: dict, transport: ArmTransport) -> list[str]:
    """既存 Locked を個別に保持し、追加・未ロックの保存先だけを管理できるようにする。

    全体を更新しないフラグでは、運用中の保存先へ新しいカテゴリを追加できない。
    実際の Locked ID を返し、既存ポリシーへの PUT を避ける。途中でロックが
    中断した場合も、確定済みの保護を維持したまま残りを再実行できる。
    """
    policies = storage["immutabilityPolicyIds"]
    states = [transport.request("GET", path + "?api-version=2023-05-01") for path in policies]
    for response in states:
        if response["statusCode"] != 404:
            require_success(response, "Read audit immutability policy")
    retained = []
    for path, response in zip(policies, states):
        if response["statusCode"] == 404:
            continue
        policy = (response.get("body") or {})
        properties = policy.get("properties", {})
        period = properties.get("immutabilityPeriodSinceCreationInDays")
        # 未設定・削除済みポリシーでもHTTP 200のメタデータが返る。
        # 撤去時と同様、Deletedかつ整数の保持期間0だけを保護対象なしと扱う。
        if properties.get("state") == "Deleted" and type(period) is int and period == 0:
            continue
        if properties.get("state") not in ("Locked", "Unlocked"):
            raise DeploymentError("Audit policy state could not be confirmed")
        _verify_audit_policy_id(storage, path, policy)
        if storage.get("allowProtectedAppendWrites") is False and storage["immutabilityMode"] == "Disabled":
            raise DeploymentError("Existing body evidence has a WORM policy; configuration cannot disable it")
        if properties["state"] == "Locked":
            if not _audit_policy_matches(storage, properties):
                raise DeploymentError("Locked audit policy retention/append configuration does not match the requested settings")
            retained.append(path)
    return retained


def _verify_audit_policy_id(storage: dict, path: str, policy: dict) -> None:
    if storage.get("allowProtectedAppendWrites") is False and str(policy.get("id", "")).casefold() != path.casefold():
        raise DeploymentError("Body evidence policy readback does not match the configured container")


def _audit_policy_matches(storage: dict, properties: dict) -> bool:
    period = properties.get("immutabilityPeriodSinceCreationInDays")
    if type(period) is not int or period < storage["retentionInDays"]:
        return False
    if storage.get("allowProtectedAppendWrites", True):
        return properties.get("allowProtectedAppendWrites") is True
    return (properties.get("allowProtectedAppendWrites", False) is False
            and properties.get("allowProtectedAppendWritesAll", False) is False)


def lock_and_verify_policies(storage: dict, transport: ArmTransport) -> None:
    """指定した保持期間と追記設定を確認し、必要なときだけ不可逆の保持ロックを行う。

    更新前にETagを取り、同時変更を踏み越えない。設定後も読み直して確定を確認する。
    """
    if storage["immutabilityMode"] == "Disabled":
        return
    for path in storage["immutabilityPolicyIds"]:
        response = transport.request("GET", path + "?api-version=2023-05-01")
        policy = require_success(response, "Read audit immutability policy")
        _verify_audit_policy_id(storage, path, policy)
        props = policy.get("properties", {})
        if props.get("state") not in ("Locked", "Unlocked") or not _audit_policy_matches(storage, props):
            raise DeploymentError("Audit policy retention/append configuration does not match the requested settings")
        if storage["immutabilityMode"] == "Locked" and props.get("state") != "Locked":
            etag = next((value for key, value in response["headers"].items() if key.lower() == "etag"), None) or policy.get("etag")
            if not isinstance(etag, str) or not etag.strip() or etag.strip('" ') == '*':
                raise DeploymentError("Audit policy ETag is missing; lock was not attempted")
            require_success(transport.request("POST", path + "/lock?api-version=2023-05-01", headers={"If-Match": etag}), "Lock audit policy")
            policy = require_success(transport.request("GET", path + "?api-version=2023-05-01"), "Read back locked audit policy")
            _verify_audit_policy_id(storage, path, policy)
            props = policy.get("properties", {})
            if props.get("state") != "Locked" or not _audit_policy_matches(storage, props):
                raise DeploymentError("Audit policy lock could not be confirmed")


def _body_audit_storage(config: dict) -> dict:
    settings = config.get("auditEvidence", {})
    return {"immutabilityMode": settings.get("payloadImmutabilityMode", "Disabled") if settings.get("enabled") else "Disabled",
            "retentionInDays": settings.get("payloadRetentionDays", 30),
            "immutabilityPolicyIds": [evidence_policy_id(config)], "allowProtectedAppendWrites": False}


def prepare_body_audit_immutability(config: dict, transport: ArmTransport, *, previously_deployed: bool = False) -> list[str]:
    """本文Blobの既存ロックを再利用し、30日削除とWORMの同時設定を検出します。"""
    if not config.get("auditEvidence", {}).get("enabled") and not previously_deployed:
        return []
    storage = _body_audit_storage(config)
    retained = prepare_immutability(storage, transport)
    if storage["immutabilityMode"] != "Locked":
        return retained
    account_id = evidence_container_id(config).removesuffix("/blobServices/default/containers/payload")
    path = account_id + "/managementPolicies/default"
    response = transport.request("GET", path + "?api-version=2023-05-01")
    existing = require_success(response, "Read body evidence lifecycle policy", allowed=(200, 404))
    if response["statusCode"] == 200:
        props = existing.get("properties", {}).get("policy", {})
        rules = props.get("rules")
        if str(existing.get("id", "")).casefold() != path.casefold() or not isinstance(rules, list):
            raise DeploymentError("Body evidence lifecycle policy could not be confirmed")
        for rule in rules:
            if rule.get("enabled", True) and any("delete" in action for action in rule.get("definition", {}).get("actions", {}).values()):
                raise DeploymentError("Review the existing body evidence delete rule before enabling WORM")
    return retained


def lock_body_audit_policy(config: dict, outputs: dict, transport: ArmTransport) -> None:
    """成功したCoreが用意した本文コンテナーだけにWORMを確定します。"""
    if not config.get("auditEvidence", {}).get("enabled") or config["auditEvidence"].get("payloadImmutabilityMode") != "Locked":
        return
    storage = _body_audit_storage(config)
    evidence = outputs.get("auditEvidence") or {}
    container_id = evidence_container_id(config)
    path = storage["immutabilityPolicyIds"][0]
    recorded = evidence.get("payloadImmutability") or {}
    if (str(evidence.get("evidenceContainerId", "")).casefold() != container_id.casefold()
            or str(recorded.get("policyId", "")).casefold() != path.casefold()
            or recorded.get("mode") != storage["immutabilityMode"]
            or recorded.get("retentionInDays") != storage["retentionInDays"]):
        raise DeploymentError("Core output does not match the configured body evidence container and retention")
    lock_and_verify_policies(storage, transport)


def configure_app_insights_cap(config: dict, outputs: dict) -> None:
    """ARM資格情報を標準入力でPowerShellへ渡し、Application Insightsの日次上限を設定・再読取する。"""
    auth = arm_access_token(config["subscriptionId"], config["azureTenantId"])
    account = run(["az", "account", "show", "--subscription", config["subscriptionId"], "-o", "json"])
    executable = locate_powershell()
    run([executable, "-NoLogo", "-NoProfile", "-File", str(ROOT / "scripts/deployment/helpers/set-app-insights-cap.ps1")], environment=powershell_environment(executable), payload={
        "tenantId": config["azureTenantId"], "subscriptionId": config["subscriptionId"],
        "accountId": account["user"]["name"], "accessToken": auth["accessToken"],
        "resourceGroupName": config["resourceGroupNames"]["monitor"],
        "appInsightsName": outputs["monitoring"]["appInsightsName"], "dailyCapGb": 100,
    })


def ensure_api_center_available(config: dict, outputs: dict, transport: ArmTransport) -> dict:
    """今回の同名API Centerだけを確認し、論理削除されていれば同名へ復元する。

    名前は監視段階のBicep出力を正本にする。別RG・別名の削除履歴には触れず、
    全ページを確認してから復元する。復元後も実資源のID・場所・所有タグを
    読み直し、要求が受け付けられただけでは成功としない。
    """
    names = outputs.get("names")
    name = names.get("apiCenter") if isinstance(names, dict) else None
    if not isinstance(name, str) or name.casefold() != ("apic-" + config["resourceStem"]).casefold():
        raise DeploymentError("Bicep did not return the expected API Center name")
    scope = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}/providers/Microsoft.ApiCenter"
    service_id = scope + "/services/" + name
    live_path = service_id + "?api-version=2024-03-01"
    expected_tags = {"product": config["productName"], "environment": config["environmentName"],
                     "azd-env-name": config["environmentName"]}

    def verify_live(response: dict) -> str:
        """所有識別を確認してから、Azureが返した構築状態を限定した値で扱う。"""
        body = require_success(response, "Read API Center", allowed=(200,))
        if (not isinstance(body, dict)
                or str(body.get("id", "")).casefold() != service_id.casefold()
                or str(body.get("name", "")).casefold() != name.casefold()
                or str(body.get("type", "")).casefold() != "microsoft.apicenter/services"
                or not same_azure_location(body.get("location"), config["apiCenterLocation"])
                or not isinstance(body.get("tags"), dict)
                or any(body["tags"].get(key) != value for key, value in expected_tags.items())):
            raise DeploymentError("API Center identity, location or ownership tags do not match this environment")
        properties = body.get("properties")
        state = properties.get("provisioningState") if isinstance(properties, dict) else None
        if state not in ("Succeeded", "Accepted", "Creating", "Updating"):
            raise DeploymentError("API Center provisioning state could not be confirmed")
        return state

    live = transport.request("GET", live_path)
    if live.get("statusCode") == 200:
        if verify_live(live) != "Succeeded":
            raise DeploymentError("API Center is still being changed; inspect it before retrying")
        return {"status": "existing", "resourceId": service_id}
    require_success(live, "Check API Center existence", allowed=(404,))

    # Subscription全体の削除済み一覧ではなく、今回のsvc RGの一覧だけを読む。
    # nextLinkも同じARMホスト・同じ一覧・同じAPI版に限定し、外部URLへ認証を送らない。
    deleted_path = scope + "/deletedServices"
    next_page = deleted_path + "?api-version=2024-06-01-preview"
    visited, seen_ids = set(), set()
    found = False
    while next_page:
        try:
            uri = urlparse(next_page if next_page.startswith("https://") else "https://management.azure.com" + next_page)
            port = uri.port
        except ValueError:
            raise DeploymentError("API Center deleted-service pagination could not be verified") from None
        if (uri.scheme != "https" or uri.hostname != "management.azure.com"
                or uri.username is not None or uri.password is not None or port not in (None, 443)
                or uri.fragment or uri.path.casefold() != deleted_path.casefold()
                or parse_qs(uri.query).get("api-version") != ["2024-06-01-preview"]
                or uri.geturl() in visited or len(visited) >= 100):
            raise DeploymentError("API Center deleted-service pagination could not be verified")
        visited.add(uri.geturl())
        page = require_success(transport.request("GET", uri.geturl()), "Read deleted API Centers", allowed=(200,))
        entries = page.get("value") if isinstance(page, dict) else None
        if not isinstance(entries, list):
            raise DeploymentError("API Center deleted-service list is malformed")
        for entry in entries:
            entry_name = entry.get("name") if isinstance(entry, dict) else None
            entry_id = entry.get("id") if isinstance(entry, dict) else None
            if (not isinstance(entry_name, str) or re.fullmatch(r"[a-zA-Z0-9-]{3,90}", entry_name) is None
                    or not isinstance(entry_id, str)
                    or entry_id.casefold() != (deleted_path + "/" + entry_name).casefold()
                    or str(entry.get("type", "")).casefold() != "microsoft.apicenter/deletedservices"
                    or entry_id.casefold() in seen_ids):
                raise DeploymentError("API Center deleted-service identity could not be verified")
            seen_ids.add(entry_id.casefold())
            found = found or entry_name.casefold() == name.casefold()
        next_page = page.get("nextLink")
        if next_page is not None and (not isinstance(next_page, str) or not next_page):
            raise DeploymentError("API Center deleted-service continuation is malformed")
    if not found:
        return {"status": "available-for-create", "resourceId": service_id}

    # restore=trueは既存の同名資源を戻す操作。他の設定変更は後続Bicepが担当する。
    require_success(transport.request("PUT", service_id + "?api-version=2024-06-01-preview",
        body={"location": config["apiCenterLocation"], "properties": {"restore": True}}),
        "Restore the owned API Center", allowed=(200, 201, 202))
    for attempt in range(60):
        actual = transport.request("GET", live_path)
        if actual.get("statusCode") == 200 and verify_live(actual) == "Succeeded":
            return {"status": "restored", "resourceId": service_id}
        if actual.get("statusCode") != 200:
            require_success(actual, "Read restored API Center", allowed=(404,))
        if attempt < 59:
            time.sleep(5)
    raise DeploymentError("API Center restore is still pending; inspect it before retrying")


def _preprovision(config: dict) -> None:
    """検査→Provider登録→固定名・利用枠確認→監査基盤→コア入力の順に準備する。

    有料構築前に設定や資格情報の不備を検出し、コアより先にログ保存先を揃える。
    再upでは既存の収集やLocked保持を弱めない。
    """
    # 第1段階: ローカルで失敗を検出する。ファイルや依存関係の不足で
    # 有料リソースを作ってから停止することを避ける。
    _assert_current_apim_instance(config)
    _read_application_infrastructure_checkpoint(config)
    local_preflight(config)
    # 第2段階: ログイン先・固定名・残り利用枠を読取で確認する。
    # 誤ったテナントや不足した利用枠へ構築を開始しない。
    azure_preflight(config)
    # 初めて使うサブスクでも名前照会APIを利用できるよう、無課金のRP登録を
    # 先に済ませる。RG・監視・VM等の課金資源は、以下の確認が終わるまで作らない。
    register_providers(config)
    transport = ArmTransport(config)
    require_core_appgw_ready(config, transport)
    require_certificate_registry_ready(config, transport)
    serial_password = _prepare_management_serial_password(config, transport)
    secrets = {**load_secret_inputs(config), **prepare_grpc_secret(config, ROOT, protected_json),
               "managementInitialSerialGeneration": _management_serial_generation(config, serial_password)}
    secrets["ingressSecretRegistration"] = plan_secret_registration(config, ROOT, transport, secrets)
    # 固定名の競合は有料の監視・ネットワーク作成より前に検出する。
    # 回避名のAPIMを増やさず、同名で再実行できる状態かだけを読み取る。
    apim_name_receipt = local_paths(config, ROOT).state / "apim-name-preparation.json"
    apim_name_receipt.unlink(missing_ok=True)
    try:
        apim_name_check = verify_apim_name(config, transport)
    except ApimPreflightError as error:
        raise DeploymentError(str(error)) from None
    protected_json(apim_name_receipt, {
        **apim_name_check, "observedAtUtc": datetime.now(timezone.utc).isoformat(),
    })
    previous_response = transport.request("GET", f"/subscriptions/{config['subscriptionId']}/providers/Microsoft.Resources/deployments/{config['resourceStem']}-monitoring?api-version=2022-09-01")
    previous_outputs = require_success(previous_response, "Read prior monitoring deployment", allowed=(200, 404)).get("properties", {}).get("outputs", {})
    capacity_preflight(config, transport, previous_outputs)
    # 第3段階: 既存の監査保存先を照合する。保持設定や証跡を初期化しない。
    previous = {"storage": previous_outputs.get("auditStorage", {}).get("value", {})}
    retained_policy_ids = []
    if previous.get("storage"):
        requested_storage = {**previous["storage"], "retentionInDays": config["monitoring"]["auditRetentionDays"]}
        retained_policy_ids = prepare_immutability(requested_storage, transport)
        if retained_policy_ids and config["monitoring"]["auditImmutabilityMode"] != "Locked":
            raise DeploymentError("Existing audit retention is Locked; configuration cannot weaken it")
    preserve_collection = previous_outputs.get("monitoring", {}).get("value", {}).get("auditCollectionEnabled", False)
    # 前段の監視配備だけでは直近の収集状態を表せない場合がある。前回コアの成功出力も
    # 調べ、再upによって有効なログ収集を弱めない。
    previous_body = False
    if (local_paths(config, ROOT).state / "latest.json").exists():
        state = json.loads((local_paths(config, ROOT).state / "latest.json").read_text())
        if all(state.get(key) == config[key] for key in ("subscriptionId", "azureTenantId", "resourceStem")):
            preserve_collection = preserve_collection or state.get("monitoring", {}).get("auditCollectionEnabled", False)
            previous_body = bool(state.get("outputs", {}).get("auditEvidence"))
    body_retained = prepare_body_audit_immutability(config, transport, previously_deployed=previous_body)
    if previous_outputs.get("monitoring", {}).get("value", {}).get("workspaceId"):
        workspace_id = previous_outputs["monitoring"]["value"]["workspaceId"]
        actual_diag = transport.request("GET", workspace_id + f"/providers/Microsoft.Insights/diagnosticSettings/diag-{config['resourceStem']}-law?api-version=2021-05-01-preview")
        diag = require_success(actual_diag, "Read current workspace audit collection", allowed=(200, 404))
        preserve_collection = preserve_collection or bool(diag.get("properties", {}).get("storageAccountId"))
    # 第4段階: コアの操作ログを受け取れるよう、監視・監査の保存先を先に作る。
    # 実際のResource IDを直ちに記録し、途中失敗でも撤去時に同じ対象を辿れるようにする。
    monitoring_diagnostics = prepare_diagnostic_settings(config, transport, "monitoring", preserve_collection)
    outputs = deploy_phase(config, "monitoring", manage_policies=True,
                           retained_policy_ids=retained_policy_ids, preserve_collection=preserve_collection,
                           diagnostic_setting_properties=monitoring_diagnostics)
    _save_deployment_state(config, outputs)
    # Workspace作成とDCR作成を終えてから関連付け、次のCore PUTでもその実参照を保持します。
    workspace_privacy_dcr_id = ensure_workspace_link(config, transport)
    # Action Groupは、この後の公開入口配備で作られます。確認メールが既に届いた
    # という案内にはせず、届いた時点から30分以内の本人確認が必要だと先に知らせます。
    # 長い構築の完了を待たず確認し、メールアドレス・OTPはここへ入力しません。
    print(f"Action group ag-{config['resourceStem']}-ops will be created during the public ingress deployment. "
          "New/unverified email recipients should watch for Azure's verification email and complete "
          "verification within 30 minutes from when that email is sent; do not wait for azd up to finish. "
          "Never enter the OTP here. If expired, use Azure Portal > Action group > Overview > "
          "Notifications > Resend. Verify the recipient status and actual test-email delivery separately.")
    lock_and_verify_policies(outputs["auditStorage"], transport)
    configure_app_insights_cap(config, outputs)
    # 監視段階で5RGを作った後なら、同じsvc RG内の論理削除を照合・復元できる。
    # 公開名を変更せず、事前登録したEntra Portalの戻り先もそのまま使う。
    api_center_receipt = local_paths(config, ROOT).state / "api-center-preparation.json"
    api_center_receipt.unlink(missing_ok=True)
    protected_json(api_center_receipt, {
        **ensure_api_center_available(config, outputs, transport),
        "subscriptionId": config["subscriptionId"], "resourceStem": config["resourceStem"],
        "observedAtUtc": datetime.now(timezone.utc).isoformat(),
    })
    # 第5段階: 最後にコア構築用のsecure入力を生成する。通常のazd provisionへ
    # 引き渡し、成功後またはhook失敗時にこの生成ファイルを回収する。
    prepare_core_network_upgrade(config, transport)
    existing_nsg_names = discover_existing_network_security_groups(config, transport)
    existing_routes = discover_existing_route_table_routes(config, transport)
    existing_apim_custom_properties = {}
    existing_apim_hostnames = discover_existing_apim_hostnames(config, transport, custom_properties_out=existing_apim_custom_properties)
    existing_portal_app = discover_existing_portal_app(config, transport)
    existing_apim_egress_fqdns = discover_existing_apim_egress_fqdns(config, transport)
    core_diagnostics = prepare_diagnostic_settings(config, transport, "core")

    _confirm_management_serial_target(config, transport, serial_password)
    _record_management_serial_creation(config, serial_password, native=True)
    body_worm = _body_audit_storage(config)["immutabilityMode"] == "Locked"
    monitoring_worm = config["monitoring"]["auditImmutabilityMode"] == "Locked"
    # 本文ポリシーはCore配備で作成する。監視段階ですでにロックしたポリシーはPUTから外す。
    core_retained = [*body_retained, *(outputs["auditStorage"]["immutabilityPolicyIds"] if monitoring_worm and body_worm else [])]
    write_arm_parameters(config, parameters(config, "core", secrets=secrets,
        manage_policies=body_worm or not monitoring_worm,
        retained_policy_ids=core_retained,
        existing_nsg_names=existing_nsg_names,
        existing_route_table_routes=existing_routes,
        existing_apim_hostname_configurations=existing_apim_hostnames,
        existing_apim_custom_properties=existing_apim_custom_properties,
        existing_portal_app=existing_portal_app,
        existing_apim_egress_fqdns=existing_apim_egress_fqdns,
        existing_workspace_privacy_dcr_id=workspace_privacy_dcr_id,
        diagnostic_setting_properties=core_diagnostics))
    print("Monitoring destinations are prepared. Native azd provision will create the core resources.")


def preprovision(config: dict) -> None:
    """構築前処理を呼び、途中失敗時には生成した秘密入りARM入力ファイルを回収する。"""
    try:
        _preprovision(config)
    except BaseException:
        local_paths(config, ROOT).parameters.unlink(missing_ok=True)
        raise


def waiting_for_appgw_certificate_role(error):
    """新しい対象secretのロール伝搬だけを公開入口の再配備へ渡す。"""
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    if code == "UserAssignedIdentityDoesNotHaveGetPermissionOnKeyVault":
        return True
    if code not in ("DeploymentFailed", "ResourceDeploymentFailure", "ApplicationGatewayKeyVaultSecretException"):
        return False
    details = error.get("details")
    return isinstance(details, list) and bool(details) and all(waiting_for_appgw_certificate_role(item) for item in details)


def public_ingress_deployment_id(config):
    return (f"/subscriptions/{config['subscriptionId']}/providers/Microsoft.Resources/deployments/"
            f"{config['resourceStem']}-public-ingress")


def deploy_public_ingress(config, outputs, transport, l7_secret, firewall_root_ca, *, sleep=time.sleep, clock=time.monotonic):
    """証明書ゲート後に、AppGWと公開入口に従属する監視だけを配備する。"""
    if not isinstance(l7_secret, str) or not re.fullmatch(r"[a-f0-9]{128}", l7_secret):
        raise DeploymentError("The protected L7 ingress credential is required")
    gateway_plan, monitoring_plan = outputs["gatewayPlan"], outputs["ingressMonitoringPlan"]
    bootstrap = outputs["certificateBootstrap"]
    if gateway_plan.get("keyVaultCertificateSecretId") != bootstrap["certificateSecretUri"]:
        raise DeploymentError("The gateway plan has a different certificate reference")
    template = run(["az", "bicep", "build", "--file", str(ROOT / "infra/public-ingress.bicep"), "--stdout"])
    resource = public_ingress_deployment_id(config)
    path = resource + "?api-version=2022-09-01"
    body = {"location": config["location"],
            "tags": {"azd-env-name": config["environmentName"], "azd-project-name": PROJECT_NAME, "azd-layer-name": ""},
            "properties": {"mode": "Incremental", "template": template, "parameters": {
                "gatewayPlan": {"value": gateway_plan}, "ingressMonitoringPlan": {"value": monitoring_plan},
                "firewallRootCaData": {"value": firewall_root_ca},
                "monitoringContact": {"value": {"operatorEmailAddresses": config["monitoring"]["operatorEmailAddresses"]}},
                "l7IngressSecret": {"value": l7_secret}}}}
    initial = transport.request("GET", path)
    existing = require_success(initial, "Inspect public ingress deployment", allowed=(200, 404))
    current = existing.get("properties", {})
    if initial["statusCode"] == 200 and str(existing.get("id", "")).casefold() != resource.casefold():
        raise DeploymentError("Public ingress deployment returned another resource")
    if initial["statusCode"] == 404 or current.get("provisioningState") in ("Succeeded", "Failed", "Canceled"):
        require_success(transport.request("PUT", path, body=body), "Deploy public ingress", allowed=(200, 201, 202))
    else:
        recorded = current.get("parameters") or {}
        if (recorded.get("gatewayPlan", {}).get("value") != gateway_plan
                or recorded.get("ingressMonitoringPlan", {}).get("value") != monitoring_plan):
            raise DeploymentError("An existing public ingress request differs from the current input")
    deadline = clock() + 3600
    role_retries = 0
    while clock() < deadline:
        observed = require_success(transport.request("GET", path), "Read public ingress deployment")
        if str(observed.get("id", "")).casefold() != resource.casefold():
            raise DeploymentError("Public ingress deployment returned another resource")
        props = observed.get("properties", {})
        status = props.get("provisioningState")
        if status == "Succeeded":
            value = props.get("outputs", {}).get("gateway", {}).get("value")
            expected_url = "https://" + config["gateway"]["publicHostName"]
            if (not isinstance(value, dict) or value.get("publicUrl") != expected_url
                    or str(value.get("id", "")).casefold() != gateway_id(config).casefold()
                    or value.get("publicIpId") != gateway_plan.get("publicIpId")
                    or value.get("publicIp") != gateway_plan.get("publicIpAddress")):
                raise DeploymentError("Public ingress output does not match the prepared gateway")
            return value
        if (status == "Failed" and role_retries < 5 and clock() + 30 < deadline
                and waiting_for_appgw_certificate_role(props.get("error"))):
            role_retries += 1
            sleep(30)
            require_success(transport.request("PUT", path, body=body), "Retry public ingress certificate access",
                            allowed=(200, 201, 202))
            continue
        if status in ("Failed", "Canceled", "Deleted"):
            raise DeploymentError("Public ingress deployment did not succeed")
        sleep(min(10, max(0, deadline - clock())))
    raise DeploymentError("Public ingress deployment is still pending; inspect the existing request")


def observe_initial_certificate(config, gateway, certificate, *, sleep=time.sleep, clock=time.monotonic):
    """公開DNSを更新する前に対象PIPへ接続し、両SNIの実leafを照合する。"""
    address = str(ipaddress.IPv4Address(gateway["publicIp"]))
    hosts = (config["gateway"]["publicHostName"], config["portal"]["publicHostName"])
    expected = base64.b64decode(certificate["publicCertificate"], validate=True)
    expected_hash = hashlib.sha256(expected).digest()
    if config["certificate"].get("directory") == "staging":
        # Stagingの信頼は、この実行が検証した公開leafだけに限定する。
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(expected))
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    else:
        context = ssl.create_default_context()
    deadline = clock() + 900
    while clock() < deadline:
        for hostname in hosts:
            try:
                with socket.create_connection((address, 443), timeout=max(0.1, min(10, deadline - clock()))) as raw:
                    with context.wrap_socket(raw, server_hostname=hostname) as tls:
                        if hashlib.sha256(tls.getpeercert(binary_form=True)).digest() != expected_hash:
                            break
            except (OSError, ssl.SSLError):
                break
        else:
            return
        sleep(min(10, max(0, deadline - clock())))
    raise DeploymentError("The expected certificate was not observed on both gateway hostnames; public DNS was not advanced")


def finish_public_dns(config, outputs, transport):
    certificate = config["certificate"]
    dns_transport = None
    if certificate.get("mode") == "dnsPlugin" and certificate.get("plugin") == "Azure":
        dns_transport = ArmTransport({**config, "subscriptionId": certificate["azureDnsSubscriptionId"]})
        print("公開DNS: GatewayとPortalのAエイリアスを確認しています。", flush=True)
    return configure_public_dns(config, outputs, transport, dns_transport)


def require_certificate_job_cutover(config, outputs, transport):
    """旧VMタイマーの管理資源が残る環境との並行発行を避ける。"""
    vm = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['maint']}"
          f"/providers/Microsoft.Compute/virtualMachines/vm-{config['resourceStem']}-mgmt")
    if str(outputs.get("managementVm", {}).get("id", "")).casefold() != vm.casefold():
        raise DeploymentError("The certificate bootstrap VM does not match the selected Core deployment")
    response = transport.request("GET", vm + "/runCommands/configure-public-certificate?api-version=2024-07-01")
    if response.get("statusCode") != 404:
        raise DeploymentError("Verify retirement of the previous VM certificate automation before starting the ACA job")


def finish_certificate_core(config, outputs, transport, serial_password, l7_secret, firewall_root_ca):
    """Coreの依存先を記録し、初回発行・公開・DNSを同じ順序で完了する。"""
    from .helpers.certificate_jobs import complete_initial_certificates
    from .helpers.gateway_obo_certificate import store_initial as store_gateway_obo_certificate
    from .helpers.portal_auth_certificate import store_initial_certificate
    from operations.common import OperationContext, OperationError

    workspace = local_paths(config, ROOT)
    context = OperationContext(workspace.params, config, state_from_outputs(config, outputs), workspace, transport)
    try:
        with context.locked():
            _save_deployment_state(config, outputs)
            require_certificate_job_cutover(config, outputs, transport)
            dns_path = workspace.state / "public-dns.json"
            dns_path.unlink(missing_ok=True)
            _bind_management_serial_password(config, outputs, transport, serial_password)
            complete_core_network_upgrade(config, transport)
            issued = complete_initial_certificates(config, outputs, transport, protected_json,
                                                   workspace=workspace, repository=ROOT)
            store_initial_certificate(config, workspace, issued["portal"], protected_json)
            if "gatewayObo" in issued:
                store_gateway_obo_certificate(config, workspace, issued["gatewayObo"], protected_json)
            pending_path = workspace.state / "public-ingress-pending.json"
            pending = {key: config[key] for key in ("subscriptionId", "azureTenantId", "resourceStem", "resourceGroupNames")}
            pending.update(apimInstanceId=apim_instance_id(config), gatewayPlan=outputs["gatewayPlan"],
                           ingressMonitoringPlan=outputs["ingressMonitoringPlan"])
            protected_json(pending_path, pending)
            gateway = deploy_public_ingress(config, outputs, transport, l7_secret, firewall_root_ca)
            require_appgw_backend_tls_ready(config, transport)
            observe_initial_certificate(config, gateway, issued["tls"])
            final = {**outputs, "gateway": gateway,
                     "endpoints": {**outputs["endpoints"], "public": gateway["publicUrl"]},
                     "certificateBootstrap": {**outputs["certificateBootstrap"], "imageDigest": issued["imageDigest"]},
                     "ingressSecrets": {**outputs["ingressSecrets"], "enabled": True}}
            protected_json(workspace.state / "ingress-bootstrap.json",
                           configure_grpc_endpoint(config, final, transport,
                                                   mutation_recorder=upgrade_journal(config, "ingress-endpoint")))
            receipt = finish_public_dns(config, final, transport)
            protected_json(dns_path, receipt)
            _save_deployment_state(config, final)
            pending_path.unlink(missing_ok=True)
            return final, receipt
    except OperationError as error:
        raise DeploymentError(str(error)) from None


def postprovision(config: dict) -> None:
    """今回のネイティブCore配備を確認し、初回発行と公開入口を仕上げる。"""
    workspace = local_paths(config, ROOT)
    try:
        env = run(["azd", "env", "get-values", "--environment", config["environmentName"], "-o", "json"])
        deployment_name = env.get("CORE_DEPLOYMENT_NAME")
        if not deployment_name:
            raise DeploymentError("CORE_DEPLOYMENT_NAME is missing; deployment outputs were not recorded")
        response = run(["az", "deployment", "sub", "show", "--subscription", config["subscriptionId"],
                        "--name", deployment_name, "-o", "json"])
        outputs = {key: item["value"] for key, item in response["properties"]["outputs"].items()}
        if outputs.get("resourceGroups") != config["resourceGroupNames"]:
            raise DeploymentError("The deployment group list differs from the configured five groups")
        transport = ArmTransport(config)
        if _body_audit_storage(config)["immutabilityMode"] == "Locked":
            if response["properties"].get("provisioningState") != "Succeeded":
                raise DeploymentError("Confirm Core deployment succeeded before locking body evidence")
            lock_body_audit_policy(config, outputs, transport)
        if outputs.get("certificateBootstrap"):
            if response["properties"].get("provisioningState") != "Succeeded":
                raise DeploymentError("The Core foundation deployment did not succeed")
            initial_password = _read_initial_serial_deployment_input(config)
            parameters_path = workspace.checked(workspace.parameters)
            require_serial_storage_permissions(parameters_path)
            generated = json.loads(parameters_path.read_text(encoding="utf-8"))
            entries = generated["parameters"]
            credential = entries["l7IngressSecret"]["value"]
            firewall_root_ca = entries["firewallTlsInspection"]["value"].get("rootCaData", "")
            outputs, dns_receipt = finish_certificate_core(config, outputs, transport, initial_password, credential, firewall_root_ca)
        else:
            _save_deployment_state(config, outputs)
            from .helpers.portal_auth_certificate import collect_initial_certificate
            collect_initial_certificate(config, outputs, transport, protected_json)
            dns_path = workspace.state / "public-dns.json"
            dns_path.unlink(missing_ok=True)
            serial_password = _read_initial_serial_deployment_input(config)
            if serial_password and response.get("properties", {}).get("provisioningState") != "Succeeded":
                raise DeploymentError("The initial Core deployment success could not be verified; retain its pending credential")
            _bind_management_serial_password(config, outputs, transport, serial_password)
            complete_core_network_upgrade(config, transport)
            ingress = configure_grpc_endpoint(config, outputs, transport,
                                              mutation_recorder=upgrade_journal(config, "ingress-endpoint"))
            if ingress["enabled"]:
                protected_json(workspace.state / "ingress-bootstrap.json", ingress)
            dns_receipt = finish_public_dns(config, outputs, transport)
            protected_json(dns_path, dns_receipt)
    finally:
        workspace.parameters.unlink(missing_ok=True)
    print(json.dumps({"deployed": True, "publicUrl": outputs["endpoints"]["public"],
                      "publicDnsStatus": dns_receipt["status"],
                      "apiPublicationStatus": "managed-separately"}))


def main(argv=None) -> int:
    """CLI/hookを振り分ける。validate・読取確認・構築・撤去でAzure変更の有無が異なる。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "capacity", "clear-inputs", "preprovision", "postprovision", "predown", "postdown"))
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        selected = args.config or configuration_path(ROOT)
        source = selected if selected.is_absolute() else ROOT / selected
        validate_parameters = None
        if args.command in ("validate", "preprovision"):
            validate_parameters = lambda config: _validate_parameter_registrations(config, source, args.command)
        config = load_configuration(source, validate_parameters=validate_parameters)
        workspace = local_paths(config, ROOT)
        workspace.checked(source)
        workspace.check_selection(config)
        if args.command == "clear-inputs":
            workspace.parameters.unlink(missing_ok=True)
            print("Generated credential inputs removed. Protected source files are unchanged.")
            return 0
        if args.command != "validate":
            workspace.ensure()
        if args.command == "validate":
            print(json.dumps({"valid": True, "resourceGroups": config["resourceGroupNames"], "apiPublicationStatus": config["apiPublicationStatus"]}))
        elif args.command == "capacity":
            azure_preflight(config)
            transport = ArmTransport(config)
            path = f"/subscriptions/{config['subscriptionId']}/providers/Microsoft.Resources/deployments/{config['resourceStem']}-monitoring?api-version=2022-09-01"
            previous = require_success(transport.request("GET", path), "Read prior monitoring deployment", allowed=(200, 404))
            report = capacity_preflight(config, transport, previous.get("properties", {}).get("outputs", {}))
            print(json.dumps(report))
        elif args.command == "preprovision":
            preprovision(config)
        elif args.command == "postprovision":
            postprovision(config)
        else:
            from .helpers.cleanup import CleanupError, pre_down, post_down
            if not (local_paths(config, ROOT).state / "latest.json").exists():
                raise DeploymentError("No core deployment state is available; recover the exact deployment outputs before cleanup")
            state = json.loads((local_paths(config, ROOT).state / "latest.json").read_text())
            _read_application_infrastructure_checkpoint(config)
            if args.command == "predown":
                # native downもbridgeを評価する。撤去中は秘密なしの入力だけを残す。
                write_arm_parameters(config, parameters(config, "core"))
            operation = pre_down if args.command == "predown" else post_down
            receipt_path = local_paths(config, ROOT).state / ("cleanup-" + args.command + ".json")
            receipt_path.unlink(missing_ok=True)
            try:
                def checkpoint(inventory: list[dict]) -> None:
                    # CAEの削除後は管理RG名をAzureから再取得できないため、
                    # 変更前に確認したIDを保護保存し、中断後の撤去でも引き継ぐ。
                    updated = {**state, "applicationInfrastructure": inventory}
                    protected_json((local_paths(config, ROOT).state / "latest.json"), updated)
                    state["applicationInfrastructure"] = inventory

                options = {"checkpoint": checkpoint} if args.command == "predown" else {}
                receipt = {"subscriptionId": config["subscriptionId"], "azureTenantId": config["azureTenantId"],
                           "apimInstanceId": apim_instance_id(config),
                           "resourceStem": config["resourceStem"], "result": operation(source, state, ArmTransport(config), **options),
                           "observedAtUtc": datetime.now(timezone.utc).isoformat()}
                protected_json(receipt_path, receipt)
                print(json.dumps(receipt, ensure_ascii=False))
            except CleanupError as error:
                raise DeploymentError(f"Cleanup requires attention: {error.code}: {error}") from error
            if args.command == "postdown":
                local_paths(config, ROOT).parameters.unlink(missing_ok=True)
                remove_grpc_secret(config, ROOT)
                if receipt["result"].get("credentialManager", {}).get("status") == "unverified":
                    print("Credential Manager: 公開資源の撤去確認は終了しましたが、Microsoft内部アカウントの解放・"
                          "同名CM再構築は未確認です。同名account競合が起きた場合は、削除・配備の記録を添えて"
                          "Microsoftサポートへ確認してください。", file=sys.stderr)
        return 0
    except (ValueError, OSError, KeyError, DeploymentError, LogPrivacyError) as error:
        # 任意の辞書や外部コマンドのstderrを、そのまま利用者向け出力へ混ぜない。
        message = str(error) if isinstance(error, (DeploymentError, ValueError)) else type(error).__name__
        print("Core operation failed: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

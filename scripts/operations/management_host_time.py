#!/usr/bin/env python3
"""Inspect, apply and verify management PHC time with protected, single-submit receipts."""
from __future__ import annotations

import os
import sys
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re
import stat
import time
import uuid

from deployment.helpers.configuration import load_configuration
from deployment.helpers.management_host_time import (
    COMPUTE_API, GUEST_SCRIPT, ManagementHostTimeError, host_time_script,
    require_management_host_time_core_ready,
)
from deployment.helpers.private_permissions import check_private_permissions
from operations.common import OperationContext, OperationError, poll_delay


GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
TERMINAL = {"Succeeded", "Failed", "Canceled", "TimedOut"}
OUTPUT_LIMIT = 4000


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, '{"status":"stopped","invalidArguments":true}\n')


def utc(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset().total_seconds() != 0:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise OperationError("The protected time evidence has invalid UTC timestamps") from None


def strict_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous JSON keys")
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError("Non-finite JSON values")
    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid)


class InputsGuard:
    def __init__(self, ctx):
        self.ctx = ctx
        self.paths = (ctx.config_path, ctx.state_path)
        self.content = tuple(self.read(path) for path in self.paths)
        self.config, self.state = deepcopy(ctx.config), deepcopy(ctx.state)
        if json.loads(self.content[1]) != ctx.state:
            raise OperationError("Core state changed before the time operation")

    @staticmethod
    def read(path):
        check_private_permissions(path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise OperationError("Protected inputs must remain regular files")
        return path.read_bytes()

    def check(self):
        if (tuple(self.read(path) for path in self.paths) != self.content
                or self.ctx.config != self.config or self.ctx.state != self.state):
            raise OperationError("Selected inputs or Core state changed during the time operation")


def read_private(ctx, path):
    path = ctx.output_path(path)
    if not path.exists():
        return None
    check_private_permissions(path.parent, directory=True)
    check_private_permissions(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise OperationError("The selected time receipt cannot be read safely")
    try:
        return strict_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise OperationError("The selected time receipt is unreadable") from None


def owned_vm(ctx):
    try:
        record = ctx.state["outputs"]["managementVm"]
        # Older Core outputs omit vmId. Freeze the owned live generation in the
        # inspection receipt without upgrading or rewriting that Core record.
        has_recorded_uuid = "vmId" in record
        recorded_uuid = record.get("vmId")
        if has_recorded_uuid and (not isinstance(recorded_uuid, str) or not GUID.fullmatch(recorded_uuid)
                or recorded_uuid == "00000000-0000-0000-0000-000000000000"):
            raise ValueError()
        expected = (f"/subscriptions/{ctx.config['subscriptionId']}/resourceGroups/"
                    f"{ctx.config['resourceGroupNames']['maint']}/providers/Microsoft.Compute/"
                    f"virtualMachines/vm-{ctx.config['resourceStem']}-mgmt")
        if record["id"].casefold() != expected.casefold():
            raise ValueError()
        vm = ctx.verify_resource(expected, COMPUTE_API, "Microsoft.Compute/virtualMachines")
        properties, identity = vm["properties"], vm["identity"]
        image = properties["storageProfile"]["imageReference"]
        selected_image = ctx.config["management"]["imageReference"]
        identity_type = identity.get("type")
        if not isinstance(identity_type, str):
            raise ValueError()
        identity_types = re.split(r"\s*,\s*", identity_type.strip().casefold())
        if (properties.get("provisioningState") != "Succeeded" or vm.get("location") != ctx.config["location"]
                or not GUID.fullmatch(properties.get("vmId", ""))
                or properties["vmId"] == "00000000-0000-0000-0000-000000000000"
                or has_recorded_uuid and properties["vmId"].casefold() != recorded_uuid.casefold()
                or not GUID.fullmatch(identity.get("principalId", ""))
                or identity.get("principalId", "").casefold() != record["principalId"].casefold()
                or identity.get("tenantId", "").casefold() != ctx.config["azureTenantId"].casefold()
                or "systemassigned" not in identity_types
                or image.get("publisher") != "Canonical" or image.get("offer") != "ubuntu-24_04-lts"
                or image.get("sku") != "server"
                or any(image.get(key) != selected_image.get(key) for key in ("publisher", "offer", "sku", "version"))
                or vm.get("tags", {}).get("product") != ctx.config["productName"]
                or vm.get("tags", {}).get("environment") != ctx.config["environmentName"]):
            raise ValueError()
        utc(properties["timeCreated"])
        binding = {"id": expected, "vmUuid": properties["vmId"], "principalId": identity["principalId"],
                   "tenantId": identity["tenantId"], "timeCreated": properties["timeCreated"],
                   "location": vm["location"]}
        return vm, binding
    except (KeyError, TypeError, ValueError):
        raise OperationError("The current management VM generation or image differs from Core state") from None


def tags(ctx):
    return {**ctx.config.get("tags", {}), "product": ctx.config["productName"],
            "environment": ctx.config["environmentName"], "azd-env-name": ctx.config["environmentName"]}


def expected_command(ctx, binding, script, *, temporary=False):
    return {"location": binding["location"],
            "tags": {**tags(ctx), **({"purpose": "management-host-time-read"} if temporary else {})},
            "properties": {"source": {"script": script}, "asyncExecution": False,
                           "timeoutInSeconds": 90 if temporary else 300,
                           "treatFailureAsDeploymentFailure": True}}


def command_settings(body, target, expected):
    if (not isinstance(body, dict) or str(body.get("id", "")).casefold() != target.casefold()
            or str(body.get("type", "")).casefold() != "microsoft.compute/virtualmachines/runcommands"
            or body.get("location") != expected["location"] or body.get("tags") != expected["tags"]):
        raise OperationError("The exact time command ownership differs")
    properties = body.get("properties")
    if not isinstance(properties, dict):
        raise OperationError("The exact time command properties are unreadable")
    source = properties.get("source")
    if (not isinstance(source, dict) or source.get("script") != expected["properties"]["source"]["script"]
            or any(value is not None for key, value in source.items() if key != "script")):
        raise OperationError("The exact time command uses different guest material")
    for key in ("asyncExecution", "timeoutInSeconds", "treatFailureAsDeploymentFailure"):
        value = expected["properties"][key]
        if type(properties.get(key)) is not type(value) or properties[key] != value:
            raise OperationError("The exact time command execution settings differ")
    if any(properties.get(key) is not None for key in
           ("runAsUser", "runAsPassword", "outputBlobUri", "errorBlobUri",
            "outputBlobManagedIdentity", "errorBlobManagedIdentity")):
        raise OperationError("The time command has unexpected execution options")
    if properties.get("parameters") not in (None, []) or properties.get("protectedParameters") not in (None, []):
        raise OperationError("The time command has unexpected arguments")
    return properties


def http(reply, permitted):
    if not isinstance(reply, dict) or type(reply.get("statusCode")) is not int or reply["statusCode"] not in permitted:
        raise OperationError("The selected ARM time response is unreadable or unaccepted")
    return reply


def expanded(ctx, binding, target, *, temporary=None):
    permitted = {binding["id"] + "/runCommands/configure-host-time",
                 binding["id"] + "/runCommands/configure-linux-audit"}
    if temporary is not None:
        if not re.fullmatch(re.escape(binding["id"]) + r"/runCommands/management-host-time-(inspect|verify)-[0-9a-f]{32}", temporary):
            raise OperationError("The recorded temporary time target is invalid")
        permitted.add(temporary)
    if target not in permitted or ctx.transport is None:
        raise OperationError("An exact selected time command is required")
    return ctx.transport.request("GET", ctx._resource_path(target) + "?api-version=" + COMPUTE_API + "&$expand=instanceView")


class ProofSnapshot:
    def __init__(self, binding, vm, command):
        self.responses = {binding["id"]: {"statusCode": 200, "body": vm},
                          binding["id"] + "/runCommands/configure-host-time": {"statusCode": 200, "body": command}}

    def request(self, method, path):
        if method != "GET" or path.split("?", 1)[0] not in self.responses:
            raise OperationError("Unexpected readiness proof access")
        return deepcopy(self.responses[path.split("?", 1)[0]])


def permanent_state(ctx, vm, binding, expected):
    host = binding["id"] + "/runCommands/configure-host-time"
    reply = expanded(ctx, binding, host)
    http(reply, (200, 404))
    body = reply.get("body")
    prepared = False
    failed = False
    if reply["statusCode"] == 200:
        properties = command_settings(body, host, expected)
        state = properties.get("instanceView", {}).get("executionState")
        if state not in TERMINAL:
            raise OperationError("A time command is running or unrecognized")
        failed = state != "Succeeded"
        if not failed:
            try:
                require_management_host_time_core_ready(ctx.config, ProofSnapshot(binding, vm, body))
                prepared = True
            except ManagementHostTimeError:
                raise OperationError("The current host-time success proof is invalid") from None
    audit = binding["id"] + "/runCommands/configure-linux-audit"
    other = expanded(ctx, binding, audit)
    http(other, (200, 404))
    if other["statusCode"] == 200:
        value = other.get("body")
        if (not isinstance(value, dict) or str(value.get("id", "")).casefold() != audit.casefold()
                or str(value.get("type", "")).casefold() != "microsoft.compute/virtualmachines/runcommands"
                or value.get("properties", {}).get("instanceView", {}).get("executionState") not in TERMINAL):
            raise OperationError("A full audit command is running or unrecognized")
    return {"prepared": prepared, "failed": failed, "hostReply": reply, "auditReply": other}


def readonly_script(stage):
    if stage not in ("inspect", "verify"):
        raise OperationError("A fixed read-only time stage is required")
    source = GUEST_SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    if source.count("# BEGIN TIME CONFIG HELPERS\n") != 1 or source.count("# END TIME CONFIG HELPERS") != 1:
        raise OperationError("The shared time read helpers are ambiguous")
    helpers = source.split("# BEGIN TIME CONFIG HELPERS\n", 1)[1].split("# END TIME CONFIG HELPERS", 1)[0]
    entry = r'''
readonly readonly_stage='__STAGE__'
[[ $(id -u) == 0 && $(uname -s) == Linux ]]
readonly_service=$(select_time_service "$(systemctl show chrony.service --property=LoadState --value)" "$(systemctl show chronyd.service --property=LoadState --value)")
command -v chronyd >/dev/null
command -v chronyc >/dev/null
validate_host_clock /dev/ptp_hyperv /sys/class/ptp
chronyd -p -f /etc/chrony/chrony.conf >/dev/null
readonly_check=$(time_configuration check readonly /etc/chrony/chrony.conf /etc/chrony/conf.d /etc/chrony/sources.d /run/chrony-dhcp /etc/systemd/timesyncd.conf.d/90-core-platform.conf /run/core-management-time)
systemctl is-active --quiet "$readonly_service"
readonly_sources=$(chronyc -c sources)
readonly_tracking=$(chronyc tracking)
if [[ $readonly_stage == verify ]]; then verify_host_time "$readonly_sources" "$readonly_tracking"; fi
export CORE_TIME_STAGE="$readonly_stage" CORE_TIME_SERVICE="$readonly_service" CORE_TIME_CHECK="$readonly_check" CORE_TIME_SOURCES="$readonly_sources" CORE_TIME_TRACKING="$readonly_tracking"
exec python3 -B - <<'CORE_TIME_READ'
import csv, hashlib, json, math, os, re, stat
from datetime import datetime, timezone
from pathlib import Path
def main():
    check=json.loads(os.environ['CORE_TIME_CHECK'])
    if check.get('configurationKnown') is not True:
        raise ValueError()
    rows=list(csv.reader(os.environ['CORE_TIME_SOURCES'].splitlines()))
    tracking=os.environ['CORE_TIME_TRACKING']
    selected=len(rows)==1 and rows[0][:3]==['#','*','AZUR']
    normal=bool(re.search(r'^Leap status\s*:\s*Normal\s*$',tracking,re.MULTILINE))
    reference=bool(re.search(r'^Reference ID\s*:\s*415A5552\s+\(AZUR\)\s*$',tracking,re.MULTILINE))
    match=re.search(r'^System time\s*:\s*([+-]?[0-9.]+(?:[eE][+-]?[0-9]+)?)\s+seconds\s+(?:fast|slow)',tracking,re.MULTILINE)
    correction=abs(float(match.group(1))) if match else None
    valid=(selected and normal and reference and correction is not None and math.isfinite(correction)
           and correction<=0.1 and check.get('hostConfigurationCurrent') is True)
    if os.environ['CORE_TIME_STAGE']=='verify' and not valid:
        raise ValueError()
    fingerprints={}
    for alias,path in [('ssh','/etc/ssh/sshd_config.d/00-core-access.conf'),('rsyslog','/etc/rsyslog.d/00-core-command-redaction.conf')]:
        path=Path(path)
        if not path.exists() and not path.is_symlink():
            fingerprints[alias]={'present':False}
            continue
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size>65536:
            raise ValueError()
        if any(parent.is_symlink() for parent in path.parents):
            raise ValueError()
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            actual=os.fstat(stream.fileno())
            if (actual.st_dev,actual.st_ino)!=(info.st_dev,info.st_ino):
                raise ValueError()
            data=stream.read(65537)
        if len(data)>65536:
            raise ValueError()
        fingerprints[alias]={'present':True,'permissions':stat.S_IMODE(info.st_mode),'sha256':hashlib.sha256(data).hexdigest()}
    result={'status':'observed','stage':os.environ['CORE_TIME_STAGE'],'observedAtUtc':datetime.now(timezone.utc).isoformat(),
            'configurationKnown':True,'hostConfigurationCurrent':check['hostConfigurationCurrent'],'phcVerified':True,
            'timeService':os.environ['CORE_TIME_SERVICE'],'currentTimeValid':valid,
            'phcSelected':selected,'leapNormal':normal,'referenceVerified':reference,
            'systemCorrectionSeconds':correction,'fingerprints':fingerprints}
    text=json.dumps(result,separators=(',',':'))
    if len(text.encode())>=4000:
        raise ValueError()
    print(text)
try:
    main()
except BaseException:
    print(json.dumps({'status':'failed','stage':os.environ.get('CORE_TIME_STAGE','read')}))
    raise SystemExit(1) from None
CORE_TIME_READ
'''.replace("__STAGE__", stage)
    return ("#!/usr/bin/env bash\nset -Eeuo pipefail\nset +x\nexport PYTHONDONTWRITEBYTECODE=1\nexec 3>&1\n"
            "trap 'printf \"{\\\"status\\\":\\\"failed\\\",\\\"readOnly\\\":true}\\n\" >&3' ERR\n" + helpers + entry)


def observed_proof(properties, binding, stage, requested):
    view = properties.get("instanceView")
    if (properties.get("provisioningState") != "Succeeded" or not isinstance(view, dict)
            or view.get("executionState") != "Succeeded" or type(view.get("exitCode")) is not int
            or view["exitCode"] != 0 or view.get("error") not in (None, "")):
        raise OperationError("The read-only time command did not succeed")
    if not utc(binding["timeCreated"]) <= utc(requested) <= utc(view.get("startTime")) <= utc(view.get("endTime")):
        raise OperationError("The time command belongs to another VM or operation generation")
    output = view.get("output")
    if not isinstance(output, str) or len(output.encode("utf-8")) >= OUTPUT_LIMIT or len(output.splitlines()) != 1:
        raise OperationError("The read-only time output is ambiguous or truncated")
    try:
        proof = strict_json(output)
    except ValueError:
        raise OperationError("The read-only time output is unreadable") from None
    if (not isinstance(proof, dict) or proof.get("status") != "observed" or proof.get("stage") != stage
            or proof.get("configurationKnown") is not True or proof.get("phcVerified") is not True
            or proof.get("timeService") not in ("chrony.service", "chronyd.service")
            or set(proof.get("fingerprints", {})) != {"ssh", "rsyslog"}
            or stage == "verify" and proof.get("currentTimeValid") is not True):
        raise OperationError("The current guest time observations are incomplete")
    correction = proof.get("systemCorrectionSeconds")
    if correction is not None and (type(correction) not in (int, float) or not math.isfinite(correction) or correction < 0):
        raise OperationError("The observed clock correction is invalid")
    if stage == "verify" and (proof.get("hostConfigurationCurrent") is not True or correction is None or correction > 0.1
            or any(proof.get(key) is not True for key in ("phcSelected", "leapNormal", "referenceVerified"))):
        raise OperationError("The current clock correction exceeds the accepted limit")
    utc(proof.get("observedAtUtc"))
    for fingerprint in proof["fingerprints"].values():
        if fingerprint == {"present": False}:
            continue
        if (set(fingerprint) != {"present", "permissions", "sha256"} or fingerprint["present"] is not True
                or type(fingerprint["permissions"]) is not int or not 0 <= fingerprint["permissions"] <= 0o7777
                or not re.fullmatch(r"[0-9a-f]{64}", fingerprint.get("sha256", ""))):
            raise OperationError("The protected audit configuration fingerprints are incomplete")
    return proof


def inspection_baseline(ctx, directory, binding):
    baseline = read_private(ctx, directory / "inspect-result.json")
    receipt = read_private(ctx, directory / "inspect-receipt.json")
    reply = read_private(ctx, directory / "inspect-observed-command.json")
    expected = expected_command(ctx, binding, readonly_script("inspect"), temporary=True)
    if (not isinstance(baseline, dict) or baseline.get("binding") != binding or baseline.get("expected") != expected
            or not isinstance(receipt, dict) or receipt.get("version") != 1 or receipt.get("status") != "verified"
            or receipt.get("stage") != "inspect" or receipt.get("binding") != binding or receipt.get("expected") != expected
            or not re.fullmatch(re.escape(binding["id"]) + r"/runCommands/management-host-time-inspect-[0-9a-f]{32}", receipt.get("commandId", ""))):
        raise OperationError("A complete owned inspection receipt is required in this evidence directory")
    properties = command_settings(http(reply, (200,)).get("body"), receipt["commandId"], expected)
    proof = observed_proof(properties, binding, "inspect", receipt["requestedAt"])
    if baseline.get("proof") != proof:
        raise OperationError("The protected inspection proof differs from its original command")
    return proof


def perform(ctx, stage, evidence_dir, *, execute=False, sleep=time.sleep, clock=time.monotonic,
            now=lambda: datetime.now(timezone.utc), timeout=360):
    if stage not in ("inspect", "apply", "verify"):
        raise OperationError("Select an explicit time operation stage")
    directory = ctx.output_path(Path(evidence_dir))
    guard = InputsGuard(ctx)
    submitted = False
    with ctx.locked():
        guard.check()
        if load_configuration(ctx.config_path) != ctx.config:
            raise OperationError("Selected configuration changed before the operation lock")
        vm, binding = owned_vm(ctx)
        apply_expected = expected_command(ctx, binding, host_time_script())
        receipt_path = directory / (stage + "-receipt.json")
        receipt = read_private(ctx, receipt_path)
        try:
            if stage in ("apply", "verify"):
                inspect_receipt = read_private(ctx, directory / "inspect-receipt.json")
                if inspect_receipt is not None and (not isinstance(inspect_receipt, dict)
                        or inspect_receipt.get("binding") != binding):
                    raise OperationError("The immutable inspection belongs to another VM generation")
                if execute and (stage == "verify" or "vmId" not in ctx.state["outputs"]["managementVm"]):
                    inspection_baseline(ctx, directory, binding)
            if receipt is None:
                current = permanent_state(ctx, vm, binding, apply_expected)
                ctx.save_private(directory / (stage + "-permanent-read.json"), current, overwrite=True)
                if stage == "apply" and current["prepared"]:
                    result = {"stage": stage, "status": "already-configured", "applied": False,
                              "currentClockMeasured": False, "inputsUnchanged": True}
                    ctx.save_private(directory / "apply-result.json", {"binding": binding, **result}, overwrite=True)
                    return result
                plan = {"stage": stage, "binding": binding, "expected": apply_expected if stage == "apply" else
                        expected_command(ctx, binding, readonly_script(stage), temporary=True),
                        "wouldWrite": True, "wouldUpdateVmParent": False, "wouldUpdateCoreState": False,
                        "requiresInspection": stage == "apply", "previousCommandFailed": current["failed"]}
                ctx.save_private(directory / (stage + "-plan.json"), plan, overwrite=True)
                if not execute:
                    return {"stage": stage, "status": "planned", "wouldWrite": True, "inputsUnchanged": True}
                if stage == "apply":
                    if current["failed"]:
                        raise OperationError("Review the existing failed time command before another submission")
                    inspection_baseline(ctx, directory, binding)
                    target = binding["id"] + "/runCommands/configure-host-time"
                else:
                    if stage == "verify" and not current["prepared"]:
                        raise OperationError("A current canonical host-time preparation is required before verification")
                    target = binding["id"] + "/runCommands/management-host-time-" + stage + "-" + uuid.uuid4().hex
                    absent = expanded(ctx, binding, target, temporary=target)
                    http(absent, (404,))
                receipt = {"version": 1, "stage": stage, "binding": binding, "commandId": target,
                           "expected": plan["expected"], "requestedAt": now().isoformat(),
                           "status": "submission-requested", "temporaryCommandRetained": stage != "apply"}
                guard.check()
                ctx.save_private(receipt_path, receipt)
                guard.check()
                try:
                    submitted = True
                    reply = ctx.request("PUT", target, COMPUTE_API, receipt["expected"])
                except Exception as error:
                    ctx.save_private(directory / (stage + "-put-response.json"),
                                     {"transportException": type(error).__name__, "detail": str(error)})
                    raise OperationError("Time submission is unknown; inspect its recorded target without resubmitting") from None
                ctx.save_private(directory / (stage + "-put-response.json"), reply)
                http(reply, (200, 201, 202))
            else:
                if (not isinstance(receipt, dict) or receipt.get("version") != 1 or receipt.get("stage") != stage
                        or receipt.get("binding") != binding
                        or receipt.get("expected") != (apply_expected if stage == "apply" else
                            expected_command(ctx, binding, readonly_script(stage), temporary=True))):
                    raise OperationError("The existing receipt differs; use its recorded target for review")
                if not execute:
                    return {"stage": stage, "status": "recorded", "wouldResubmit": False, "inputsUnchanged": True}
            target = receipt["commandId"]
            if stage == "apply" and target != binding["id"] + "/runCommands/configure-host-time":
                raise OperationError("The recorded apply target differs")
            if stage != "apply" and not re.fullmatch(re.escape(binding["id"]) +
                    r"/runCommands/management-host-time-" + stage + r"-[0-9a-f]{32}", target):
                raise OperationError("The recorded read-only target belongs to another stage")
            deadline = clock() + timeout
            while clock() < deadline:
                guard.check()
                reply = expanded(ctx, binding, target, temporary=target if stage != "apply" else None)
                ctx.save_private(directory / (stage + "-observed-command.json"), reply, overwrite=True)
                http(reply, (200,))
                properties = command_settings(reply.get("body"), target, receipt["expected"])
                view = properties.get("instanceView", {})
                if view.get("executionState") in ("Failed", "Canceled", "TimedOut"):
                    raise OperationError("The recorded command failed; retain its protected evidence")
                if view.get("executionState") == "Succeeded":
                    after_vm, after = owned_vm(ctx)
                    if after != binding:
                        raise OperationError("The management VM changed during the time command")
                    if stage == "apply":
                        if utc(view.get("startTime")) < utc(receipt["requestedAt"]):
                            raise OperationError("The time result predates this submission")
                        try:
                            require_management_host_time_core_ready(ctx.config, ProofSnapshot(binding, after_vm, reply["body"]))
                        except ManagementHostTimeError:
                            raise OperationError("The canonical time application proof is invalid") from None
                        proof = {"hostTimePrepared": True, "currentClockMeasured": False}
                    else:
                        proof = observed_proof(properties, binding, stage, receipt["requestedAt"])
                        if stage == "verify":
                            baseline = inspection_baseline(ctx, directory, binding)
                            if baseline["fingerprints"] != proof["fingerprints"]:
                                raise OperationError("SSH or rsyslog configuration changed across the time operation")
                    guard.check()
                    ctx.save_private(directory / (stage + "-result.json"),
                                     {"binding": binding, "expected": receipt["expected"], "proof": proof}, overwrite=True)
                    receipt["status"] = "verified"
                    ctx.save_private(receipt_path, receipt, overwrite=True)
                    return {"stage": stage, "status": "verified", "inputsUnchanged": True,
                            "applied": stage == "apply" and submitted, "guestReadCreated": stage != "apply" and submitted,
                            "currentClockMeasured": stage != "apply" and submitted,
                            "recordedObservationVerified": stage != "apply"}
                if view.get("executionState") not in ("Pending", "Running") or properties.get("provisioningState") in ("Failed", "Canceled"):
                    raise OperationError("The recorded command state is unrecognized")
                delay = poll_delay(reply, 5)
                if delay > deadline - clock():
                    break
                sleep(delay)
            raise OperationError("The recorded command is pending; no additional submission was sent")
        finally:
            guard.check()


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--runtime-root")
    parser.add_argument("--stage", required=True, choices=("inspect", "apply", "verify"))
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        ctx = OperationContext.load(args.config, runtime_root=args.runtime_root)
        result = perform(ctx, args.stage, args.evidence_dir, execute=args.execute)
    except Exception:
        print(json.dumps({"stage": args.stage, "status": "stopped", "requiresReview": True}))
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

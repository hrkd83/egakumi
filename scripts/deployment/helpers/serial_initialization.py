"""Original-creation provenance and new-VM-only Serial initialization.

Only protected local state is written. Existing VMs without original creation
provenance never open credentials. Ambiguous dispatches recover without another
password write; completed generations preserve manual password changes.
"""
from __future__ import annotations

import json
import secrets
import time

from . import serial_credentials as credential

SOURCE = "managed-serial-initialization-v1"
FILE = "management-serial-initialization.json"
IDENTITY_FIELDS = {"resourceId", "vmId", "principalId", "tenantId", "adminUsername", "credentialGeneration", "source"}
PROOF_FIELDS = IDENTITY_FIELDS | {"schemaVersion", "status", "passwordWriteRequested", "verificationAttempted", "pamAuthenticated", "sshVerified"}
FIELDS = {"schemaVersion", "source", "targetVmResourceId", "adminUsername", "credentialGeneration", "coreMode", "coreDeploymentId", "status", "target", "dispatchAttempted", "initialVmAbsenceVerified"}


def journal(config, workspace):
    path = workspace.checked(workspace.state / FILE)
    if not path.exists() and not path.is_symlink(): return None
    payload, _ = credential._read_payload(path)
    value = json.loads(payload)
    owner, username = credential._owner(config)
    if (not isinstance(value, dict) or set(value) != FIELDS or value.get("schemaVersion") != 1 or value.get("source") != SOURCE
            or value.get("targetVmResourceId") != owner or value.get("adminUsername") != username
            or not isinstance(value.get("credentialGeneration"), str) or not credential._UUID.fullmatch(value["credentialGeneration"])
            or value.get("coreMode") not in {"direct", "native"} or value.get("initialVmAbsenceVerified") is not True
            or value.get("status") not in {"core-requested", "target-confirmed", "dispatch-requested", "complete"}
            or type(value.get("dispatchAttempted")) is not bool
            or value.get("coreDeploymentId") is not None and not isinstance(value["coreDeploymentId"], str)
            or value.get("target") is not None and (not isinstance(value["target"], dict) or set(value["target"]) != IDENTITY_FIELDS)):
        raise credential.SerialCredentialError("The original Serial creation provenance could not be verified")
    return value


def save(workspace, value, writer):
    writer(workspace.checked(workspace.state / FILE), value)


def record_core_request(config, workspace, password, mode, writer):
    if not password: return
    generation = credential.prepared_initial_serial_generation(config, workspace, password)
    previous = journal(config, workspace)
    if previous is not None and previous["credentialGeneration"] == generation:
        return  # Preserve the original request and any bound target on recovery.
    if previous is not None:
        writer(workspace.checked(workspace.state / ("management-serial-initialization-" + previous["credentialGeneration"] + ".json")), previous)
    owner, username = credential._owner(config)
    deployment = (f"/subscriptions/{config['subscriptionId']}/providers/Microsoft.Resources/deployments/{config['resourceStem']}-core"
                  if mode == "direct" else None)
    save(workspace, {"schemaVersion": 1, "source": SOURCE, "targetVmResourceId": owner, "adminUsername": username,
                    "credentialGeneration": generation, "coreMode": mode, "coreDeploymentId": deployment,
                    "status": "core-requested", "target": None, "dispatchAttempted": False, "initialVmAbsenceVerified": True}, writer)


def verify_target(config, value, vm, creation):
    credential._owned_vm(config, vm)
    properties = vm.get("properties") or {}
    identity = vm.get("identity") or {}
    profile = properties.get("osProfile") or {}
    if (not isinstance(creation, dict) or set(creation) != {"generation", "coreDeploymentId", "managementVm"}
            or creation.get("generation") != value["credentialGeneration"] or not isinstance(creation.get("managementVm"), dict)
            or not isinstance(creation.get("coreDeploymentId"), str)
            or not creation["coreDeploymentId"].casefold().startswith(f"/subscriptions/{config['subscriptionId']}/providers/Microsoft.Resources/deployments/".casefold())
            or value["coreDeploymentId"] is not None and value["coreDeploymentId"].casefold() != creation["coreDeploymentId"].casefold()
            or properties.get("provisioningState") != "Succeeded" or profile.get("adminUsername") != value["adminUsername"]
            or profile.get("linuxConfiguration", {}).get("disablePasswordAuthentication") is not True):
        raise credential.SerialCredentialError("The original Core creation and VM could not be verified; retain pending credentials")
    output = creation["managementVm"]
    expected = {"resourceId": value["targetVmResourceId"], "vmId": str(properties.get("vmId", "")).lower(),
                "principalId": str(identity.get("principalId", "")).lower(), "tenantId": str(identity.get("tenantId", "")).lower(),
                "adminUsername": value["adminUsername"], "credentialGeneration": value["credentialGeneration"], "source": SOURCE}
    if (str(output.get("id", "")).casefold() != expected["resourceId"].casefold()
            or any(not credential._UUID.fullmatch(expected[k]) for k in ("vmId", "principalId", "tenantId"))
            or any(str(output.get(k, "")).lower() != expected[k] for k in ("vmId", "principalId", "tenantId"))
            or value["target"] is not None and value["target"] != expected):
        raise credential.SerialCredentialError("The original management VM generation could not be verified")
    return expected


def confirm_creation(config, workspace, vm, creation, writer):
    value = journal(config, workspace)
    if value is None:
        raise credential.SerialCredentialError("Original initial-creation provenance is missing; no password action is permitted")
    expected = verify_target(config, value, vm, creation)
    if value["target"] is None:
        value = {**value, "target": expected, "coreDeploymentId": creation["coreDeploymentId"], "status": "target-confirmed"}
        save(workspace, value, writer)  # Pin UUID/SAMI/tenant BEFORE credential open or guest dispatch.
    return value


def saved_password(config, workspace, value):
    if value.get("target") is None:
        raise credential.SerialCredentialError("The original VM target must be confirmed before credential access")
    path = workspace.checked(workspace.secrets / credential._FILE_NAME)
    credential._owner_only(path.parent, directory=True)
    stored, _ = credential._read(path, config)
    if (stored["generation"] != value["credentialGeneration"]
            or stored["boundVmId"] not in (None, value["target"]["vmId"])):
        raise credential.SerialCredentialError("The original saved credential generation differs")
    return stored["password"]


def recover_password(config, workspace, vm, read_core, writer):
    value = journal(config, workspace)
    if value is None or value["status"] == "complete": return ""
    response = read_core(value)
    body = response.get("body")
    props = body.get("properties") if isinstance(body, dict) else None
    tags = body.get("tags") if isinstance(body, dict) else None
    owner_tags = {"azd-env-name": config["environmentName"], "azd-project-name": "ai-workforce-core", "azd-layer-name": ""}
    if (response.get("statusCode") != 200 or not isinstance(props, dict) or props.get("provisioningState") not in {"Succeeded", "Failed", "Canceled"}
            or not isinstance(tags, dict) or any(tags.get(k) != v for k, v in owner_tags.items())
            or props.get("parameters", {}).get("managementInitialSerialGeneration", {}).get("value") != value["credentialGeneration"]):
        raise credential.SerialCredentialError("The original Core request must be verified before credential recovery")
    complete_core = props["provisioningState"] == "Succeeded"
    creation = (props.get("outputs", {}).get("managementInitialSerialCreation", {}).get("value")
                if complete_core else response.get("managementCreation"))
    if not isinstance(creation, dict) or str(body.get("id", "")).casefold() != str(creation.get("coreDeploymentId", "")).casefold():
        raise credential.SerialCredentialError("The original Core deployment identity differs")
    value = confirm_creation(config, workspace, vm, creation, writer)
    return saved_password(config, workspace, value) if complete_core else ""


def completion(response, expected):
    if response.get("statusCode") != 200: return None
    properties = response.get("body", {}).get("properties", {})
    instance = properties.get("instanceView", {})
    if instance.get("executionState") != "Succeeded" or instance.get("exitCode") != 0: return None
    try: proof = json.loads(instance.get("output", ""))
    except (ValueError, TypeError): return None
    if (not isinstance(proof, dict) or set(proof) != PROOF_FIELDS or proof.get("schemaVersion") != 1
            or any(proof.get(k) != expected[k] for k in IDENTITY_FIELDS)
            or proof.get("status") != "complete"
            or not all(proof.get(k) is True for k in ("passwordWriteRequested", "verificationAttempted", "pamAuthenticated", "sshVerified"))): return None
    return proof


def initialize_guest(config, workspace, value, password, transport, template, writer):
    target = value.get("target")
    if not isinstance(target, dict): raise credential.SerialCredentialError("Initial VM target is missing")
    if not secrets.compare_digest(saved_password(config, workspace, value), password):
        raise credential.SerialCredentialError("The supplied initial credential generation differs")
    generation = value["credentialGeneration"]
    command = value["targetVmResourceId"] + "/runCommands/initialize-serial-" + generation + "?api-version=2024-11-01&$expand=instanceView"
    with credential._locked(workspace.state):
        current = journal(config, workspace)
        if current is None or current["target"] != target: raise credential.SerialCredentialError("Initial VM target changed")
        before = transport.request("GET", command)
        proof = completion(before, target)
        if proof is not None: return proof
        if before.get("statusCode") not in (200, 404):
            raise credential.SerialCredentialError("The original initialization command could not be read")
        operation = "recover" if current["dispatchAttempted"] else "initial"
        if before.get("statusCode") == 200:
            instance = before.get("body", {}).get("properties", {}).get("instanceView", {})
            if instance.get("executionState") in {"Running", "Pending"}:
                raise credential.SerialCredentialError("The original initialization is still pending; no additional request was sent")
            operation = "recover"
        # This durable flag precedes dispatch. An uncertain request can only
        # invoke the guest's recovery path; that path cannot call chpasswd.
        save(workspace, {**current, "status": "dispatch-requested", "dispatchAttempted": True}, writer)
        deployment = f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['maint']}/providers/Microsoft.Resources/deployments/management-serial-{generation}?api-version=2022-09-01"
        body = {"tags": {"product": config["productName"], "environment": config["environmentName"]}, "properties": {
            "mode": "Incremental", "template": template, "parameters": {k: {"value": v} for k, v in {
                "vmName": value["targetVmResourceId"].rsplit("/", 1)[1], "location": config["location"],
                "tags": {"product": config["productName"], "environment": config["environmentName"]},
                "expected": target, "operation": operation, "initialPassword": password}.items()}}}
        response = transport.request("PUT", deployment, body=body)
        if response.get("statusCode") not in (200, 201, 202):
            raise credential.SerialCredentialError("Initial credential dispatch was not confirmed; retain the original pending generation")
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            response = transport.request("GET", deployment)
            status = response.get("body", {}).get("properties", {}).get("provisioningState") if response.get("statusCode") == 200 else None
            if status in ("Succeeded", "Failed", "Canceled"):
                proof = completion(transport.request("GET", command), target)
                if proof is not None: return proof
                raise credential.SerialCredentialError("Initial credential completion was not verified; no password retry is permitted")
            time.sleep(5)
        raise credential.SerialCredentialError("The original initialization is still pending; retain its pending generation")


def mark_complete(config, workspace, writer):
    value = journal(config, workspace)
    if value is None or value["target"] is None or not value["dispatchAttempted"]:
        raise credential.SerialCredentialError("Initial credential completion provenance is missing")
    save(workspace, {**value, "status": "complete"}, writer)

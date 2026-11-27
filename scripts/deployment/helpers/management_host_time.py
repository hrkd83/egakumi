"""Read the Core-managed VM's last PHC preparation result before networking updates."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path


COMPUTE_API = "2024-07-01"
GUEST_SCRIPT = Path(__file__).resolve().parents[3] / "infra/guest/configure-audit.sh"


class ManagementHostTimeError(RuntimeError):
    """Stop before networking writes without disclosing environment values."""


def _response(value):
    if not isinstance(value, dict) or type(value.get("statusCode")) is not int:
        raise ManagementHostTimeError("The management host-time response could not be verified")
    return value


def _utc_time(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() != timedelta(0):
            raise ValueError()
    except ValueError:
        raise ManagementHostTimeError("The management host-time preparation timestamps are invalid") from None
    return result


def _owned(body, target, kind):
    if (not isinstance(body, dict)
            or str(body.get("id", "")).casefold() != target.casefold()
            or str(body.get("type", "")).casefold() != kind.casefold()
            or not isinstance(body.get("properties"), dict)
            or body["properties"].get("provisioningState") != "Succeeded"):
        raise ManagementHostTimeError("The management host-time target identity or state is invalid")
    return body["properties"]


def require_management_host_time_core_ready(config, transport):
    """Check the Core-owned Linux audit command from the current VM instance."""
    target = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/"
              f"{config['resourceGroupNames']['maint']}/providers/Microsoft.Compute/"
              f"virtualMachines/vm-{config['resourceStem']}-mgmt")
    response = _response(transport.request("GET", target + "?api-version=" + COMPUTE_API))
    if response.get("statusCode") == 404:
        return {"newManagementVm": True, "hostTimePreparationRequired": False}
    if response.get("statusCode") != 200:
        raise ManagementHostTimeError("The management VM could not be inspected before network changes")
    body = response.get("body")
    vm_properties = _owned(body, target, "Microsoft.Compute/virtualMachines")
    tags = body.get("tags")
    if (not isinstance(tags, dict) or tags.get("product") != config["productName"]
            or tags.get("environment") != config["environmentName"]):
        raise ManagementHostTimeError("The management VM ownership differs from the selected construction")

    command = target + "/runCommands/configure-linux-audit"
    response = _response(transport.request("GET", command + "?api-version=" + COMPUTE_API + "&$expand=instanceView"))
    if response.get("statusCode") != 200:
        raise ManagementHostTimeError("The Core Linux audit command could not be inspected")
    properties = _owned(response.get("body"), command, "Microsoft.Compute/virtualMachines/runCommands")
    source = properties.get("source")
    script = source.get("script") if isinstance(source, dict) else None
    expected = GUEST_SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not isinstance(script, str) or script.replace("\r\n", "\n") != expected:
        raise ManagementHostTimeError("The existing management audit result uses different guest material")
    view = properties.get("instanceView")
    if (not isinstance(view, dict) or view.get("executionState") != "Succeeded"
            or type(view.get("exitCode")) is not int or view["exitCode"] != 0
            or not isinstance(view.get("output"), str)):
        raise ManagementHostTimeError("The management PHC preparation has not completed successfully")
    created = _utc_time(vm_properties.get("timeCreated"))
    started = _utc_time(view.get("startTime"))
    finished = _utc_time(view.get("endTime"))
    if not created <= started <= finished:
        raise ManagementHostTimeError("The PHC preparation result predates the current VM instance or has invalid ordering")
    proofs = []
    for line in view["output"].splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("status") == "configured":
            proofs.append(value)
    if (len(proofs) != 1 or proofs[0].get("timeSource") != "azure-host-phc"
            or proofs[0].get("timeSynchronized") is not True
            or proofs[0].get("timeService") not in ("chrony.service", "chronyd.service")):
        raise ManagementHostTimeError("The managed audit result does not prove Azure PHC synchronization")
    return {"newManagementVm": False, "hostTimePrepared": True, "currentClockMeasured": False}

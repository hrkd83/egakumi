"""Bounded audit reads via the constructed management VM and its own identity.

The caller supplies correlation/time-bounded KQL. This helper does not declare
an audit passed, change infrastructure permissions, install the guest client,
or use the caller's authentication cache on the VM. It retains guest evidence.
Resume by inspecting recorded commands; run() never resubmits a recorded ID.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import time
import traceback

from management_client import validate_inventory
from deployment.helpers.management_client_assets import management_client_inventory
from operations.common import OperationError, OperationContext
from deployment.helpers.private_permissions import check_private_permissions

COMPUTE_API = "2024-07-01"
# The installed tooling did not establish these Compute-specific contracts.
# Do not enable on ARM-general assumptions or an observed ETag alone.
DELETE_IF_MATCH_VERIFIED = False
MAX_RAW_BYTES = 4 * 1024 * 1024
MAX_GZIP_BYTES = 48 * 1024
MAX_BUNDLE_BYTES = 256 * 1024
CHUNK_BYTES = 1536
MARKER = "CORE_PHASE12_AUDIT:"
FIXED_KEYS = ("schemaVersion", "subscriptionId", "azureTenantId", "managementVmId",
              "managementPrincipalId", "workspace", "auditStorage")
TERMINAL = frozenset(("Succeeded", "Failed", "Canceled", "TimedOut"))
HASH = re.compile(r"[0-9a-f]{64}")
NONCE = re.compile(r"[0-9a-f]{32}")
GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class AuditVmError(OperationError):
    """Controlled failure; inspect protected evidence instead of replaying."""


def _fail(message="Audit VM verification failed; inspect protected evidence"):
    raise AuditVmError(message)


def _compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _queries(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        _fail("Supply one to four explicit bounded audit queries")
    names = set()
    result = []
    for entry in value:
        if (not isinstance(entry, dict) or set(entry) != {"name", "query"}
                or not isinstance(entry["name"], str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", entry["name"])
                or entry["name"] in names or not isinstance(entry["query"], str)
                or not entry["query"].strip() or "\0" in entry["query"]
                or len(entry["query"].encode("utf-8")) > 1024 * 1024):
            _fail("Audit queries require unique fixed names and bounded KQL")
        names.add(entry["name"])
        result.append(deepcopy(entry))
    return result


def _tables(value):
    if (not isinstance(value, dict) or any(key in value and value[key] is not None for key in ("error", "partialError"))
            or not isinstance(value.get("tables"), list) or not value["tables"]
            or len(value["tables"]) > 16):
        _fail("Audit query did not return complete tables")
    for table in value["tables"]:
        if (not isinstance(table, dict) or not isinstance(table.get("name"), str)
                or not isinstance(table.get("columns"), list) or not table["columns"]
                or not isinstance(table.get("rows"), list)):
            _fail("Audit query table metadata is incomplete")
        columns = table["columns"]
        if any(not isinstance(col, dict) or not isinstance(col.get("name"), str)
               or not isinstance(col.get("type"), str) for col in columns):
            _fail("Audit query column metadata is incomplete")
        if any(not isinstance(row, list) or len(row) != len(columns) for row in table["rows"]):
            _fail("Audit query rows do not match their columns")
    return value


# Static, reviewed guest source. No caller text is interpolated into executable
# Python/shell. Query/identity inputs are passed only as protected named env data.
GUEST_COMMON = r'''
import base64, contextlib, gzip, hashlib, json, os, re, stat, subprocess, sys
from pathlib import Path
MAX_RAW=4*1024*1024
MAX_GZIP=48*1024
MAX_BUNDLE=256*1024
CHUNK=1536
MARKER="CORE_PHASE12_AUDIT:"
FIXED=("schemaVersion","subscriptionId","azureTenantId","managementVmId",
       "managementPrincipalId","workspace","auditStorage")
def reject():
    raise RuntimeError("Audit guest verification failed")
def compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":"))
def sha(data):
    return hashlib.sha256(data).hexdigest()
def private(path,directory=False):
    path=Path(path)
    for parent in (path,*path.parents):
        info=parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,"st_file_attributes",0)&0x400:
            reject()
    info=path.lstat()
    kind=stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=(0o700 if directory else 0o600):
        reject()
    return info
def read_private(path,limit):
    private(Path(path).parent,True)
    before=private(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,"rb") as stream:
        actual=os.fstat(stream.fileno())
        if (actual.st_dev,actual.st_ino)!=(before.st_dev,before.st_ino):
            reject()
        data=stream.read(limit+1)
        if len(data)>limit:
            reject()
        return data
def write_new(path,data):
    private(Path(path).parent,True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream:
        private(path)
        current=os.fstat(stream.fileno())
        expected=Path(path).lstat()
        if (current.st_dev,current.st_ino)!=(expected.st_dev,expected.st_ino):
            reject()
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
def tables(value):
    if not isinstance(value,dict) or any(key in value and value[key] is not None for key in ("error", "partialError")) or not isinstance(value.get("tables"),list) or not value["tables"] or len(value["tables"])>16:
        reject()
    for table in value["tables"]:
        if not isinstance(table,dict) or not isinstance(table.get("name"),str) or not isinstance(table.get("columns"),list) or not table["columns"] or not isinstance(table.get("rows"),list):
            reject()
        cols=table["columns"]
        if any(not isinstance(col,dict) or not isinstance(col.get("name"),str) or not isinstance(col.get("type"),str) for col in cols):
            reject()
        if any(not isinstance(row,list) or len(row)!=len(cols) for row in table["rows"]):
            reject()
    return value
def data_input():
    encoded=os.environ.pop("CORE_PHASE12_AUDIT_B64")
    if len(encoded)>4*((MAX_BUNDLE+2)//3):
        reject()
    raw=base64.b64decode(encoded,validate=True)
    if len(raw)>MAX_BUNDLE:
        reject()
    value=json.loads(raw)
    if not isinstance(value,dict) or type(value.get("version")) is not int or value.get("version")!=1 or not re.fullmatch("[0-9a-f]{32}",value.get("operationId","")):
        reject()
    return value,sha(raw)
@contextlib.contextmanager
def verified_guest(value,base=Path("/opt/core/maintenance")):
    import fcntl
    if os.name!="posix" or os.geteuid()!=0:
        reject()
    private(base,True)
    lock=base/".prepare.lock"
    previous=private(lock)
    fd=os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
    try:
        actual=os.fstat(fd)
        if (actual.st_dev,actual.st_ino)!=(previous.st_dev,previous.st_ino):
            reject()
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        code=read_private(base/"management_client.py",1024*1024)
        if not re.fullmatch("[0-9a-f]{64}",value.get("clientSha256","")) or sha(code)!=value["clientSha256"]:
            reject()
        # Execute the exact verified source bytes, rather than importing a
        # potentially cached module/pyc. Query payloads never enter this code.
        namespace={"__name__":"core_verified_management_client",
                   "__file__":str(base/"management_client.py")}
        exec(compile(code,namespace["__file__"],"exec"),namespace)
        inventory=namespace["load_inventory"](base/"inventory.json")
        expected=value.get("inventory")
        if not isinstance(expected,dict) or any(inventory.get(key)!=expected.get(key) for key in FIXED):
            reject()
        identity=namespace["VmIdentity"](inventory,namespace["HttpTransport"]())
        identity.verify_vm()
        if identity.compute_vm_id!=value.get("vmUuid"):
            reject()
        yield base,inventory
    finally:
        os.close(fd)
def evidence_root(base,operation,create=False):
    root=base/"evidence"
    if not root.exists():
        if not create:
            reject()
        root.mkdir(mode=0o700)
    private(root,True)
    path=root/("phase12-"+operation)
    return path
'''

GUEST_QUERY = GUEST_COMMON + r'''
def main():
    value,bundle_hash=data_input()
    queries=value.get("queries")
    if not isinstance(queries,list) or not 1<=len(queries)<=4:
        reject()
    names=set()
    for query in queries:
        if not isinstance(query,dict) or set(query)!={"name","query"} or not isinstance(query["name"],str) or not re.fullmatch("[a-z][a-z0-9-]{0,31}",query["name"]) or query["name"] in names or not isinstance(query["query"],str) or not query["query"].strip() or "\0" in query["query"] or len(query["query"].encode())>1024*1024:
            reject()
        names.add(query["name"])
    with verified_guest(value) as (base,inventory):
        run=evidence_root(base,value["operationId"],create=True)
        run.mkdir(mode=0o700,exist_ok=False)
        private(run,True)
        inputs=run/"input"
        inputs.mkdir(mode=0o700)
        outputs=run/"output"
        outputs.mkdir(mode=0o700)
        write_new(run/"owner.json",compact({"version":1,"operationId":value["operationId"],"vmUuid":value["vmUuid"],"bundleSha256":bundle_hash}).encode())
        results={}
        for query in queries:
            name=query["name"]
            path=inputs/(name+".kql")
            write_new(path,query["query"].encode("utf-8"))
            output=outputs/(name+".json")
            completed=subprocess.run([str(base/"venv/bin/python"),"-I","-B",str(base/"management_client.py"),"--inventory",str(base/"inventory.json"),"--output",str(output),"workspace-query","--resource-id",inventory["workspace"]["id"],"--query-file",str(path)],capture_output=True,timeout=90,env={"PYTHONDONTWRITEBYTECODE":"1","PATH":"/usr/bin:/bin","LANG":"C.UTF-8"})
            if len(completed.stdout)>65536 or len(completed.stderr)>65536:
                reject()
            if completed.stderr:
                write_new(outputs/(name+".stderr"),completed.stderr)
            if completed.returncode!=0:
                reject()
            results[name]=tables(json.loads(read_private(output,MAX_RAW)))
            if len(compact(results).encode("utf-8"))>MAX_RAW:
                reject()
        raw=compact(results).encode("utf-8")
        if not 0<len(raw)<=MAX_RAW:
            reject()
        zipped=gzip.compress(raw,mtime=0)
        if not 0<len(zipped)<=MAX_GZIP:
            reject()
        write_new(run/"results.gz",zipped)
        if sha(read_private(run/"results.gz",MAX_GZIP))!=sha(zipped):
            reject()
        manifest={"version":1,"kind":"manifest","operationId":value["operationId"],"vmUuid":value["vmUuid"],"bundleSha256":bundle_hash,"rawBytes":len(raw),"gzipBytes":len(zipped),"rawSha256":sha(raw),"gzipSha256":sha(zipped),"chunkCount":(len(zipped)+CHUNK-1)//CHUNK,"queryNames":[q["name"] for q in queries]}
        encoded=compact(manifest)
        if len(encoded.encode())>=1024:
            reject()
        write_new(run/"manifest.json",encoded.encode())
        print(MARKER+encoded)
if __name__=="__main__":
    try:
        main()
    except BaseException:
        sys.stdout.write("CORE_PHASE12_AUDIT_ERROR\n")
        raise SystemExit(1) from None
'''

GUEST_READER = GUEST_COMMON + r'''
def main():
    value,_=data_input()
    expected=value.get("manifest")
    index=value.get("index")
    if not isinstance(expected,dict) or type(index) is not int or not 0<=index<32:
        reject()
    with verified_guest(value) as (base,inventory):
        run=evidence_root(base,value["operationId"])
        private(run,True)
        manifest=json.loads(read_private(run/"manifest.json",1023))
        owner=json.loads(read_private(run/"owner.json",1024))
        if manifest!=expected or manifest.get("operationId")!=value["operationId"] or manifest.get("vmUuid")!=value["vmUuid"] or owner!={"version":1,"operationId":value["operationId"],"vmUuid":value["vmUuid"],"bundleSha256":manifest.get("bundleSha256")}:
            reject()
        data=read_private(run/"results.gz",MAX_GZIP)
        if len(data)!=manifest.get("gzipBytes") or sha(data)!=manifest.get("gzipSha256") or not index<manifest.get("chunkCount",0):
            reject()
        chunk=data[index*CHUNK:(index+1)*CHUNK]
        answer={"version":1,"kind":"chunk","operationId":value["operationId"],"index":index,"bytes":len(chunk),"sha256":sha(chunk),"data":base64.b64encode(chunk).decode("ascii")}
        encoded=compact(answer)
        if len(encoded.encode())>=3072:
            reject()
        print(MARKER+encoded)
if __name__=="__main__":
    try:
        main()
    except BaseException:
        sys.stdout.write("CORE_PHASE12_AUDIT_ERROR\n")
        raise SystemExit(1) from None
'''


def _script(source):
    # Static here-document; protected payload values never enter shell text.
    return ("#!/bin/sh\nset -eu\nexport PYTHONDONTWRITEBYTECODE=1\n"
            "exec /opt/core/maintenance/venv/bin/python -I -B - <<'CORE_PHASE12_GUEST'\n"
            + source + "\nCORE_PHASE12_GUEST\n")


def _vm(ctx, inventory, expected_uuid=None):
    value = ctx.verify_resource(inventory["managementVmId"], COMPUTE_API,
                                "Microsoft.Compute/virtualMachines")
    identity = value.get("identity") or {}
    if not isinstance(identity, dict):
        _fail()
    properties = value.get("properties")
    if not isinstance(properties, dict):
        _fail()
    actual_uuid = properties.get("vmId")
    if (not isinstance(value.get("location"), str) or not value["location"]
            or not isinstance(actual_uuid, str) or not GUID.fullmatch(actual_uuid)
            or actual_uuid == "00000000-0000-0000-0000-000000000000"
            or str(identity.get("principalId", "")).casefold() != inventory["managementPrincipalId"].casefold()
            or str(identity.get("tenantId", "")).casefold() != inventory["azureTenantId"].casefold()
            or "systemassigned" not in re.split(r"\s*,\s*", str(identity.get("type", "")).casefold())
            or expected_uuid is not None and actual_uuid != expected_uuid):
        _fail("The constructed audit VM generation or identity differs")
    if properties.get("provisioningState") != "Succeeded":
        error = AuditVmError("The constructed audit VM generation or identity differs")
        if properties.get("provisioningState") == "Updating":
            # This tag is emitted only after the complete owned identity and
            # generation guard above passed. Callers may wait, never accept it.
            error._phase12_verified_updating_uuid = actual_uuid
        raise error
    return value


def _owned(command, command_id, expected):
    properties = command.get("properties", command) if isinstance(command, dict) else None
    if (not isinstance(command, dict) or not isinstance(properties, dict)
            or str(command.get("id", "")).casefold() != command_id.casefold()
            or str(command.get("type", "")).casefold() != "microsoft.compute/virtualmachines/runcommands"
            or command.get("location") != expected["location"]
            or command.get("tags") != expected["tags"]
            or properties.get("source") != expected["properties"]["source"]):
        _fail("The exact temporary audit command ownership differs")
    for key in ("asyncExecution", "timeoutInSeconds", "treatFailureAsDeploymentFailure"):
        target = expected["properties"][key]
        if type(properties.get(key)) is not type(target) or properties.get(key) != target:
            _fail("The temporary audit command execution settings differ")
    for key in ("runAsUser", "runAsPassword", "outputBlobUri", "errorBlobUri",
                "outputBlobManagedIdentity", "errorBlobManagedIdentity"):
        if properties.get(key) is not None:
            _fail("The temporary audit command has unexpected execution options")
    if properties.get("parameters") not in (None, []):
        _fail("The temporary audit command has unexpected arguments")
    parameters = properties.get("protectedParameters")
    if parameters is not None:
        if (not isinstance(parameters, list)
                or [v.get("name") if isinstance(v, dict) else None for v in parameters]
                != [v["name"] for v in expected["properties"]["protectedParameters"]]):
            _fail("The temporary audit command protected argument names differ")
    return properties


def _etag(reply):
    values = []
    headers = reply.get("headers") or {}
    if not isinstance(headers, dict):
        _fail("The expanded audit command headers are unreadable")
    for key, value in headers.items():
        if key.casefold() == "etag":
            values.append(value)
    body = reply.get("body")
    if isinstance(body, dict) and body.get("etag") is not None:
        values.append(body["etag"])
    if not values:
        return None
    if (any(not isinstance(v, str) or not re.fullmatch(r'"[!#-~]{1,254}"', v) for v in values)
            or len(set(values)) != 1):
        return None
    return values[0]


def _view(ctx, command_id, expected):
    # Raw owned GET provides terminal view and HTTP ETag in one response.
    # request() already initialized the selected scoped ArmTransport.
    try:
        path = ctx._resource_path(command_id)
        if ctx.transport is None:
            _fail("The selected audit ARM transport is unavailable")
        reply = ctx.transport.request("GET", path+"?api-version="+COMPUTE_API+"&$expand=instanceView")
    except Exception:
        _fail("The expanded audit command response is unknown; inspect its recorded ID")
    if not isinstance(reply, dict):
        _fail("The expanded audit command response is unreadable")
    if reply.get("statusCode") != 200:
        _fail("The exact expanded audit command cannot be read")
    command = reply.get("body")
    properties = _owned(command, command_id, expected)
    view = properties.get("instanceView") or command.get("instanceView") or {}
    if not isinstance(view, dict):
        _fail()
    return properties, view, _etag(reply)


def _envelope(output, kind, limit):
    if not isinstance(output, str) or len(output.encode("utf-8")) > 65536:
        _fail("The audit command output is incomplete")
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0].startswith(MARKER):
        _fail("The audit guest did not return exactly one successful record")
    lines = [lines[0][len(MARKER):]]
    if len(lines[0].encode("utf-8")) >= limit:
        _fail("The bounded audit output marker is missing or truncated")
    try:
        value = json.loads(lines[0])
    except (ValueError, TypeError):
        _fail("The bounded audit output marker is malformed")
    if not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") != 1 or value.get("kind") != kind:
        _fail("The audit output protocol differs")
    return value


def _terminal_delete(ctx, inventory, vm_uuid, command_id, expected, receipt, receipt_path,
                     result, validator, kind, *, sleep, clock):
    # A completed result is not permission to delete a recreated/running command.
    properties, view, current_etag = _view(ctx, command_id, expected)
    if (view.get("executionState") != "Succeeded" or type(view.get("exitCode")) is not int
            or view["exitCode"] != 0 or view.get("error") not in (None, "")
            or receipt.get("terminalETag") is not None and current_etag is not None
                and current_etag != receipt["terminalETag"]):
        _fail("The completed audit command changed before cleanup; it was retained")
    fresh = _envelope(view.get("output"), kind, 1024 if kind == "manifest" else 3072)
    validator(fresh, receipt["bundleSha256"])
    if fresh != result:
        _fail("The completed audit command result changed before cleanup")
    _vm(ctx, inventory, vm_uuid)
    if not DELETE_IF_MATCH_VERIFIED or receipt.get("terminalETag") is None or current_etag is None:
        receipt["cleanupStatus"] = "conditional-delete-unverified"
        ctx.save_private(receipt_path, receipt, overwrite=True)
        return False
    receipt["cleanupRequested"] = True
    ctx.save_private(receipt_path, receipt, overwrite=True)
    try:
        response = ctx.request("DELETE", command_id, COMPUTE_API,
                               headers={"If-Match": current_etag})
    except Exception:
        _fail("Audit command removal is unknown; no deletion was repeated")
    if response.get("statusCode") not in (200, 202, 204):
        _fail("Audit command removal is unknown; no deletion was repeated")
    deadline = clock() + 60
    while clock() < deadline:
        reply = ctx.request("GET", command_id, COMPUTE_API)
        if reply.get("statusCode") == 404:
            receipt["temporaryCommandRemoved"] = True
            ctx.save_private(receipt_path, receipt, overwrite=True)
            return True
        if reply.get("statusCode") != 200:
            _fail("Audit command removal remains unverified")
        _owned(reply.get("body"), command_id, expected)
        sleep(min(5, max(0, deadline - clock())))
    _fail("Audit command removal remains pending; no deletion was repeated")


def _put_exception(error):
    # The common context suppresses display of its original transport exception.
    # Retain both records only in this operation's protected evidence.
    records = []
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        records.append({"class": type(error).__name__, "detail": str(error),
                        "traceback": "".join(traceback.format_exception(
                            type(error), error, error.__traceback__, chain=False))})
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return {"transportException": records[0], "exceptionChain": records[1:]}


def _save_put_evidence(ctx, path, value, receipt_path, receipt):
    # Persist the unmodified response before adopting any acceptance decision.
    try:
        ctx.save_private(path, value)
    except Exception:
        receipt["putEvidenceSaved"] = False
        receipt["putClassification"] = "evidence-save-failed"
        receipt_saved = False
        try:
            ctx.save_private(receipt_path, receipt, overwrite=True)
            receipt_saved = True
        except Exception:
            pass
        error = AuditVmError("Audit PUT evidence could not be saved; no request was repeated")
        error.putEvidenceSaved = False
        error.putReceiptSaved = receipt_saved
        raise error from None
    receipt["putEvidenceSaved"] = True
    try:
        ctx.save_private(receipt_path, receipt, overwrite=True)
    except Exception:
        error = AuditVmError("Audit PUT receipt could not be saved; inspect protected response evidence")
        error.putEvidenceSaved = True
        error.putReceiptSaved = False
        raise error from None


def _execute_once(ctx, inventory, vm, directory, name, source, payload, kind,
                  *, timeout, sleep, clock, progress, validator):
    operation = payload["operationId"]
    if not NONCE.fullmatch(operation) or not re.fullmatch(r"query|chunk-[0-9]{2}", name):
        _fail()
    command_id = inventory["managementVmId"] + "/runCommands/phase12-audit-" + operation + "-" + name
    receipt_path = ctx.output_path(directory / (name + "-command.json"))
    put_path = ctx.output_path(directory / (name + "-put-response.json"))
    if receipt_path.exists() or put_path.exists():
        _fail("An audit command is already recorded; inspect it without resubmitting")
    encoded = _compact(payload).encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        _fail("The audit command protected payload exceeds its bound")
    expected = {"location": vm["location"], "tags": {
        "product": ctx.config["productName"], "environment": ctx.config["environmentName"],
        "purpose": "phase12-audit-read"}, "properties": {
        "source": {"script": _script(source)}, "protectedParameters": [{
            "name": "CORE_PHASE12_AUDIT_B64", "value": base64.b64encode(encoded).decode("ascii")}],
        "asyncExecution": True, "timeoutInSeconds": 600 if name == "query" else 90,
        "treatFailureAsDeploymentFailure": True}}
    preflight = ctx.request("GET", command_id, COMPUTE_API)
    if preflight.get("statusCode") != 404:
        _fail("The new temporary audit command is not confirmed absent")
    receipt = {"version": 1, "commandId": command_id, "status": "submission-requested",
               "expected": expected, "bundleSha256": _sha(encoded), "vmUuid": vm["properties"]["vmId"],
               "temporaryCommandRemoved": False, "cleanupRequested": False,
               "putEvidenceFile": put_path.name, "putEvidenceSaved": False,
               "putResponseKnown": False, "putClassification": "submission-requested"}
    ctx.save_private(receipt_path, receipt)
    progress("audit-query-submitting" if name == "query" else "audit-chunk-submitting")
    try:
        response = ctx.request("PUT", command_id, COMPUTE_API, expected)
    except Exception as error:
        receipt["putClassification"] = "transport-exception"
        _save_put_evidence(ctx, put_path, _put_exception(error), receipt_path, receipt)
        _fail("Audit command submission is unknown; inspect it without resubmitting")
    status = response.get("statusCode") if isinstance(response, dict) else None
    known = type(status) is int and 100 <= status <= 599
    accepted = known and status in (200, 201, 202)
    receipt["putResponseKnown"] = known
    receipt["putClassification"] = (
        "http-accepted" if accepted else "http-not-accepted" if known else "http-response-invalid")
    if known:
        receipt["putHttpStatus"] = status
        headers = response.get("headers", {})
        header_names = ({key.casefold() for key in headers if isinstance(key, str)}
                        if isinstance(headers, dict) else None)
        receipt["putAzureAsyncOperationHeaderPresent"] = (
            "azure-asyncoperation" in header_names if header_names is not None else None)
        receipt["putLocationHeaderPresent"] = (
            "location" in header_names if header_names is not None else None)
    _save_put_evidence(ctx, put_path, response, receipt_path, receipt)
    if not accepted:
        _fail("Audit command PUT was not confirmed accepted; inspect its protected response without resubmitting")
    deadline = clock() + timeout
    while clock() < deadline:
        properties, view, terminal_etag = _view(ctx, command_id, expected)
        state = view.get("executionState")
        if state in TERMINAL:
            receipt["status"] = "terminal"
            receipt["executionState"] = state
            receipt["exitCode"] = view.get("exitCode")
            receipt["terminalETag"] = terminal_etag
            ctx.save_private(receipt_path, receipt, overwrite=True)
            if state != "Succeeded" or type(view.get("exitCode")) is not int or view["exitCode"] != 0 or view.get("error") not in (None, ""):
                _fail("The audit guest command failed; inspect its exact recorded command")
            value = _envelope(view.get("output"), kind, 1024 if kind == "manifest" else 3072)
            validator(value, _sha(encoded))
            _vm(ctx, inventory, vm["properties"]["vmId"])
            # Save data before reclaiming the exact completed control resource.
            ctx.save_private(directory / (name + "-output.json"), value)
            receipt["status"] = "result-saved"
            ctx.save_private(receipt_path, receipt, overwrite=True)
            removed = _terminal_delete(ctx, inventory, vm["properties"]["vmId"], command_id, expected, receipt,
                             receipt_path, value, validator, kind, sleep=sleep, clock=clock)
            return value, _sha(encoded), removed
        if properties.get("provisioningState") in ("Failed", "Canceled"):
            _fail("The audit command provisioning failed; its request was not repeated")
        progress("audit-query-pending" if name == "query" else "audit-chunk-pending")
        sleep(min(5, max(0, deadline-clock())))
    _fail("The audit command remains pending; inspect its recorded ID without resubmitting")


def _manifest(value, operation, vm_uuid, digest, names):
    keys = {"version", "kind", "operationId", "vmUuid", "bundleSha256", "rawBytes",
            "gzipBytes", "rawSha256", "gzipSha256", "chunkCount", "queryNames"}
    if (not isinstance(value, dict) or set(value) != keys
            or type(value["version"]) is not int or value["version"] != 1 or value["kind"] != "manifest"
            or value["operationId"] != operation or value["vmUuid"] != vm_uuid
            or value["bundleSha256"] != digest or value["queryNames"] != names
            or type(value["rawBytes"]) is not int or not 0 < value["rawBytes"] <= MAX_RAW_BYTES
            or type(value["gzipBytes"]) is not int or not 0 < value["gzipBytes"] <= MAX_GZIP_BYTES
            or type(value["chunkCount"]) is not int
            or value["chunkCount"] != (value["gzipBytes"]+CHUNK_BYTES-1)//CHUNK_BYTES
            or not isinstance(value["rawSha256"], str) or not HASH.fullmatch(value["rawSha256"])
            or not isinstance(value["gzipSha256"], str) or not HASH.fullmatch(value["gzipSha256"])):
        _fail("The audit manifest does not match the submitted bounded query")
    return value


def _chunk(value, manifest, index):
    expected = min(CHUNK_BYTES, manifest["gzipBytes"]-index*CHUNK_BYTES)
    if (not isinstance(value, dict) or set(value) != {"version", "kind", "operationId", "index", "bytes", "sha256", "data"}
            or type(value["version"]) is not int or value["version"] != 1 or value["kind"] != "chunk"
            or value["operationId"] != manifest["operationId"]
            or type(value["index"]) is not int or value["index"] != index
            or type(value["bytes"]) is not int or value["bytes"] != expected
            or not isinstance(value["data"], str)
            or len(value["data"]) != 4*((expected+2)//3)):
        _fail("The saved audit chunk is incomplete or belongs to another operation")
    try:
        data = base64.b64decode(value["data"], validate=True)
    except (ValueError, TypeError):
        _fail("The saved audit chunk is malformed")
    if len(data) != expected or not isinstance(value.get("sha256"), str) or _sha(data) != value["sha256"]:
        _fail("The saved audit chunk length differs")
    return data


def _unpack(data, manifest):
    if (type(manifest.get("rawBytes")) is not int or not 0 < manifest["rawBytes"] <= MAX_RAW_BYTES
            or type(manifest.get("gzipBytes")) is not int or not 0 < manifest["gzipBytes"] <= MAX_GZIP_BYTES
            or not isinstance(data, bytes) or len(data) != manifest["gzipBytes"]
            or _sha(data) != manifest["gzipSha256"]):
        _fail("The assembled audit result hash or length differs")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            raw = stream.read(manifest["rawBytes"]+1)
            if len(raw) != manifest["rawBytes"] or stream.read(1):
                _fail("The expanded audit result exceeds its recorded bound")
    except AuditVmError:
        raise
    except (OSError, EOFError, ValueError):
        _fail("The assembled audit result is not complete gzip")
    if len(raw) > MAX_RAW_BYTES or _sha(raw) != manifest["rawSha256"]:
        _fail("The expanded audit result hash differs")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        _fail("The expanded audit result is not valid JSON")
    if not isinstance(value, dict) or list(value) != manifest["queryNames"]:
        _fail("The expanded audit result has different query names")
    for result in value.values():
        _tables(result)
    return raw, value


def _snapshot(ctx):
    paths = {"input": ctx.config_path, "coreState": ctx.state_path,
             "checkpoint": ctx.workspace.state/"gateway-api-source.json"}
    result = {}
    for key, path in paths.items():
        path = ctx.output_path(path)
        check_private_permissions(path)
        result[key] = _sha(path.read_bytes())
    return result


def run(ctx, inventory, queries, *, expected_client_sha256, expected_snapshot, operation_id,
        sleep=time.sleep, clock=time.monotonic, progress=lambda stage: None):
    """Retrieve bounded tables; missing logs require separate audit judgment.

    operation_id is a new independently generated 32-character hex nonce.
    Reusing its recorded directory refuses replay even after unknown submission.
    The protected input/Core/checkpoint snapshot must be the caller's gated one.
    New commands use the repository's unique ID, GET404, shared lock, durable
    intent and one PUT pattern. Unproven conditional deletion retains completed
    controls as cleanup-pending while bounded evidence retrieval continues.
    """
    if (not isinstance(operation_id, str) or not NONCE.fullmatch(operation_id)
            or not isinstance(expected_client_sha256, str) or not HASH.fullmatch(expected_client_sha256)
            or not isinstance(expected_snapshot, dict) or set(expected_snapshot) != {"input", "coreState", "checkpoint"}
            or any(not isinstance(v, str) or not HASH.fullmatch(v) for v in expected_snapshot.values())):
        _fail("Select a new audit operation and verified public source/protected snapshot")
    selected = deepcopy(validate_inventory(deepcopy(inventory)))
    queries = _queries(queries)
    original_config, original_state = deepcopy(ctx.config), deepcopy(ctx.state)
    directory = ctx.output_path(ctx.workspace.root/"evidence"/("phase12-audit-"+operation_id))
    with ctx.locked():
        refreshed = OperationContext.load(ctx.config_path, runtime_root=ctx.workspace.repository)
        if (refreshed.config != original_config or refreshed.state != original_state
                or _snapshot(refreshed) != expected_snapshot):
            _fail("The selected protected inputs/Core/checkpoint changed before audit execution")
        authoritative = management_client_inventory(refreshed.config, refreshed.state)
        if selected != authoritative:
            _fail("The audit inventory differs from the current constructed inventory")
        vm = _vm(ctx, selected)
        payload = {"version":1,"operationId":operation_id,"vmUuid":vm["properties"]["vmId"],
                   "inventory":{key:selected[key] for key in FIXED_KEYS},
                   "clientSha256":expected_client_sha256,"queries":queries}
        names = [query["name"] for query in queries]
        def validate_manifest(value, digest):
            _manifest(value, operation_id, vm["properties"]["vmId"], digest, names)
        manifest, _, query_removed = _execute_once(ctx, selected, vm, directory, "query", GUEST_QUERY,
                                    payload, "manifest", timeout=900, sleep=sleep, clock=clock,
                                    progress=progress, validator=validate_manifest)
        ctx.save_private(directory/"manifest.json", manifest)
        retained = [] if query_removed else [selected["managementVmId"]+"/runCommands/phase12-audit-"+operation_id+"-query"]
        pieces = []
        for index in range(manifest["chunkCount"]):
            fresh = OperationContext.load(ctx.config_path,runtime_root=ctx.workspace.repository)
            if fresh.config != original_config or fresh.state != original_state or _snapshot(fresh) != expected_snapshot:
                _fail("The selected protected inputs/Core/checkpoint changed before another audit command")
            request = {key:deepcopy(value) for key,value in payload.items() if key != "queries"}
            request.update(manifest=manifest,index=index)
            def validate_chunk(value, digest):
                piece = _chunk(value, manifest, index)
                if index == manifest["chunkCount"]-1:
                    _unpack(b"".join(pieces+[piece]), manifest)
            value, _, removed = _execute_once(ctx, selected, vm, directory, f"chunk-{index:02d}", GUEST_READER,
                                    request, "chunk", timeout=180, sleep=sleep, clock=clock,
                                    progress=progress, validator=validate_chunk)
            if not removed:
                retained.append(selected["managementVmId"]+"/runCommands/phase12-audit-"+operation_id+f"-chunk-{index:02d}")
            pieces.append(_chunk(value, manifest, index))
        raw, _ = _unpack(b"".join(pieces), manifest)
        path = ctx.save_private(directory/"audit-results.json", raw.decode("utf-8"))
        if _sha(path.read_bytes()) != manifest["rawSha256"]:
            _fail("The protected audit evidence readback differs")
        _vm(ctx, selected, vm["properties"]["vmId"])
        if _snapshot(OperationContext.load(ctx.config_path,runtime_root=ctx.workspace.repository)) != expected_snapshot:
            _fail("The selected protected inputs/Core/checkpoint changed during audit execution")
        completion = {"version":1,"status":"retrieved","auditVerified":False,
                      "temporaryCommandsRemoved":not retained,"guestEvidenceRetained":True,
                      "cleanupPendingCommands":retained,
                      "operationId":operation_id,"queryNames":manifest["queryNames"]}
        ctx.save_private(directory/"completion.json", completion)
        progress("audit-results-retrieved")
        return {**completion,"evidenceDirectory":directory,"resultFile":path}

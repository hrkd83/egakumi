"""Synthetic ownership, no-replay and bounded audit-result transport checks."""
from contextlib import contextmanager
from copy import deepcopy
import gzip
import io
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from operations import apim_phase12_audit_vm as audit

SUB = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
PRINCIPAL = "33333333-3333-3333-3333-333333333333"
VM_UUID = "44444444-4444-4444-4444-444444444444"
CUSTOMER = "55555555-5555-5555-5555-555555555555"
OPERATION = "a" * 32
SNAPSHOT = {"input":"1"*64,"coreState":"2"*64,"checkpoint":"3"*64}
VM_ID = f"/subscriptions/{SUB}/resourceGroups/synthetic-maint/providers/Microsoft.Compute/virtualMachines/vm-synthetic-mgmt"
INVENTORY = {"schemaVersion": 1, "subscriptionId": SUB, "azureTenantId": TENANT,
             "managementVmId": VM_ID, "managementPrincipalId": PRINCIPAL,
             "workspace": {"id": f"/subscriptions/{SUB}/resourceGroups/synthetic-monitor/providers/Microsoft.OperationalInsights/workspaces/law-synthetic", "customerId": CUSTOMER},
             "auditStorage": [], "applications": [], "jobs": [], "environments": [], "sqlDatabases": []}
QUERIES = [{"name": "native", "query": "print syntheticValue=1"}]
VM = {"id": VM_ID, "type": "Microsoft.Compute/virtualMachines", "location": "synthetic-location",
      "identity": {"type": "SystemAssigned", "principalId": PRINCIPAL, "tenantId": TENANT},
      "properties": {"vmId": VM_UUID, "provisioningState": "Succeeded"}}
TABLE = {"tables": [{"name": "PrimaryResult", "columns": [{"name": "syntheticValue", "type": "long"}], "rows": [[1]]}]}


def protocol(raw, operation=OPERATION, digest="b"*64):
    zipped = gzip.compress(raw, mtime=0)
    manifest = {"version": 1, "kind": "manifest", "operationId": operation, "vmUuid": VM_UUID,
                "bundleSha256": digest, "rawBytes": len(raw), "gzipBytes": len(zipped),
                "rawSha256": audit._sha(raw), "gzipSha256": audit._sha(zipped),
                "chunkCount": (len(zipped)+audit.CHUNK_BYTES-1)//audit.CHUNK_BYTES,
                "queryNames": ["native"]}
    return manifest, zipped


class Clock:
    def __init__(self): self.value = 0
    def __call__(self): return self.value
    def sleep(self, seconds): self.value += seconds


class FakeContext:
    """Only in-memory fake ARM and independently synthetic local evidence."""
    def __init__(self, root):
        self.workspace = SimpleNamespace(root=Path(root),repository=Path(root),state=Path(root)/"state")
        self.config_path = Path(root)/"synthetic-params.json"
        self.state_path = Path(root)/"state/synthetic-core.json"
        self.transport = SimpleNamespace(request=self.expanded_request)
        self.config = {"subscriptionId": SUB, "azureTenantId": TENANT,
                       "resourceGroupNames": {"maint": "synthetic-maint"},
                       "productName": "synthetic-product", "environmentName": "synthetic"}
        self.state = {}
        self.commands = {}
        self.calls = []
        self.vm = deepcopy(VM)
        self.tamper = None
        self.unknown_put = False
        self.unknown_delete = False
        self.preflight_unknown = False
        self.pending = False
        self.truncated = False
        self.vm_drift = False
        self.delete_tamper = False
        self.verified_count = 0
        self.expanded_count = 0
        self.etag_present = True
        self.etag_drift = False
        self.restart_before_cleanup = False
        self.late_output_tamper = False
        self.late_error = False
        self.semantic_tamper = None
        self.request_headers = []
        self.manifest = None
        self.zipped = None
    @contextmanager
    def locked(self): yield
    def output_path(self, path):
        path = Path(path)
        if not path.is_relative_to(self.workspace.root): raise AssertionError("Synthetic evidence boundary escaped")
        return path
    def save_private(self, path, value, overwrite=False):
        path = self.output_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not overwrite: raise audit.AuditVmError("Synthetic output already exists")
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path
    def verify_resource(self, resource_id, api_version, expected_type):
        if (resource_id, api_version, expected_type) != (VM_ID, audit.COMPUTE_API, "Microsoft.Compute/virtualMachines"):
            raise AssertionError("An unrelated synthetic resource was selected")
        self.verified_count += 1
        value = deepcopy(self.vm)
        if self.vm_drift and self.verified_count > 1:
            value["properties"]["vmId"] = "66666666-6666-6666-6666-666666666666"
        return value
    def request(self, method, resource_id, api_version, body=None, headers=None):
        self.calls.append((method, resource_id))
        self.request_headers.append((method, headers))
        if api_version != audit.COMPUTE_API or not resource_id.startswith(VM_ID+"/runCommands/phase12-audit-"):
            raise AssertionError("An unrelated synthetic control resource was used")
        if method == "GET":
            if resource_id not in self.commands:
                return {"statusCode": 503 if self.preflight_unknown else 404}
            value = deepcopy(self.commands[resource_id])
            if self.delete_tamper and self.expanded_count >= 2:
                value["tags"]["purpose"] = "another-synthetic-owner"
            return {"statusCode": 200, "body": value}
        if method == "PUT":
            if self.unknown_put: raise audit.AuditVmError("Synthetic response unknown")
            if resource_id in self.commands: raise AssertionError("Synthetic command replay")
            value = {"id": resource_id, "type": "Microsoft.Compute/virtualMachines/runCommands", **deepcopy(body)}
            payload = json.loads(audit.base64.b64decode(body["properties"]["protectedParameters"][0]["value"]))
            if resource_id.endswith("-query"):
                raw = audit._compact({q["name"]: TABLE for q in payload["queries"]}).encode()
                self.manifest, self.zipped = protocol(raw, payload["operationId"], audit._sha(audit._compact(payload).encode()))
                self.manifest["queryNames"] = [q["name"] for q in payload["queries"]]
                answer = self.manifest
            else:
                index = payload["index"]
                chunk = self.zipped[index*audit.CHUNK_BYTES:(index+1)*audit.CHUNK_BYTES]
                answer = {"version": 1, "kind": "chunk", "operationId": payload["operationId"],
                          "index": index, "bytes": len(chunk), "sha256":audit._sha(chunk), "data": audit.base64.b64encode(chunk).decode()}
            if self.semantic_tamper == "manifest" and answer["kind"] == "manifest":
                answer = {**answer,"operationId":"f"*32}
            if self.semantic_tamper == "chunk" and answer["kind"] == "chunk":
                answer = {**answer,"sha256":"0"*64}
            output = audit.MARKER + audit._compact(answer)
            if self.truncated: output = output[:-9]
            value["properties"].update(provisioningState="Succeeded", instanceView={
                "executionState": "Running" if self.pending else "Succeeded", "exitCode": 0, "output": output})
            self.commands[resource_id] = value
            return {"statusCode": 201, "body": value}
        if method == "DELETE":
            if self.unknown_delete: raise audit.AuditVmError("Synthetic delete response unknown")
            del self.commands[resource_id]
            return {"statusCode": 204}
        raise AssertionError("Unexpected synthetic mutation")
    def _resource_path(self, value):
        if not value.startswith(VM_ID+"/runCommands/phase12-audit-"):
            raise AssertionError("Synthetic raw transport boundary escaped")
        return value
    def expanded_request(self, method, path):
        if method != "GET" or not path.endswith("?api-version="+audit.COMPUTE_API+"&$expand=instanceView"):
            raise AssertionError("Synthetic terminal view was not read atomically")
        self.expanded_count += 1
        reply = self.request("GET", path.split("?",1)[0], audit.COMPUTE_API)
        value = deepcopy(reply["body"])
        if self.tamper == "id": value["id"] += "-other"
        if self.tamper == "type": value["type"] = "Microsoft.Compute/virtualMachines"
        if self.tamper == "tags": value["tags"]["purpose"] = "another-synthetic-owner"
        if self.tamper == "source": value["properties"]["source"]["script"] += "# unowned"
        if self.tamper == "location": value["location"] = "another-synthetic-location"
        if self.tamper == "properties": value["properties"] = []
        if self.tamper == "async": value["properties"]["asyncExecution"] = False
        if self.tamper == "timeout": value["properties"]["timeoutInSeconds"] = 1
        if self.tamper == "run-as": value["properties"]["runAsUser"] = "synthetic-unexpected-user"
        if self.tamper == "arguments": value["properties"]["parameters"] = [{"name":"unexpected","value":"synthetic"}]
        if self.etag_present:
            tag = '"synthetic-etag-2"' if self.etag_drift and self.expanded_count >= 2 else '"synthetic-etag-1"'
            value["etag"] = tag
            reply["headers"] = {"ETag":tag}
        if self.expanded_count >= 2:
            if self.restart_before_cleanup: value["properties"]["instanceView"]["executionState"]="Running"
            if self.late_output_tamper: value["properties"]["instanceView"]["output"]="CORE_PHASE12_AUDIT_ERROR"
            if self.late_error: value["properties"]["instanceView"]["error"]="Synthetic guest error"
        reply["body"] = value
        return reply


class AuditVmTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-audit-vm-")
        self.addCleanup(self.temporary.cleanup)
        self.ctx = FakeContext(self.temporary.name)
        self.clock = Clock()
        supported = patch.object(audit,"DELETE_IF_MATCH_VERIFIED",True)
        supported.start()
        self.addCleanup(supported.stop)
    def run_audit(self):
        with patch.object(audit, "management_client_inventory", return_value=deepcopy(INVENTORY)), \
             patch.object(audit.OperationContext,"load",return_value=self.ctx), \
             patch.object(audit,"_snapshot",return_value=deepcopy(SNAPSHOT)):
            return audit.run(self.ctx, INVENTORY, QUERIES, expected_client_sha256="c"*64,
                             operation_id=OPERATION, expected_snapshot=SNAPSHOT, sleep=self.clock.sleep, clock=self.clock)
    def execute(self, kind="query", timeout=15, queries=None):
        payload = {"version": 1, "operationId": OPERATION, "vmUuid": VM_UUID,
                   "inventory": {key: INVENTORY[key] for key in audit.FIXED_KEYS},
                   "clientSha256": "c"*64, "queries": queries or QUERIES}
        def validator(value,digest):
            audit._manifest(value,OPERATION,VM_UUID,digest,[q["name"] for q in payload["queries"]])
        return audit._execute_once(self.ctx, INVENTORY, VM, Path(self.temporary.name), kind,
                                   audit.GUEST_QUERY, payload, "manifest", timeout=timeout,
                                   sleep=self.clock.sleep, clock=self.clock, progress=lambda stage: None, validator=validator)
    def mutations(self, method): return [resource for action, resource in self.ctx.calls if action == method]
    def test_valid_pipeline_saves_exact_evidence_without_claiming_audit_pass(self):
        result = self.run_audit()
        self.assertFalse(result["auditVerified"])
        self.assertTrue(result["temporaryCommandsRemoved"])
        self.assertTrue(result["guestEvidenceRetained"])
        self.assertEqual(json.loads(result["resultFile"].read_bytes()), {"native": TABLE})
        self.assertEqual(len(self.mutations("PUT")), 2)
        self.assertEqual(self.mutations("PUT"), self.mutations("DELETE"))
        self.assertEqual(self.ctx.commands, {})
    def test_altered_command_identity_type_tags_source_or_location_blocks_delete(self):
        for target in ("id", "type", "tags", "source", "location", "properties", "async", "timeout", "run-as", "arguments"):
            with self.subTest(target=target), tempfile.TemporaryDirectory(prefix="synthetic-tamper-") as root:
                self.ctx = FakeContext(root); self.ctx.tamper = target
                with self.assertRaises(audit.AuditVmError): self.run_audit()
                self.assertEqual(self.mutations("DELETE"), [])
                self.assertEqual(len(self.mutations("PUT")), 1)
    def test_unknown_put_is_durable_and_same_recorded_id_is_never_replayed(self):
        self.ctx.unknown_put = True
        with self.assertRaises(audit.AuditVmError): self.execute()
        with self.assertRaises(audit.AuditVmError): self.execute()
        self.assertEqual(len(self.mutations("PUT")), 1)
        self.assertEqual(self.mutations("DELETE"), [])
        receipt = json.loads((Path(self.temporary.name)/"query-command.json").read_text())
        self.assertEqual(receipt["status"], "submission-requested")
        self.assertFalse(receipt["temporaryCommandRemoved"])
    def test_unknown_preflight_never_submits_a_command(self):
        self.ctx.preflight_unknown = True
        with self.assertRaises(audit.AuditVmError): self.execute()
        self.assertEqual(self.mutations("PUT"), [])

    def test_missing_conditional_delete_proof_retains_controls_and_finishes_readback(self):
        with patch.object(audit,"DELETE_IF_MATCH_VERIFIED",False):
            result = self.run_audit()
        self.assertFalse(result["temporaryCommandsRemoved"])
        self.assertFalse(result["auditVerified"])
        self.assertEqual(len(result["cleanupPendingCommands"]),2)
        self.assertEqual(len(self.ctx.commands),2)
        self.assertEqual(self.mutations("DELETE"),[])
        self.assertEqual(json.loads(result["resultFile"].read_bytes()),{"native":TABLE})

    def test_missing_etag_retains_completed_controls_and_continues_readback(self):
        self.ctx.etag_present = False
        result = self.run_audit()
        self.assertFalse(result["temporaryCommandsRemoved"])
        self.assertEqual(len(result["cleanupPendingCommands"]),2)
        self.assertEqual(self.mutations("DELETE"),[])

    def test_running_recreated_changed_output_or_guest_error_is_never_deleted(self):
        for flag in ("restart_before_cleanup","etag_drift","late_output_tamper","late_error"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory(prefix="synthetic-late-drift-") as root:
                self.ctx=FakeContext(root)
                setattr(self.ctx,flag,True)
                with self.assertRaises(audit.AuditVmError): self.run_audit()
                self.assertEqual(self.mutations("DELETE"),[])

    def test_invalid_manifest_or_chunk_is_checked_before_that_command_cleanup(self):
        for target, expected_deletions in (("manifest",0),("chunk",1)):
            with self.subTest(target=target), tempfile.TemporaryDirectory(prefix="synthetic-late-payload-") as root:
                self.ctx=FakeContext(root); self.ctx.semantic_tamper=target
                with self.assertRaises(audit.AuditVmError): self.run_audit()
                self.assertEqual(len(self.mutations("DELETE")),expected_deletions)
                self.assertEqual(len(self.ctx.commands),1)

    def test_proven_conditional_delete_uses_the_exact_unchanged_etag(self):
        self.run_audit()
        headers=[value for method,value in self.ctx.request_headers if method=="DELETE"]
        self.assertEqual(headers,[{"If-Match":'"synthetic-etag-1"'}]*2)
        self.assertEqual([value for method,value in self.ctx.request_headers if method=="PUT"],[None,None])

    def test_input_core_or_checkpoint_drift_blocks_the_first_or_next_submission(self):
        changed={**SNAPSHOT,"checkpoint":"4"*64}
        with patch.object(audit,"management_client_inventory",return_value=deepcopy(INVENTORY)), \
             patch.object(audit.OperationContext,"load",return_value=self.ctx), \
             patch.object(audit,"_snapshot",return_value=changed):
            with self.assertRaises(audit.AuditVmError):
                audit.run(self.ctx,INVENTORY,QUERIES,expected_client_sha256="c"*64,expected_snapshot=SNAPSHOT,
                          operation_id=OPERATION,sleep=self.clock.sleep,clock=self.clock)
        self.assertEqual(self.mutations("PUT"),[])
        with patch.object(audit,"management_client_inventory",return_value=deepcopy(INVENTORY)), \
             patch.object(audit.OperationContext,"load",return_value=self.ctx), \
             patch.object(audit,"_snapshot",side_effect=[deepcopy(SNAPSHOT),changed]):
            with self.assertRaises(audit.AuditVmError):
                audit.run(self.ctx,INVENTORY,QUERIES,expected_client_sha256="c"*64,expected_snapshot=SNAPSHOT,
                          operation_id=OPERATION,sleep=self.clock.sleep,clock=self.clock)
        self.assertEqual(len(self.mutations("PUT")),1)

    def test_raw_transport_unknown_response_is_wrapped_without_leaking_details(self):
        self.ctx.transport=SimpleNamespace(request=lambda *args:(_ for _ in ()).throw(RuntimeError("SYNTHETIC_DETAIL_MUST_NOT_ESCAPE")))
        with self.assertRaises(audit.AuditVmError) as caught: self.execute()
        self.assertNotIn("SYNTHETIC_DETAIL_MUST_NOT_ESCAPE",str(caught.exception))
        self.assertEqual(self.mutations("DELETE"),[])
    def test_pending_command_is_bounded_retained_and_not_replayed(self):
        self.ctx.pending = True
        with self.assertRaises(audit.AuditVmError): self.execute(timeout=12)
        self.assertGreaterEqual(self.clock(), 12)
        self.assertEqual(len(self.mutations("PUT")), 1)
        self.assertEqual(self.mutations("DELETE"), [])
    def test_unknown_delete_is_not_retried_and_receipt_remains_pending(self):
        self.ctx.unknown_delete = True
        with self.assertRaises(audit.AuditVmError): self.execute()
        with self.assertRaises(audit.AuditVmError): self.execute()
        self.assertEqual(len(self.mutations("DELETE")), 1)
        receipt = json.loads((Path(self.temporary.name)/"query-command.json").read_text())
        self.assertTrue(receipt["cleanupRequested"])
        self.assertFalse(receipt["temporaryCommandRemoved"])
    def test_owner_change_before_delete_prevents_cleanup(self):
        self.ctx.delete_tamper = True
        with self.assertRaises(audit.AuditVmError): self.execute()
        self.assertEqual(self.mutations("DELETE"), [])
    def test_vm_generation_or_principal_change_blocks_publication_result_adoption(self):
        self.ctx.vm_drift = True
        with self.assertRaises(audit.AuditVmError): self.run_audit()
        self.assertEqual(self.mutations("DELETE"), [])
        self.ctx = FakeContext(self.temporary.name)
        self.ctx.vm["identity"]["principalId"] = "99999999-9999-9999-9999-999999999999"
        with self.assertRaises(audit.AuditVmError): self.run_audit()
        self.assertEqual(self.mutations("PUT"), [])
    def test_truncated_terminal_output_is_retained_for_review(self):
        self.ctx.truncated = True
        with self.assertRaises(audit.AuditVmError): self.execute()
        self.assertEqual(self.mutations("DELETE"), [])
    def test_query_text_is_protected_data_and_never_executable_source(self):
        injection = "print syntheticText='SYNTHETIC_CODE_BOUNDARY_4A'; // shell $() ` ;"
        self.execute(queries=[{"name":"native","query":injection}])
        receipt = json.loads((Path(self.temporary.name)/"query-command.json").read_text())
        script = receipt["expected"]["properties"]["source"]["script"]
        self.assertNotIn(injection, script)
        payload = json.loads(audit.base64.b64decode(receipt["expected"]["properties"]["protectedParameters"][0]["value"]))
        self.assertEqual(payload["queries"],[{"name":"native","query":injection}])
        self.assertNotIn(injection, audit._script(audit.GUEST_QUERY))
    def test_inventory_or_invalid_query_is_rejected_before_azure_mutation(self):
        bad = deepcopy(INVENTORY); bad["managementPrincipalId"] = "88888888-8888-8888-8888-888888888888"
        with patch.object(audit, "management_client_inventory", return_value=deepcopy(INVENTORY)), \
             patch.object(audit.OperationContext,"load",return_value=self.ctx), \
             patch.object(audit,"_snapshot",return_value=deepcopy(SNAPSHOT)):
            with self.assertRaises(audit.AuditVmError):
                audit.run(self.ctx, bad, QUERIES, expected_client_sha256="c"*64, operation_id=OPERATION, expected_snapshot=SNAPSHOT)
        for queries in ([], [{"name":"../outside","query":"print 1"}], [{"name":"native","query":""}], QUERIES*2):
            with self.subTest(queries=queries), self.assertRaises(audit.AuditVmError): audit._queries(queries)
        self.assertEqual(self.mutations("PUT"), [])


class AuditVmResultBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.raw = audit._compact({"native": TABLE}).encode()
        self.manifest, self.zipped = protocol(self.raw)
    def test_complete_result_and_empty_rows_are_valid_but_not_an_audit_pass(self):
        self.assertEqual(audit._unpack(self.zipped, self.manifest), (self.raw, {"native": TABLE}))
        empty = deepcopy(TABLE); empty["tables"][0]["rows"] = []
        self.assertEqual(audit._tables(empty), empty)
    def test_query_partial_errors_missing_metadata_and_bad_rows_are_rejected(self):
        for patch_value in ({"error":{"code":"PartialError"}}, {"partialError":{"code":"PartialError"}},
                            {"tables":[]}, {"tables":[{"name":"PrimaryResult","columns":[],"rows":[]}]},
                            {"tables":[{"name":"PrimaryResult","columns":[{"name":"x","type":"long"}],"rows":[[1,2]]}]}):
            with self.subTest(value=patch_value), self.assertRaises(audit.AuditVmError):
                audit._tables({**deepcopy(TABLE), **patch_value})
    def test_manifest_identity_counts_hashes_and_boolean_types_are_rejected(self):
        for key, value in (("operationId","d"*32), ("vmUuid",TENANT), ("bundleSha256","e"*64),
                           ("rawBytes",audit.MAX_RAW_BYTES+1), ("gzipBytes",audit.MAX_GZIP_BYTES+1),
                           ("chunkCount",True), ("version",True), ("gzipSha256","bad"), ("queryNames",["other"])):
            manifest = {**self.manifest, key:value}
            with self.subTest(key=key), self.assertRaises(audit.AuditVmError):
                audit._manifest(manifest, OPERATION, VM_UUID, "b"*64, ["native"])
    def test_chunk_truncation_id_index_size_and_base64_are_rejected(self):
        answer = {"version":1,"kind":"chunk","operationId":OPERATION,"index":0,
                  "bytes":len(self.zipped),"sha256":audit._sha(self.zipped),"data":audit.base64.b64encode(self.zipped).decode()}
        self.assertEqual(audit._chunk(answer, self.manifest, 0), self.zipped)
        for key,value in (("data",answer["data"][:-4]), ("data","!"*len(answer["data"])),
                          ("operationId","d"*32), ("index",1), ("bytes",0), ("version",True), ("kind","manifest")):
            with self.subTest(key=key), self.assertRaises(audit.AuditVmError):
                audit._chunk({**answer,key:value},self.manifest,0)
    def test_compressed_or_raw_hash_mismatch_and_gzip_truncation_are_rejected(self):
        for data,manifest in ((self.zipped+b"x",self.manifest),
                              (self.zipped,{**self.manifest,"gzipSha256":"0"*64}),
                              (self.zipped,{**self.manifest,"rawSha256":"0"*64}),
                              (self.zipped[:-5],{**self.manifest,"gzipBytes":len(self.zipped)-5,"gzipSha256":audit._sha(self.zipped[:-5])})):
            with self.subTest(length=len(data)), self.assertRaises(audit.AuditVmError): audit._unpack(data,manifest)
    def test_gzip_expansion_is_stopped_at_claimed_size_and_hard_cap(self):
        data = gzip.compress(b"x"*100000,mtime=0)
        claimed = {**self.manifest,"gzipBytes":len(data),"gzipSha256":audit._sha(data),"rawBytes":32}
        with self.assertRaises(audit.AuditVmError): audit._unpack(data,claimed)
        claimed["rawBytes"] = audit.MAX_RAW_BYTES+1
        with self.assertRaises(audit.AuditVmError): audit._unpack(data,claimed)
    def test_duplicate_missing_or_oversized_output_marker_is_rejected(self):
        line = audit.MARKER+audit._compact(self.manifest)
        self.assertEqual(audit._envelope(line,"manifest",1024),self.manifest)
        for output in ("", line+"\n"+line, "CORE_PHASE12_AUDIT_ERROR\n"+line, "unexpected output\n"+line, line[:-1], audit.MARKER+"x"*1024):
            with self.subTest(output_length=len(output)), self.assertRaises(audit.AuditVmError):
                audit._envelope(output,"manifest",1024)


class AuditGuestBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.namespace = {"__name__":"synthetic_guest_helpers"}
        exec(compile(audit.GUEST_COMMON,"synthetic_guest_helpers","exec"), cls.namespace)
    def test_static_guest_programs_compile_without_running(self):
        compile(audit.GUEST_QUERY,"synthetic_query_guest","exec")
        compile(audit.GUEST_READER,"synthetic_reader_guest","exec")
    def test_actual_guest_table_validator_rejects_partial_and_malformed_results(self):
        self.assertEqual(self.namespace["tables"](TABLE), TABLE)
        for value in ({**TABLE,"error":{"code":"PartialError"}}, {"tables":[]},
                      {"tables":[{"name":"PrimaryResult","columns":[{"name":"x","type":"long"}],"rows":[[]]}]}):
            with self.assertRaises(RuntimeError): self.namespace["tables"](value)
    def test_guest_file_ownership_permissions_links_and_reparse_points_fail(self):
        os_module = self.namespace["os"]
        good = SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=0,st_file_attributes=0)
        for values in ({"st_uid":7}, {"st_mode":stat.S_IFREG|0o644},
                       {"st_mode":stat.S_IFLNK|0o600}, {"st_file_attributes":0x400}):
            info = SimpleNamespace(**{**vars(good),**values})
            with patch.object(Path,"lstat",return_value=info), patch.object(os_module,"geteuid",return_value=0,create=True):
                with self.assertRaises(RuntimeError): self.namespace["private"]("synthetic-input")
    def test_guest_lock_precedes_client_hash_and_import_and_wrong_client_stops(self):
        events=[]
        os_module=self.namespace["os"]
        fake_fcntl=SimpleNamespace(LOCK_EX=1,LOCK_NB=2,flock=lambda fd,flags:events.append("lock"))
        inventory=deepcopy(INVENTORY)
        sink=ModuleType("synthetic_audit_sink")
        sink.events=events; sink.inventory=inventory; sink.vm_uuid=VM_UUID
        code=b"""from synthetic_audit_sink import events, inventory, vm_uuid

def load_inventory(path):
    events.append("inventory")
    return inventory

class HttpTransport:
    pass

class VmIdentity:
    def __init__(self,*args):
        self.compute_vm_id=vm_uuid
    def verify_vm(self):
        events.append("imds")
"""
        fake_client=ModuleType("management_client")
        fake_client.load_inventory=lambda path:(_ for _ in ()).throw(AssertionError("Poisoned cached client executed"))
        value={"clientSha256":audit._sha(code),"inventory":{key:INVENTORY[key] for key in audit.FIXED_KEYS},"vmUuid":VM_UUID}
        info=SimpleNamespace(st_dev=1,st_ino=2)
        def read(path,limit): events.append("client-hash-read"); return code
        with patch.dict(sys.modules,{"fcntl":fake_fcntl,"management_client":fake_client,"synthetic_audit_sink":sink}), \
             patch.dict(self.namespace,{"private":lambda *args:info,"read_private":read}), \
             patch.object(os_module,"name","posix"), patch.object(os_module,"geteuid",return_value=0,create=True), \
             patch.object(os_module,"open",return_value=23), patch.object(os_module,"fstat",return_value=info), \
             patch.object(os_module,"close"), patch.object(os_module,"O_NOFOLLOW",0x20000,create=True), \
             patch.object(sys,"path",list(sys.path)):
            with self.namespace["verified_guest"](value,PurePosixPath("/synthetic/maintenance")):
                pass
            self.assertEqual(events[:4],["lock","client-hash-read","inventory","imds"])
            events.clear()
            with self.assertRaises(RuntimeError):
                with self.namespace["verified_guest"]({**value,"clientSha256":"0"*64},PurePosixPath("/synthetic/maintenance")):
                    pass
            self.assertEqual(events,["lock","client-hash-read"])
    def test_guest_read_requires_stable_descriptor_and_enforces_read_bound(self):
        os_module=self.namespace["os"]
        good=SimpleNamespace(st_dev=1,st_ino=2)
        stream=MagicMock()
        stream.__enter__.return_value=stream
        stream.fileno.return_value=23
        stream.read.return_value=b"synthetic"
        with patch.dict(self.namespace,{"private":lambda *args:good}), \
             patch.object(os_module,"open",return_value=23) as opening, \
             patch.object(os_module,"fdopen",return_value=stream), \
             patch.object(os_module,"fstat",return_value=good), \
             patch.object(os_module,"O_NOFOLLOW",0x20000,create=True):
            self.assertEqual(self.namespace["read_private"](PurePosixPath("/synthetic/input"),32),b"synthetic")
            self.assertEqual(opening.call_args.args[1],os_module.O_RDONLY|os_module.O_NOFOLLOW)
            stream.read.return_value=b"x"*33
            with self.assertRaises(RuntimeError): self.namespace["read_private"](PurePosixPath("/synthetic/input"),32)
            stream.read.return_value=b"synthetic"
            with patch.object(os_module,"fstat",return_value=SimpleNamespace(st_dev=1,st_ino=99)):
                with self.assertRaises(RuntimeError): self.namespace["read_private"](PurePosixPath("/synthetic/input"),32)

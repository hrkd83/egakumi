"""全protocolの生成元照合と初回stageの衝突拒否。合成transportだけを使う。"""
import copy
import unittest
from pathlib import Path
from policy_fixture_paths import policy_fixture_path

from test_entra_publication import APIM, GLOBAL, NATIVE, fixture, response, source_fixture
import entra_publication as publication


def protocol_fixture():
    current, transport = fixture()
    children = {}
    for api_id, names in current.operations.items():
        current.api_properties[api_id]["protocols"] = ["http", "https"]
        transport.inventory[api_id]["body"]["properties"]["protocols"] = ["http", "https"]
        for name in names:
            child_id = api_id + "/operations/" + name
            children[child_id] = {"method": "POST", "urlTemplate": "/responses", "templateParameters": []}
            transport.inventory[child_id] = response(child_id, children[child_id])
    for name, kind in (("socket", "websocket"), ("rpc", "grpc"), ("proxy", "mcp"), ("native-tool", "mcp"), ("credential-manager-login", "http")):
        api_id = APIM + "/apis/" + name
        properties = {"type": kind, "path": "tools/" + name, "protocols": ["http", "https"],
                      "subscriptionRequired": True, "subscriptionKeyParameterNames": {"header": "api-key", "query": "subscription-key"}}
        names = ("onHandshake",) if kind == "websocket" else ("start",) if kind == "http" else ()
        if kind == "websocket":
            properties.update(serviceUrl="wss://provider.example.com/stream", protocols=["ws", "wss"])
        elif kind == "grpc":
            properties.update(serviceUrl="https://provider.example.com", protocols=["https"], format="grpc",
                              value='syntax = "proto3"; service Probe { rpc Echo (X) returns (X); }')
        elif name == "proxy":
            properties.update(backendId="model-a-backend", mcpProperties={"transportType": "streamable", "endpoints": {"mcp": {"uriTemplate": "/mcp"}}})
        current.api_properties[api_id] = properties
        current.operations[api_id] = names
        current.products[api_id] = ("model-users",)
        transport.inventory[api_id] = response(api_id, properties)
        transport.inventory[api_id + "/products"] = response(api_id + "/products", value=[{"id": NATIVE}])
        transport.inventory[api_id + "/operations"] = response(api_id + "/operations", value=[{"id": api_id + "/operations/" + item} for item in names])
        transport.inventory[api_id + "/revisions"] = response(api_id + "/revisions", value=[{"apiId": "/apis/" + name + ";rev=1", "apiRevision": "1", "isCurrent": True, "isOnline": True}])
        if kind != "websocket":
            current.policies[api_id + "/policies/policy"] = publication.OPEN_PRODUCT_POLICY
        for operation in names:
            operation_id = api_id + "/operations/" + operation
            current.policies[operation_id + "/policies/policy"] = publication.OPEN_PRODUCT_POLICY
            if kind != "websocket":
                children[operation_id] = {"method": "POST", "urlTemplate": "/start", "responses": []}
                transport.inventory[operation_id] = response(operation_id, children[operation_id])
    tool_id = APIM + "/apis/native-tool/tools/redact"
    children[tool_id] = {"displayName": "redact", "operationId": APIM + "/apis/model-a/operations/responses"}
    schema_id = APIM + "/apis/model-a/schemas/public-contract"
    children[schema_id] = {"contentType": "application/vnd.oai.openapi.components+json", "document": {"components": {"schemas": {"Input": {"type": "object"}}}}}
    for api_id in current.operations:
        for kind in ("tools", "schemas"):
            inventory = [{"id": resource_id} for resource_id in children if resource_id.startswith(api_id + "/" + kind + "/")]
            transport.inventory[api_id + "/" + kind] = response(api_id + "/" + kind, value=inventory)
    for resource_id, properties in children.items():
        transport.inventory[resource_id] = response(resource_id, properties)
    for resource_id, value in current.policies.items():
        transport.inventory[resource_id] = response(resource_id, {"format": "rawxml", "value": value})
    manifest = publication.PolicyManifest(current.apim_id, current.policies, current.operations, current.products,
                                         current.backends, current.named_values, current.api_properties, children, 2)
    return manifest, transport


class ProtocolGraphTests(unittest.TestCase):
    def test_all_protocols_and_protected_connection_match_the_exact_source(self):
        manifest, transport = protocol_fixture()
        publication.read_policy_graph(manifest, transport)
        source = source_fixture(manifest)
        source.update(schemaVersion=2, apiChildren=[{"suffix": resource_id[len(APIM):], "properties": properties}
                                                  for resource_id, properties in manifest.api_children.items()])
        restored = publication.source_manifest(source, APIM, [resource_id.rsplit("/", 1)[-1] for resource_id in manifest.operations], manifest.policies[GLOBAL])
        self.assertEqual(restored, manifest)

    def test_protocol_routing_tools_schemas_and_operations_cannot_drift(self):
        for resource_id, field, value in (
                (APIM + "/apis/socket", "serviceUrl", "wss://other.example.com"),
                (APIM + "/apis/proxy", "backendId", "other"),
                (APIM + "/apis/rpc", "value", 'syntax = "proto3"; service Other {}'),
                (APIM + "/apis/native-tool/tools/redact", "operationId", APIM + "/apis/model-b/operations/responses"),
                (APIM + "/apis/model-a/schemas/public-contract", "document", {}),
                (APIM + "/apis/model-a/operations/responses", "urlTemplate", "/other")):
            with self.subTest(resource_id=resource_id, field=field):
                manifest, transport = protocol_fixture()
                transport.inventory[resource_id]["body"]["properties"][field] = value
                with self.assertRaises(ValueError):
                    publication.read_policy_graph(manifest, transport)
                self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_unrepresented_children_and_websocket_api_policy_are_rejected(self):
        for suffix in ("/apis/rpc/operations", "/apis/proxy/tools", "/apis/model-a/schemas", "/apis/socket/policies/policy"):
            with self.subTest(suffix=suffix):
                manifest, transport = protocol_fixture()
                resource_id = APIM + suffix
                transport.inventory[resource_id] = response(resource_id, {"value": publication.OPEN_PRODUCT_POLICY}) if suffix.endswith("policy") else response(resource_id, value=[{"id": resource_id + "/unreviewed"}])
                with self.assertRaises(ValueError):
                    publication.read_policy_graph(manifest, transport)

    def test_native_tool_must_reference_an_operation_in_the_same_source_graph(self):
        manifest, transport = protocol_fixture()
        resource_id = APIM + "/apis/native-tool/tools/redact"
        manifest.api_children[resource_id]["operationId"] = APIM + "/apis/unreviewed/operations/invoke"
        transport.inventory[resource_id]["body"]["properties"] = copy.deepcopy(manifest.api_children[resource_id])
        with self.assertRaisesRegex(ValueError, "represented REST"):
            publication.read_policy_graph(manifest, transport)

    def test_only_the_fixed_preinheritance_metadata_guard_is_allowed(self):
        manifest, transport = protocol_fixture()
        resource_id = APIM + "/apis/proxy/policies/policy"
        for guard, allowed in ((publication.PRM_GUARD, True), (publication.PRM_GUARD.replace('code="404"', 'code="200"'), False),
                               ('<set-header name="Authorization" exists-action="delete" />', False)):
            with self.subTest(allowed=allowed):
                value = publication.OPEN_PRODUCT_POLICY.replace("<inbound>", "<inbound>" + guard)
                manifest.policies[resource_id] = value
                transport.inventory[resource_id] = response(resource_id, {"value": value})
                if allowed:
                    publication.read_policy_graph(manifest, transport)
                else:
                    with self.assertRaises(ValueError):
                        publication.read_policy_graph(manifest, transport)

    def test_previous_metadata_guard_requires_matching_previous_source(self):
        """旧guardの読戻し互換は完全一致に限定し、新規受付や改変へ広げない。"""
        previous=publication.PREVIOUS_PRM_GUARD
        current=publication.PRM_GUARD
        changed=previous.replace('code="404"','code="200"')
        old_policy=publication.OPEN_PRODUCT_POLICY.replace('<inbound>','<inbound>'+previous)
        with self.assertRaises(ValueError):
            publication._xml(old_policy,inherited=True)
        publication._xml(publication.OPEN_PRODUCT_POLICY.replace('<inbound>','<inbound>'+current),inherited=True)
        for api in ('proxy','native-tool'):
            for expected,observed,allowed in ((previous,previous,True),(current,current,True),
                                              (previous,current,False),(current,previous,False),
                                              (previous,changed,False),(changed,changed,False)):
                with self.subTest(api=api,expected=expected==previous,observed=observed==previous,allowed=allowed):
                    manifest,transport=protocol_fixture()
                    resource_id=APIM+'/apis/'+api+'/policies/policy'
                    manifest.policies[resource_id]=publication.OPEN_PRODUCT_POLICY.replace('<inbound>','<inbound>'+expected)
                    transport.inventory[resource_id]=response(resource_id,{
                        'value':publication.OPEN_PRODUCT_POLICY.replace('<inbound>','<inbound>'+observed)})
                    if allowed:publication.read_policy_graph(manifest,transport)
                    else:
                        with self.assertRaises(ValueError):publication.read_policy_graph(manifest,transport)
                    self.assertTrue(all(call[0]=='GET' for call in transport.calls))

    def test_grpc_missing_import_readback_is_not_assumed_compatible(self):
        manifest, transport = protocol_fixture()
        properties = transport.inventory[APIM + "/apis/rpc"]["body"]["properties"]
        properties.pop("format")
        properties.pop("value")
        with self.assertRaisesRegex(ValueError, "routing/key"):
            publication.read_policy_graph(manifest, transport)


def fresh_fixture():
    plan = {"apimId": APIM, "publishedApiIds": [APIM + "/apis/new-api"], "parameters": {
        "apiDefinitions": {"value": [{"name": "new-api", "productId": "model-users", "keySubscriptions": []}]},
        "names": {"value": {"keyVault": "kv-fixture"}}, "l7IngressSecretIdentifier": {"value": ""}}}
    _, transport = fixture()
    transport.inventory = {GLOBAL: response(GLOBAL, {"value": '<policies><inbound /><backend /><outbound /><on-error /></policies>'})}
    for kind in ("apis", "policyFragments", "backends", "products", "namedValues", "subscriptions", "loggers", "diagnostics"):
        transport.inventory[APIM + "/" + kind] = response(APIM + "/" + kind, value=[])
    return plan, transport


class FreshPreflightTests(unittest.TestCase):
    def test_fresh_plan_preserves_unrelated_api_keys_products_and_global(self):
        plan, transport = fresh_fixture()
        for kind, name in (("apis", "unrelated;rev=2"), ("products", "unrelated"), ("subscriptions", "unrelated")):
            transport.inventory[APIM + "/" + kind]["body"]["value"] = [{"id": APIM + "/" + kind + "/" + name}]
        snapshot = publication.read_fresh_publication(plan, transport)
        publication.verify_snapshot(snapshot, transport)
        self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_existing_api_revision_or_shared_child_is_not_a_fresh_install(self):
        for kind, name in (("apis", "new-api;rev=2"), ("policyFragments", "shared"), ("backends", "shared"),
                           ("products", "released-apis"), ("namedValues", "new-api-backend-audience"),
                           ("loggers", "azuremonitor"), ("diagnostics", "azuremonitor")):
            with self.subTest(kind=kind):
                plan, transport = fresh_fixture()
                transport.inventory[APIM + "/" + kind]["body"]["value"] = [{"id": APIM + "/" + kind + "/" + name}]
                with self.assertRaises(ValueError):
                    publication.read_fresh_publication(plan, transport)
                self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    def test_pagination_or_global_drift_prevents_the_first_write(self):
        plan, transport = fresh_fixture()
        transport.inventory[APIM + "/apis"]["body"]["nextLink"] = "https://management.azure.com/next"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            publication.read_fresh_publication(plan, transport)
        plan, transport = fresh_fixture()
        snapshot = publication.read_fresh_publication(plan, transport)
        transport.refresh(GLOBAL, {"value": publication.OPEN_PRODUCT_POLICY})
        with self.assertRaisesRegex(ValueError, "drifted"):
            publication.verify_snapshot(snapshot, transport)


class GeneratedSourceTests(unittest.TestCase):
    def test_compiled_mcp_metadata_guards_preserve_boundaries_before_anonymous_authentication(self):
        """実生成guardを実共通認証より先に実行し、本文を読まず予約パスだけ404にする。"""
        import json
        import subprocess
        import tempfile
        import xml.etree.ElementTree as ET
        from arm_helpers import compiled_policy_value
        from bicep_helpers import compile_bicep
        from test_api_protocols import bound_external_coverage, configuration
        from test_common_authentication import CSHARP, compile_inbound
        from test_gateway_limits_telemetry import PWSH
        from deployment.helpers.apim_authentication import render_service_policy
        if not PWSH:self.skipTest('PowerShell Roslyn is required; Azure routing is not tested')
        core=Path(__file__).resolve().parents[1]
        canonical=(core/'apps/gateway/apim/policies/snippets/request/mcp-metadata-guard/mcp-metadata-guard.xml').read_text(encoding='utf-8')
        self.assertEqual(publication.PRM_GUARD,canonical)
        expected=publication._structure(ET.fromstring(canonical))
        config=configuration.normalize_configuration(bound_external_coverage(),base_directory=core/'config')
        definitions=[api for api in config['apiDefinitions'] if api['apiType']=='mcp' or 'mcp' in api]
        self.assertTrue(any(api['apiType']=='mcp' for api in definitions))
        self.assertTrue(any('mcp' in api for api in definitions))
        template=compile_bicep('apps/gateway/apim/api-publication.bicep')
        common_methods,common_inbound=compile_inbound(ET.fromstring(render_service_policy()),trace_messages=True)
        methods=[common_methods];selectors=[];cases=[];expectations={}
        for index,api in enumerate(definitions):
            api.setdefault('backendUrl','https://provider.example.com'+api.get('backendPath',''))
            generated='mcp' in api
            name=api['mcp']['name'] if generated else api['name']
            policy=ET.fromstring(compiled_policy_value(template,{'apiDefinitions':[api]},
                resource_name='mcpPolicies' if generated else 'apiPolicies'))
            inbound=policy.find('inbound');children=list(inbound)
            base=inbound.find('base');self.assertIsNotNone(base)
            guards=[node for node in children if publication._structure(node)==expected]
            self.assertEqual(len(guards),1,name)
            guard=guards[0]
            self.assertLess(children.index(guard),children.index(base),name)
            self.assertIsNone(guard.find('.//set-body'))
            self.assertEqual(guard.find('./when/return-response/set-status').attrib,{'code':'404','reason':'Not Found'})
            self.assertNotIn('__MCP_PRM_GUARD_XML__',ET.tostring(policy,encoding='unicode'))
            # 原本の条件・応答を抽出する。base前位置は上で実生成全体から確認し、
            # 実共通認証へ戻る処理だけを後段の共通メソッドへ接続する。
            adapted=ET.Element('policies');ET.SubElement(adapted,'inbound').append(copy.deepcopy(guard))
            guard_methods,guard_body=compile_inbound(adapted)
            prefix='MetadataGuard'+str(index)+'Expression'
            methods.append(guard_methods.replace('Expression',prefix))
            methods.append('static int MetadataGuard'+str(index)+'(Context context) { '+guard_body.replace('Expression',prefix)+' return 0; }')
            selectors.append(('if' if index==0 else 'else if')+' (name == '+json.dumps(name)+') status=MetadataGuard'+str(index)+'(context);')
            api_prefix='/'+(api['mcp']['path'] if generated else api['path'])
            reserved='/.well-known/oauth-protected-resource'
            paths=(
                ('terminal',reserved,True),('api-terminal',api_prefix+reserved,True),
                ('child',api_prefix+reserved+'/',True),('nested-child',api_prefix+reserved+'/nested/path',True),
                ('case',api_prefix+reserved.upper(),True),('depth',api_prefix+'/one/two'+reserved,True),
                ('mixed-child',api_prefix+'/.WeLl-KnOwN/Oauth-Protected-Resource/child',True),
                ('similar-suffix',api_prefix+reserved+'-example',False),
                ('similar-plural',api_prefix+reserved+'s',False),
                ('similar-letter',api_prefix+reserved+'X',False),
                ('similar-child',api_prefix+reserved+'-example/child',False),
                ('similar-directory',api_prefix+'/.well-known-example/oauth-protected-resource',False),
                ('similar-prefix',api_prefix+'/prefix-.well-known/oauth-protected-resource',False),
                ('later-terminal',api_prefix+reserved+'-example'+reserved,True),
                ('later-child',api_prefix+reserved+'-example'+reserved+'/child',True),
                ('normal',api_prefix+'/mcp',False),('root','/',False),
            )
            for label,path,rejected in paths:
                identifier=name+'-'+label
                cases.append({'id':identifier,'policy':name,'path':path})
                expectations[identifier]=rejected
        self.assertGreaterEqual(len(definitions),2)
        # 共通fixtureの要求・Context型を再利用し、本文読取りがあれば必ず試験を止める。
        source=CSHARP.split('public static class CommonAuthenticationFixture {',1)[0]
        # traceを実行する共通監査式は、応答前のResponse=nullも参照します。
        source=source.replace('public class Context {','public class FixtureResponse { public int StatusCode; }\npublic class Context { public FixtureResponse Response;')
        original='public object Body { get { throw new Exception("Common authentication read a request body"); } }'
        self.assertIn(original,source)
        source=source.replace(original,'public int BodyReads; public object Body { get { BodyReads++; throw new Exception("MCP metadata guard read a request body"); } }')
        source+=r'''
public static class MetadataFixture {
 __METHODS__
 static int Evaluate(Context context,string name) {
  var status=0;__SELECTORS__ else throw new Exception("Unknown metadata fixture policy");
  if(status!=0)return status;
  context.Variables["fixtureBaseReached"]=true;
  __COMMON_INBOUND__ return 200;
 }
 public static string Run(string json) {
  var results=new JObject();
  foreach(JObject item in JArray.Parse(json)) {
   var c=new Context();c.Api.Id=(string)item["policy"];c.Operation.Id="invoke";c.Request.Method="GET";
   c.Request.Url.Path=(string)item["path"];c.Request.OriginalUrl.Path=c.Request.Url.Path;
   c.Request.Headers["X-Core-L7-Ingress"]=new [] {"fixture-ingress"};
   var status=Evaluate(c,(string)item["policy"]);
   results[(string)item["id"]]=new JObject(new JProperty("status",status),
    new JProperty("anonymous",c.Subscription==null&&c.Token==null),
    new JProperty("baseReached",c.Variables.ContainsKey("fixtureBaseReached")),
    new JProperty("bodyReads",c.Request.BodyReads),new JProperty("headers",JObject.FromObject(c.ResponseHeaders)),
    new JProperty("requestId",c.RequestId.ToString()),new JProperty("auditCount",c.TraceMessages.Count));
  }
  return results.ToString(Formatting.None);
 }
}'''.replace('__METHODS__','\n'.join(methods)).replace('__SELECTORS__',' '.join(selectors)).replace('__COMMON_INBOUND__',common_inbound)
        with tempfile.TemporaryDirectory()as directory:
            folder=Path(directory)
            (folder/'fixture.cs').write_text(source,encoding='utf-8')
            (folder/'cases.json').write_text(json.dumps(cases),encoding='utf-8')
            (folder/'run.ps1').write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll')|ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[MetadataFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""",encoding='utf-8')
            result=subprocess.run([PWSH,'-NoProfile','-File',str(folder/'run.ps1')],capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        observed=json.loads(result.stdout)
        self.assertEqual(set(observed),set(expectations))
        for identifier,rejected in expectations.items():
            with self.subTest(case=identifier):
                value=observed[identifier]
                self.assertTrue(value['anonymous']);self.assertEqual(value['bodyReads'],0)
                self.assertEqual(value['status'],404 if rejected else 401)
                self.assertEqual(value['baseReached'],not rejected)
                if rejected:
                    self.assertEqual(value['headers'],{'apim-request-id':value['requestId']})
                    self.assertEqual(value['auditCount'],0)

    def test_compiled_source_represents_the_real_assignments_for_every_protocol(self):
        from bicep_helpers import compile_bicep
        from test_api_protocols import bound_external_coverage, configuration
        from test_api_publication_size import SizeExpression, expand
        from deployment.helpers import credential_login
        core = Path(__file__).resolve().parents[1]
        config = configuration.normalize_configuration(bound_external_coverage(), base_directory=core / "config")
        definitions = config["apiDefinitions"]
        for api in definitions:
            api.setdefault("backendUrl", "https://provider.example.com" + api.get("backendPath", ""))
            for operation in api["operations"]:
                if "backendOverride" in operation:
                    operation["backendOverride"].setdefault("backendUrl", "https://provider.example.com")
        template = compile_bicep("apps/gateway/apim/api-publication.bicep")
        parameters = {"apiDefinitions": definitions, "names": {"apim": "apim-test-fixture"}, "publishApis": True,
                      "rateLimits": config["gateway"]["rateLimits"], "credentialLogin": credential_login.settings(config)}
        expanded = expand(template, parameters)
        reader = SizeExpression("[true()]", template["variables"], {}, template.get("functions", []))
        source = reader.resolve(template["outputs"]["entraPublicationSource"]["value"], expanded["parameters"])
        self.assertEqual(source["schemaVersion"], 2)
        expected_names = {api["name"] for api in definitions} | {api["mcp"]["name"] for api in definitions if "mcp" in api} | {"credential-manager-login"}
        self.assertEqual({entry["name"] for entry in source["apis"]}, expected_names)
        self.assertNotIn("credential-manager-browser", expected_names)
        actual = {}
        for resource in expanded["resources"]:
            if not resource["type"].startswith("Microsoft.ApiManagement/service/") or "properties" not in resource:
                continue
            kinds = resource["type"].split("/")[2:]
            names = resource["name"].split("/")[1:]
            suffix = "/" + "/".join(part for pair in zip(kinds, names) for part in pair)
            actual[suffix] = resource["properties"]
        for entry in source["apis"]:
            self.assertTrue(publication._contains(actual["/apis/" + entry["name"]], entry["properties"]), entry["name"])
        for key, field in (("policies", "value"), ("backends", "properties"), ("namedValues", "properties"), ("apiChildren", "properties")):
            for entry in source[key]:
                observed = actual[entry["suffix"]]
                self.assertEqual(observed["value"] if field == "value" else observed, entry[field], entry["suffix"])
        # Compare deployment XML against the independent Python renderer, not
        # just two fields emitted by the same compiled ARM template.
        from deployment.helpers.apim_audit import render_audit_fragments, render_audit_policy
        from deployment.helpers.apim_ingress import render_trusted_ingress_fragment
        from deployment.helpers.apim_browser_exception import render_browser_exception_fragment
        source_policies = {entry["suffix"]: entry["value"] for entry in source["policies"]}
        common_fragments = {**render_audit_fragments(), "core-trusted-ingress": render_trusted_ingress_fragment(),
                            "core-credential-browser-exception": render_browser_exception_fragment(parameters["credentialLogin"]),
                            "core-gateway-telemetry": render_audit_policy(policy_fixture_path(core / "apps/gateway/apim/policies", "core-gateway-telemetry.xml").read_text(encoding="utf-8"))}
        for identifier, expected in common_fragments.items():
            suffix = "/policyFragments/" + identifier
            self.assertEqual(actual[suffix]["value"].replace("\r\n", "\n"), expected, identifier)
            self.assertEqual(source_policies[suffix].replace("\r\n", "\n"), expected, identifier)
        import xml.etree.ElementTree as ET
        # Every source explanation must remain visible in its canonical Azure
        # fragment, even when repeated inline copies point readers here.
        for identifier, filename in (("core-request-context", "core-request-context.cs"),
                                     ("core-audit-authenticated-subject", "core-audit-authenticated-subject.cs"),
                                     ("core-credential-browser-exception", "core-credential-browser-exception.cs"),
                                     ("core-gateway-telemetry", "core-gateway-telemetry.cs")):
            expression_source = policy_fixture_path(core / "apps/gateway/apim/policies", filename).read_text(encoding="utf-8")
            comments = ["/*" + line.lstrip()[2:].replace("*/", "* /") + " */"
                        for line in expression_source.splitlines() if line.lstrip().startswith("//")]
            root = ET.fromstring(actual["/policyFragments/" + identifier]["value"])
            deployed_text = "\n".join(value for node in root.iter() for value in (*node.attrib.values(), node.text or ""))
            self.assertTrue(comments, filename)
            for comment in comments:
                self.assertIn(comment, deployed_text, identifier)
        cm_suffix = '/apis/credential-manager-login/policies/policy'
        cm_source = next(entry['value'] for entry in source['policies'] if entry['suffix'] == cm_suffix)
        cm_inbound = ET.fromstring(cm_source).find('inbound')
        self.assertEqual([node.tag for node in cm_inbound], ['base'])
        self.assertEqual(cm_source, actual[cm_suffix]['value'])
        self.assertTrue(all('/credential-manager-browser' not in entry['suffix']
                            for key in ('policies', 'apiChildren') for entry in source[key]))
        browser = actual['/apis/credential-manager-browser']
        self.assertIs(browser['subscriptionRequired'], False)
        browser_operations = [resource for suffix, resource in actual.items()
                              if suffix.startswith('/apis/credential-manager-browser/operations/') and suffix.endswith('/policies/policy')]
        self.assertTrue(browser_operations)
        for operation in ('callback', 'identity-callback'):
            browser_policy = actual['/apis/credential-manager-browser/operations/' + operation + '/policies/policy']['value']
            self.assertNotIn('gatewayEntraOperationSupported', browser_policy)
        callback = actual['/apis/credential-manager-browser/operations/callback/policies/policy']['value']
        self.assertIn('credentialTransaction', callback)
        identity = ET.fromstring(actual['/apis/credential-manager-browser/operations/identity-callback/policies/policy']['value'])
        self.assertEqual(identity.find('./inbound/rewrite-uri').get('template'), '/v1/identity-callback')
        self.assertEqual(identity.find('./inbound/set-backend-service').get('backend-id'), 'credential-broker')
        self.assertIsNotNone(identity.find('./inbound/set-header[@name="X-Core-Broker-Key"]'))
        browser_source = reader.resolve(template['outputs']['credentialBrowserPublicationSource']['value'], expanded['parameters'])
        self.assertEqual(browser_source['schemaVersion'], 1)
        self.assertEqual(browser_source['name'], 'credential-manager-browser')
        self.assertIs(browser_source['apiPolicyAbsent'], True)
        self.assertNotIn('/apis/credential-manager-browser/policies/policy', actual)
        self.assertTrue(publication._contains(browser, browser_source['properties']))
        self.assertEqual({entry['name'] for entry in browser_source['operations']},
                         {'launch', 'launch-post', 'identity-callback', 'callback'})
        for entry in browser_source['operations']:
            self.assertEqual(actual['/apis/credential-manager-browser/operations/' + entry['name']], entry['properties'])
        self.assertEqual(len(browser_source['policies']), 4)
        for entry in browser_source['policies']:
            self.assertEqual(actual[entry['suffix']]['value'], entry['value'])

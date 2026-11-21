"""実BicepのARM/XMLを展開し、Agent登録と本文非収集のnative診断を検証。"""
import base64
import copy
import json
import re
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import test_contract_rate_publication as rate_fixture
from arm_helpers import PolicyExpression, compiled_policy_value
from arm_helpers import DiscoveryExpression
from configuration_fixtures import connection_fixture, configuration
from deployment.helpers.diagnostics import canonical_settings
from deployment.helpers.apim_audit import render_audit_policy
from deployment.helpers.apim_input_contract import input_contracts
from policy_fixture_paths import policy_fixture_path
from bicep_helpers import compile_bicep

CORE=Path(__file__).resolve().parents[1]
class AuditPublicationTests(unittest.TestCase):
 setUpClass=classmethod(rate_fixture.ContractRatePublicationTests.setUpClass.__func__)
 def setUp(self): self.parameters=copy.deepcopy(type(self).parameters)
 def evaluate(self,value,index=0):
  return PolicyExpression(value,self.template['variables'],functions=self.template['functions']).evaluate(self.parameters,index)
 def api_settings(self,root):
  source=root.find("inbound/set-variable[@name='gatewayApiSettingsSource']")
  self.assertIsNotNone(source)
  lines=base64.b64decode(source.attrib['value'],validate=True).decode('utf-8').split('\n')
  self.assertEqual(len(lines),3)
  inbound=list(root.find('inbound'))
  calls=root.findall("inbound/include-fragment[@fragment-id='core-api-settings']")
  self.assertEqual(len(calls),1)
  self.assertEqual(calls[0].attrib,{'id':'core-api-settings','fragment-id':'core-api-settings'})
  self.assertEqual(inbound.index(calls[0]),inbound.index(source)+1)
  before=inbound[:inbound.index(root.find('inbound/base'))]
  self.assertIn(calls[0],before)
  self.assertFalse(any(node.tag=='set-variable' and node.get('name') in
    ('gatewayApiSettings','gatewayAuditSettings','gatewayControlSettings') for node in before))
  return lines[0],json.loads(lines[1]),json.loads(lines[2])
 def test_shared_audit_subject_matches_python_renderer_in_published_trace_templates(self):
  def structure(node):
   text=node.text or ''
   return (node.tag,tuple(sorted(node.attrib.items())),text if text.strip() else '',
           tuple(structure(child) for child in node),(node.tail or '').strip())
  directory=CORE/'apps/gateway/apim/policies'
  marker='__AUDIT_SUBJECT_OBSERVATION_CSHARP__'
  for filename,resource_name,source_marker,comment_mode in (
    ('core-api-settings.xml','apiSettingsFragment','__CORE_API_SETTINGS_CSHARP__','full'),
    ('core-api-authentication-result.xml','apiAuthenticationResultFragment','__CORE_API_AUTHENTICATION_RESULT_CSHARP__','full'),
    ('core-model-limits.xml','modelLimitsFragment','__CORE_MODEL_LIMITS_CSHARP__','full'),
    ('core-document-input-validation.xml','documentInputValidationFragment','__CORE_DOCUMENT_INPUT_VALIDATION_CSHARP__','full'),
    ('core-gateway-telemetry.xml','gatewayTelemetryFragment','__CORE_GATEWAY_TELEMETRY_CSHARP__','full'),
    ('core-query-telemetry.xml','queryTelemetryFragment','__CORE_QUERY_TELEMETRY_CSHARP__','full')):
   with self.subTest(template=filename):
    raw=policy_fixture_path(directory,filename).read_text(encoding='utf-8')
    self.assertIn(source_marker,raw)
    expected=ET.fromstring(render_audit_policy(raw,source_comments=comment_mode))
    value=self.template['resources'][resource_name]['properties']['value']
    actual=self.evaluate(value) if value.startswith('[') else value
    self.assertNotIn(source_marker,actual)
    self.assertEqual(structure(ET.fromstring(actual)),structure(expected))
  raw=(directory/'credential-telemetry.xml').read_text(encoding='utf-8')
  self.assertIn(marker,raw)
  expected=ET.fromstring(render_audit_policy(raw,source_comments='compact'))
  actual=self.evaluate("[variables('credentialGatewayTelemetryBody')]")
  self.assertNotIn(marker,actual)
  self.assertEqual(structure(ET.fromstring('<fragment>'+actual+'</fragment>')),structure(expected))
  expected_messages=[structure(node) for node in expected.findall('.//trace[@source="core-gateway"]')]
  self.assertTrue(expected_messages)
  for resource_name,filename in (('credentialConnectionFragment','credential-connection.xml'),
    ('credentialSessionUseFragment','credential-session-use.xml'),
    ('credentialConnectionOutboundFragment','credential-connection-outbound.xml')):
   with self.subTest(fragment=resource_name):
    source=ET.fromstring(render_audit_policy((directory/filename).read_text(encoding='utf-8'),source_comments='compact'))
    source_messages=expected_messages+[structure(node) for node in source.findall('.//trace[@source="core-gateway"]')]
    value=self.template['resources'][resource_name]['properties']['value']
    actual=self.evaluate(value) if value.startswith('[') else value
    self.assertNotIn(marker,actual)
    messages=ET.fromstring(actual).findall('.//trace[@source="core-gateway"]')
    self.assertTrue(messages)
    for node in messages:
     self.assertIn(structure(node),source_messages)
     self.assertIn('"authenticatedSubject"',node.find('message').text)
     self.assertIn('"requestContext"',node.find('message').text)
 def test_every_compiled_policy_has_expanded_audit_settings_and_backend_capture_before_base(self):
  for index,api in enumerate(self.parameters['apiDefinitions']):
   value=compiled_policy_value(self.template,self.parameters,index)
   with self.subTest(api=api['name']):
    self.assertNotIn('__AUDIT_',value)
    root=ET.fromstring(value)
    api_type,settings,controls=self.api_settings(root)
    self.assertEqual(api_type,api['apiType'])
    self.assertNotIn('__API_SETTINGS_',value)
    self.assertNotIn('__CONTROL_',value)
    self.assertEqual(set(settings),{'serviceClass','backendService','backendAuthentication','operationBackends',
      'llmOperationIds','websocketModel','targetAgent','measurementRole'})
    self.assertEqual(settings['operationBackends'],{
      operation['name']:{'backendService':operation['backendOverride']['backendService'],
        'backendAuthentication':operation['backendOverride']['backendAuthentication']['mode']}
      for operation in api['operations'] if 'backendOverride' in operation})
    self.assertEqual(set(controls),{'operationId','operationConstraints','tokenLimitOperationIds','speechSsmlOperationIds','queryObservations','mcpQueryObservations','inputContracts'})
    self.assertEqual(controls['operationId'],'')
    self.assertEqual(controls['operationConstraints'],api['authorization']['operationConstraints'])
    self.assertEqual(controls['inputContracts'],input_contracts(api))
    self.assertEqual(controls['speechSsmlOperationIds'],['tts'] if api['name']=='azure-speech-v1' else [])
    for contract in controls['inputContracts'].values():
     self.assertEqual(contract,{})
    expected=self.evaluate("[__bicep.tokenLimitOperations(parameters('apiDefinitions')[copyIndex()])]",index)
    self.assertEqual(controls['tokenLimitOperationIds'],expected)
    self.assertEqual(settings['backendAuthentication'],'credentialConnection' if api.get('credentialConnection') else api['backendAuthentication']['mode'])
    self.assertEqual(settings['serviceClass'],api['serviceClass'])
    self.assertEqual(root.find('outbound')[0].attrib['fragment-id'],'core-backend-audit')
    if api['apiType'] in ('websocket','mcp'):
     # クエリ拒否のローカル応答だけに本文を作れます。転送要求やSSE応答の本文変換は認めません。
     local_error_bodies=root.findall('.//return-response/set-body')
     self.assertTrue(all(node in local_error_bodies for node in root.iter('set-body')))
  xml=compiled_policy_value(self.template,self.parameters,resource_name='mcpPolicies')
  api_type,settings,controls=self.api_settings(ET.fromstring(xml))
  self.assertEqual(api_type,'mcp')
  self.assertEqual(settings['measurementRole'],'facade'); self.assertEqual(settings['llmOperationIds'],[])
  api=next(api for api in self.parameters['apiDefinitions'] if 'mcp' in api)
  self.assertEqual(controls,{'operationId':api['mcp']['operationNames'][0],
    'operationConstraints':api['authorization']['operationConstraints'],'tokenLimitOperationIds':[], 'speechSsmlOperationIds':[],
    'queryObservations':{},'mcpQueryObservations':{method+' /mcp':api['mcp']['queryObservation'] for method in ('POST','GET','DELETE')} if 'queryObservation' in api['mcp'] else {},
    'inputContracts':{}})
 def test_effective_cm_authentication_metadata_does_not_change_the_authentication_or_route(self):
  # 実Bicepから生成したXMLを比較し、CMの監査表示だけが変わることを確認します。
  config=rate_fixture.configuration.normalize_configuration(connection_fixture(),base_directory=CORE/'config')
  api=config['apiDefinitions'][0]
  self.parameters['apiDefinitions'].append(api)
  index=len(self.parameters['apiDefinitions'])-1
  for enabled in (True,False):
   api['credentialConnection']['enabled']=enabled
   value=compiled_policy_value(self.template,self.parameters,index)
   root=ET.fromstring(value)
   api_type,settings,_=self.api_settings(root)
   self.assertEqual(api_type,'http')
   self.assertEqual(settings['backendAuthentication'],'credentialConnection')
   self.assertIsNotNone(root.find("inbound/choose/when/include-fragment[@fragment-id='core-credential-connection']"))
  # CMを持たないHTTP、native protocol、外側MCPの表示は基底の認証方式を保ちます。
  connection=api.pop('credentialConnection')
  settings=self.evaluate("[__bicep.auditSettings(parameters('apiDefinitions')[copyIndex()], '', false())]",index)
  self.assertEqual(settings['backendAuthentication'],api['backendAuthentication']['mode'])
  api['credentialConnection']=connection
  settings=self.evaluate("[__bicep.auditSettings(parameters('apiDefinitions')[copyIndex()], 'read-profile', true())]",index)
  self.assertEqual(settings['backendAuthentication'],api['backendAuthentication']['mode'])
 def test_credential_management_api_inherits_without_model_api_settings(self):
  raw=self.template['resources']['credentialLoginApiPolicy']['properties']['value']
  value=self.evaluate(raw) if raw.startswith('[') else raw
  root=ET.fromstring(value)
  for section in ('inbound','backend','outbound','on-error'):
   self.assertEqual([node.tag for node in root.find(section)],['base'])
  self.assertEqual(root.findall('.//include-fragment[@fragment-id="core-api-settings"]'),[])
  self.assertEqual(root.findall('.//set-variable[@name="gatewayApiSettingsSource"]'),[])
 def expanded_fragment_graph(self,root):
  """実コンパイル値のinclude先を再帰探索し、未解決や循環を成功扱いしない。"""
  fragments={}
  for resource in self.template['resources'].values():
   if resource.get('type','').endswith('/policyFragments'):
    names=re.findall(r"'(core-[A-Za-z0-9-]+)'",resource['name'])
    self.assertEqual(len(names),1)
    value=resource['properties']['value']
    fragments[names[0]]=value
  found={}; visiting=set()
  def visit(node):
   for include in node.findall('.//include-fragment'):
    name=include.attrib['fragment-id']
    self.assertIn(name,fragments)
    self.assertNotIn(name,visiting,'A fragment cycle must not hide a body reference')
    if name in found: continue
    visiting.add(name)
    value=fragments[name]
    expanded=ET.fromstring(self.evaluate(value) if value.startswith('[') else value)
    visit(expanded)
    visiting.remove(name); found[name]=expanded
  visit(root)
  return found
 def assert_body_free_nodes(self,roots):
  """未実行分岐内の式も含め、本文へのアクセスと書換えを検出する。"""
  for label,root in roots.items():
   for node in root.iter():
    with self.subTest(fragment=label,tag=node.tag):
     self.assertNotEqual(node.tag,'set-body')
     for value in [*node.attrib.values(),node.text or '']:
      self.assertNotRegex(value,r'(?:Request|Response)\s*\.\s*Body\b')
 def test_native_protocols_and_all_transitive_fragments_never_access_opaque_bodies(self):
  self.parameters.update(publishApis=True,l7IngressSecretIdentifier='',entraTenantId=self.config['entraTenantId'])
  service=ET.fromstring(render_audit_policy((CORE/'apps/gateway/apim/policies/service.xml').read_text()
    .replace('__L7_INGRESS_REQUIRED__','false').replace('__L7_INGRESS_EXPECTED__','')))
  native_count=0
  for index,api in enumerate(self.parameters['apiDefinitions']):
   if api['apiType']!='websocket':continue
   root=ET.fromstring(compiled_policy_value(self.template,self.parameters,index))
   graph=self.expanded_fragment_graph(root)
   self.assertTrue({'core-audit-context','core-backend-audit','core-gateway-telemetry','core-rate-limit'} <= set(graph))
   self.assertNotIn('core-audit-body-model',graph)
   self.assert_body_free_nodes({api['name']:root,'inherited-service':service,**graph})
   native_count+=1
  self.assertGreaterEqual(native_count,2)
 def test_http_extracts_model_before_common_audit_and_mcp_does_not_import_that_reader(self):
  for index,api in enumerate(self.parameters['apiDefinitions']):
   root=ET.fromstring(compiled_policy_value(self.template,self.parameters,index))
   ids=[node.attrib['fragment-id'] for node in root.findall('inbound/include-fragment')]
   if api['apiType']=='http':
    self.assertIn('core-audit-body-model',ids)
    self.assertLess(ids.index('core-audit-body-model'),ids.index('core-audit-context'))
   if api['apiType']=='mcp':
    graph=self.expanded_fragment_graph(root)
    self.assertNotIn('core-audit-body-model',graph)
    for node in root.iter():
     for value in [*node.attrib.values(),node.text or '']:
      self.assertNotRegex(value,r'Response\s*\.\s*Body\b')
      if re.search(r'Request\s*\.\s*Body\b',value):
       self.assertEqual(node.tag,'set-variable')
       self.assertEqual(node.attrib.get('name'),'gatewayRateLimitExempt')
    self.assert_body_free_nodes(graph)
  # REST由来MCP facadeにも、HTTPモデル本文抽出fragmentを推移的に持ち込まない。
  facade=ET.fromstring(compiled_policy_value(self.template,self.parameters,resource_name='mcpPolicies'))
  graph=self.expanded_fragment_graph(facade)
  self.assertNotIn('core-audit-body-model',graph)
  self.assert_body_free_nodes(graph)

 def test_generated_mcp_argument_guard_preserves_input_and_only_builds_a_fixed_error(self):
  """本文を監査せず、PII引数の検査とJSON-RPCエラー生成だけに使う。"""
  facade=ET.fromstring(compiled_policy_value(self.template,self.parameters,resource_name='mcpPolicies'))
  readers=[]
  for node in facade.iter():
   values=[*node.attrib.values(),node.text or '']
   for value in values:
    self.assertNotRegex(value,r'Response\s*\.\s*Body\b')
   if any(re.search(r'Request\s*\.\s*Body\b',value) for value in values):readers.append(node)
  self.assertEqual([(node.tag,node.attrib.get('name')) for node in readers],
                   [('set-variable','gatewayMcpArgumentError')])
  guard=readers[0].attrib['value']
  self.assertIn('preserveContent: true',guard)
  self.assertIn('constraint?["kind"] != "pii"',guard)
  self.assertIn('constraint["allowedLanguages"]',guard)
  self.assertIn('constraint["maxRequestBytes"]',guard)
  self.assertIn('new JProperty("code", -32602)',guard)
  branch,=[node for node in facade.findall('inbound/choose/when')
           if 'gatewayMcpArgumentError' in node.attrib.get('condition','')]
  self.assertEqual(branch.find('return-response/set-status').attrib['code'],'400')
  body=branch.find('return-response/set-body')
  query_error=facade.find("inbound/choose[@id='core-query-rejection']/when/return-response/set-body")
  self.assertIsNone(query_error)
  self.assertEqual(facade.findall('.//set-body'),[body])
  self.assertEqual(body.text,'@((string)context.Variables["gatewayMcpArgumentError"])')

 def test_native_diagnostics_only_select_chat_and_never_enable_message_capture(self):
  diagnostic=self.template['resources']['apiDiagnostics']
  self.assertEqual(diagnostic['apiVersion'],'2025-09-01-preview')
  for index,api in enumerate(self.parameters['apiDefinitions']):
   # 診断のpropertiesはARMのobject expression。実コンパイルのUDFを評価する。
   raw=diagnostic['properties']
   def resolve(value):
    if isinstance(value,dict): return {k:resolve(v) for k,v in value.items()}
    if isinstance(value,list): return [resolve(v) for v in value]
    return self.evaluate(value,index) if isinstance(value,str) and value.startswith('[') else value
   actual={k:resolve(v) for k,v in raw.items() if k!='loggerId'}
   llm=actual['largeLanguageModel']
   expected=api['apiType']=='http' and api['backendService']=='foundryOpenAi' and any(
    o['urlTemplate'].endswith('/chat/completions') and api['authorization']['operationConstraints'][o['name']]['kind']=='bodyModel' for o in api['operations'])
   self.assertEqual(llm,{'logs':'enabled' if expected else 'disabled'})
   for direction in ('frontend','backend'):
    for phase in ('request','response'): self.assertEqual(actual[direction][phase]['body']['bytes'],0)
  for resource in ('globalDiagnostic','mcpDiagnostics'):
   self.assertEqual(self.template['resources'][resource]['properties']['largeLanguageModel'],{'logs':'disabled'})
  config=configuration.build_bicep_configuration(configuration.normalize_configuration(
   connection_fixture(),base_directory=CORE/'config'))
  properties=canonical_settings(config,'core')['apim-logs']['properties']
  self.assertIn({'category':'GatewayLlmLogs','enabled':True},properties['logs'])
  self.assertEqual(properties['logAnalyticsDestinationType'],'Dedicated')
  services=compile_bicep('infra/modules/services.bicep')
  diagnostic=next(item for item in services['resources']
   if item.get('type')=='Microsoft.Insights/diagnosticSettings' and item.get('name')=='core-audit'
    and "names').apim" in item.get('scope',''))
  self.assertEqual(diagnostic['properties'],"[parameters('diagnosticSettingProperties')['apim-logs']]")
 def test_only_explicit_api_registration_writes_api_contracts(self):
  main=(CORE/'infra/main.bicep').read_text(); publisher=(CORE/'apps/gateway/deploy/api_publication.py').read_text()
  self.assertNotIn('api-publication.bicep',main)
  self.assertNotIn('apiDefinitions: resolvedApis',main)
  self.assertIn('apps/gateway/apim/api-publication.bicep',publisher)
  self.assertIn('"apiDefinitions": resolved',publisher)
  self.assertEqual(self.template['resources']['auditContextFragment']['name'],"[format('{0}/{1}', parameters('names').apim, 'core-audit-context')]")
if __name__=='__main__': unittest.main()

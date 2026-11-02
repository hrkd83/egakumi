"""実BicepのARM/XMLを展開し、Agent登録と本文非収集のnative診断を検証。"""
import base64
import copy
import json
import re
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import test_contract_rate_publication as rate_fixture
from arm_helpers import PolicyExpression
from arm_helpers import DiscoveryExpression
from configuration_fixtures import connection_fixture

CORE=Path(__file__).resolve().parents[1]
class AuditPublicationTests(unittest.TestCase):
 setUpClass=classmethod(rate_fixture.ContractRatePublicationTests.setUpClass.__func__)
 def setUp(self): self.parameters=copy.deepcopy(type(self).parameters)
 def evaluate(self,value,index=0):
  return PolicyExpression(value,self.template['variables'],functions=self.template['functions']).evaluate(self.parameters,index)
 def test_every_compiled_policy_has_expanded_audit_settings_and_backend_capture_before_base(self):
  for index,api in enumerate(self.parameters['apiDefinitions']):
   value=self.evaluate("[variables('policyValues')[copyIndex()]]",index)
   with self.subTest(api=api['name']):
    self.assertNotIn('__AUDIT_',value)
    root=ET.fromstring(value)
    raw=root.find("inbound/set-variable[@name='gatewayAuditSettings']").attrib['value']
    settings=json.loads(base64.b64decode(raw.split('"')[1]))
    self.assertEqual(settings['apiId'],api['name'])
    self.assertEqual(settings['backendAuthentication'],'credentialConnection' if api.get('credentialConnection') else api['backendAuthentication']['mode'])
    self.assertEqual(settings['serviceClass'],api['serviceClass'])
    self.assertEqual(root.find('outbound')[0].attrib['fragment-id'],'core-backend-audit')
    if api['apiType'] in ('websocket','grpc','mcp'): self.assertIsNone(root.find('.//set-body'))
  xml=self.evaluate("[variables('mcpPolicyValues')[copyIndex()]]")
  raw=ET.fromstring(xml).find("inbound/set-variable[@name='gatewayAuditSettings']").attrib['value']
  settings=json.loads(base64.b64decode(raw.split('"')[1]))
  self.assertEqual(settings['measurementRole'],'facade'); self.assertEqual(settings['llmOperationIds'],[])
 def test_effective_cm_authentication_metadata_does_not_change_the_authentication_or_route(self):
  # 実Bicepから生成したXMLを比較し、CMの監査表示だけが変わることを確認します。
  config=rate_fixture.configuration.normalize_configuration(connection_fixture(),base_directory=CORE/'config')
  api=config['apiDefinitions'][0]
  self.parameters['apiDefinitions'].append(api)
  index=len(self.parameters['apiDefinitions'])-1
  for enabled in (True,False):
   api['credentialConnection']['enabled']=enabled
   value=self.evaluate("[variables('policyValues')[copyIndex()]]",index)
   root=ET.fromstring(value)
   node=root.find("inbound/set-variable[@name='gatewayAuditSettings']")
   settings=json.loads(base64.b64decode(node.attrib['value'].split('"')[1]))
   self.assertEqual(settings['backendAuthentication'],'credentialConnection')
   self.assertIsNotNone(root.find("inbound/choose/when/include-fragment[@fragment-id='core-credential-connection']"))
  # CMを持たないHTTP、native protocol、外側MCPの表示は基底の認証方式を保ちます。
  connection=api.pop('credentialConnection')
  settings=self.evaluate("[__bicep.auditSettings(parameters('apiDefinitions')[copyIndex()], '', false())]",index)
  self.assertEqual(settings['backendAuthentication'],api['backendAuthentication']['mode'])
  api['credentialConnection']=connection
  settings=self.evaluate("[__bicep.auditSettings(parameters('apiDefinitions')[copyIndex()], 'read-profile', true())]",index)
  self.assertEqual(settings['backendAuthentication'],api['backendAuthentication']['mode'])
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
  service=ET.fromstring((CORE/'apps/gateway/apim/policies/service.xml').read_text()
    .replace('__L7_INGRESS_REQUIRED__','false').replace('__L7_INGRESS_EXPECTED__',''))
  native_count=0
  for index,api in enumerate(self.parameters['apiDefinitions']):
   if api['apiType'] not in ('websocket','grpc'):continue
   root=ET.fromstring(self.evaluate("[variables('policyValues')[copyIndex()]]",index))
   graph=self.expanded_fragment_graph(root)
   self.assertTrue({'core-audit-context','core-backend-audit','core-gateway-telemetry','core-rate-limit'} <= set(graph))
   self.assertNotIn('core-audit-body-model',graph)
   self.assert_body_free_nodes({api['name']:root,'inherited-service':service,**graph})
   native_count+=1
  self.assertGreaterEqual(native_count,2)
 def test_http_extracts_model_before_common_audit_and_mcp_does_not_import_that_reader(self):
  for index,api in enumerate(self.parameters['apiDefinitions']):
   root=ET.fromstring(self.evaluate("[variables('policyValues')[copyIndex()]]",index))
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
  facade=ET.fromstring(self.evaluate("[variables('mcpPolicyValues')[copyIndex()]]"))
  graph=self.expanded_fragment_graph(facade)
  self.assertNotIn('core-audit-body-model',graph)
  self.assert_body_free_nodes({'generated-mcp':facade,**graph})

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
  source=(CORE/'infra/modules/services.bicep').read_text()
  self.assertIn("category: 'GatewayLlmLogs', enabled: true",source)
  self.assertIn("logAnalyticsDestinationType: 'Dedicated'",source)
 def test_only_explicit_api_registration_writes_api_contracts(self):
  main=(CORE/'infra/main.bicep').read_text(); publisher=(CORE/'apps/gateway/deploy/api_publication.py').read_text()
  self.assertNotIn('api-publication.bicep',main)
  self.assertNotIn('apiDefinitions: resolvedApis',main)
  self.assertIn('apps/gateway/apim/api-publication.bicep',publisher)
  self.assertIn('"apiDefinitions": resolved',publisher)
  self.assertEqual(self.template['resources']['auditContextFragment']['name'],"[format('{0}/{1}', parameters('names').apim, 'core-audit-context')]")
if __name__=='__main__': unittest.main()

"""実BicepのARM/XMLを展開し、Agent登録と本文非収集のnative診断を検証。"""
import base64
import copy
import json
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
from uuid import UUID
import xml.etree.ElementTree as ET
from tests.support.arm_helpers import PolicyExpression, compiled_policy_value
from tests.support.arm_helpers import DiscoveryExpression
from tests.support.configuration_fixtures import api_fixture, connection_fixture, configuration, pii_fixture
from deployment.helpers.diagnostics import canonical_settings
from deployment.helpers.apim_audit import CORE_CREDENTIAL_CALLER_MARKER, CORE_CREDENTIAL_USE_RESULT_MARKER, render_audit_policy
from deployment.helpers import credential_login
from deployment.helpers.apim_input_contract import input_contracts
from deployment.helpers.apim_chat_store import chat_store_disabled_operations
from tests.support.policy_fixture_paths import policy_fixture_path
from tests.support.bicep_helpers import compile_bicep
from tests.support.rate_publication import rate_publication_fixture
from tests.support.powershell import PWSH
from tests.support.policy_expressions import single_expression
from tests import REPOSITORY

CORE=REPOSITORY
class AuditPublicationTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.template, cls.config, cls.parameters = rate_publication_fixture()
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
  directory=CORE/'apps/api-gateway/policies'
  marker='__AUDIT_SUBJECT_OBSERVATION_CSHARP__'
  for filename,resource_name,source_marker,comment_mode in (
    ('core-api-settings.xml','apiSettingsFragment','__CORE_API_SETTINGS_CSHARP__','full'),
    ('core-api-authentication-result.xml','apiAuthenticationResultFragment','__CORE_API_AUTHENTICATION_RESULT_CSHARP__','full'),
    ('core-api-target-validation.xml','apiTargetValidationFragment','__CORE_API_TARGET_VALIDATION_CSHARP__','full'),
    ('core-chat-store-disabled.xml','chatStoreDisabledFragment','__CORE_CHAT_STORE_DISABLED_CSHARP__','full'),
    ('core-model-limits.xml','modelLimitsFragment','__CORE_MODEL_LIMITS_CSHARP__','full'),
    ('core-rate-limit.xml','rateLimitFragment','__CORE_RATE_LIMIT_CSHARP__','full'),
    ('core-token-limits.xml','tokenLimitFragment','__CORE_TOKEN_LIMITS_CSHARP__','full'),
    ('core-entra-user-profile.xml','entraUserProfileFragment','__CORE_ENTRA_USER_PROFILE_CSHARP__','full'),
    ('core-document-input-validation.xml','documentInputValidationFragment','__CORE_DOCUMENT_INPUT_VALIDATION_CSHARP__','full'),
    ('core-gateway-telemetry.xml','gatewayTelemetryFragment','__CORE_GATEWAY_TELEMETRY_CSHARP__','full'),
    ('core-backend-audit.xml','backendAuditFragment','__CORE_BACKEND_AUDIT_CSHARP__','full'),
    ('core-audit-context.xml','auditContextFragment','__CORE_AUDIT_CONTEXT_TRACE_CSHARP__','full'),
    ('core-realtime-observer.xml','realtimeObserverFragment','__REALTIME_OBSERVER_OWNER_CSHARP__','full'),
    ('core-query-telemetry.xml','queryTelemetryFragment','__CORE_QUERY_TELEMETRY_CSHARP__','full')):
   with self.subTest(template=filename):
    raw=policy_fixture_path(directory,filename).read_text(encoding='utf-8')
    self.assertIn(source_marker,raw)
    expected_xml=render_audit_policy(raw,source_comments=comment_mode)
    expected=ET.fromstring(expected_xml)
    value=self.template['resources'][resource_name]['properties']['value']
    actual=self.evaluate(value) if value.startswith('[') else value
    self.assertNotIn(source_marker,actual)
    self.assertEqual(structure(ET.fromstring(actual)),structure(expected))
  raw=policy_fixture_path(directory,'credential-telemetry.xml').read_text(encoding='utf-8')
  self.assertIn('__CREDENTIAL_TELEMETRY_CSHARP__',raw)
  expected=ET.fromstring(render_audit_policy(raw,source_comments='compact'))
  actual=self.evaluate("[variables('credentialGatewayTelemetryBody')]")
  self.assertNotIn(marker,actual)
  self.assertEqual(structure(ET.fromstring('<fragment>'+actual+'</fragment>')),structure(expected))
  expected_messages=[structure(node) for node in expected.findall('.//trace[@source="core-gateway"]')]
  self.assertTrue(expected_messages)
  for resource_name,filename in (('credentialConnectionFragment','credential-connection.xml'),
    ('credentialSessionUseFragment','core-credential-session-use.xml'),
    ('credentialConnectionOutboundFragment','credential-connection-outbound.xml')):
   with self.subTest(fragment=resource_name):
    source=ET.fromstring(render_audit_policy(policy_fixture_path(directory,filename).read_text(encoding='utf-8'),source_comments='compact'))
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
 def test_compiled_api_and_protected_source_apply_user_limits_after_authentication(self):
  def check(value):
   root=ET.fromstring(value)
   inbound=root.find('inbound')
   children=list(inbound)
   selector=next(node for node in inbound.findall('choose')
                 if node.find("when/include-fragment[@fragment-id='core-entra-user-profile']") is not None)
   self.assertIn('["method"] == "user"',selector.find('when').get('condition'))
   rate=inbound.find("include-fragment[@fragment-id='core-rate-limit']")
   self.assertLess(children.index(selector),children.index(rate))
  for index,api in enumerate(self.parameters['apiDefinitions']):
   with self.subTest(api=api['name'],source='deployed'):
    check(compiled_policy_value(self.template,self.parameters,index))
  parameters={name:definition['defaultValue'] for name,definition in self.template['parameters'].items()
              if 'defaultValue' in definition}
  parameters.update(self.parameters)
  reader=DiscoveryExpression('[true()]',self.template['variables'],functions=self.template['functions'])
  policies=reader.resolve(self.template['outputs']['entraPublicationSource']['value']['policies'],parameters)
  for api in self.parameters['apiDefinitions']:
   suffix='/apis/'+api['name']+('/operations/onHandshake' if api['apiType']=='websocket' else '')+'/policies/policy'
   matches=[entry for entry in policies if entry['suffix']==suffix]
   with self.subTest(api=api['name'],source='protected-source'):
    self.assertEqual(len(matches),1)
    check(matches[0]['value'])
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
    self.assertEqual(set(controls),{'operationId','operationConstraints','tokenLimitOperationIds','speechSsmlOperationIds','chatStoreDisabledOperationIds','queryObservations','mcpQueryObservations','inputContracts'})
    self.assertEqual(controls['operationId'],'')
    self.assertEqual(controls['operationConstraints'],api['authorization']['operationConstraints'])
    self.assertEqual(controls['inputContracts'],input_contracts(api))
    self.assertEqual(controls['chatStoreDisabledOperationIds'],chat_store_disabled_operations(api))
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
    'operationConstraints':api['authorization']['operationConstraints'],'tokenLimitOperationIds':[], 'speechSsmlOperationIds':[], 'chatStoreDisabledOperationIds':[],
    'queryObservations':{},'mcpQueryObservations':{method+' /mcp':api['mcp']['queryObservation'] for method in ('POST','GET','DELETE')} if 'queryObservation' in api['mcp'] else {},
    'inputContracts':{}})
 def test_hosted_store_rewrite_reaches_additional_and_mixed_http_apis(self):
  """合法な追加Hostedの配備と読戻し用sourceへ、同じ本文編集と先行拒否順を渡す。"""
  raw_policy=(CORE/'apps/api-gateway/policies/api-templates/rest/rest.xml').read_text(encoding='utf-8')
  source=ET.fromstring(render_audit_policy(raw_policy))
  expected=source.find("inbound/choose[@id='core-hosted-response-metadata']")
  self.assertIsNotNone(expected)
  def structure(node):
   return (node.tag,tuple(sorted(node.attrib.items())),(node.text or '').strip(),
           tuple(structure(child) for child in node))
  for label,url,target in (
   ('named-agent','https://agent.example.com/agents/synthetic-agent','synthetic-agent'),
   ('unnamed-agent','https://agent.example.com/gateway',''),
  ):
   with self.subTest(case=label):
    raw=api_fixture()
    api=raw['apiDefinitions'][0]
    api.update(name='synthetic-hosted-'+label,backendService='external',backendUrl=url,
               backendAudience='https://ai.azure.com',backendAuthentication={'mode':'managedIdentity'})
    first=copy.deepcopy(api['operations'][0])
    first.update(name='hosted-first',urlTemplate='/responses')
    alternate=copy.deepcopy(first)
    alternate.update(name='hosted-override',urlTemplate='/alternate/responses',
      backendOverride={'backendService':'external','backendUrl':'https://override.example.com/gateway',
                       'backendAudience':'https://ai.azure.com','backendAuthentication':{'mode':'managedIdentity'}})
    neighbor=copy.deepcopy(first)
    neighbor.update(name='neighbor',urlTemplate='/neighbor')
    api['operations']=[first,alternate,neighbor]
    api['authorization']['operationConstraints']={
     name:{'kind':kind,'bodyFormat':'json','maxRequestBytes':2048}
     for name,kind in (('hosted-first','hostedAgentPost'),('hosted-override','hostedAgentPost'),
                       ('neighbor','registeredOperation'))}
    api=configuration.normalize_configuration(raw,base_directory=CORE/'infra/config')['apiDefinitions'][0]
    self.parameters['apiDefinitions']=[api]
    deployed=ET.fromstring(compiled_policy_value(self.template,self.parameters))
    _,audit,controls=self.api_settings(deployed)
    self.assertEqual(audit['targetAgent'],target)
    self.assertEqual({name:value['kind'] for name,value in controls['operationConstraints'].items()},
                     {'hosted-first':'hostedAgentPost','hosted-override':'hostedAgentPost','neighbor':'registeredOperation'})
    reader=DiscoveryExpression('[true()]',self.template['variables'],functions=self.template['functions'])
    parameters={name:definition['defaultValue'] for name,definition in self.template['parameters'].items()
                if 'defaultValue' in definition}
    parameters.update(self.parameters)
    policies=reader.resolve(self.template['outputs']['entraPublicationSource']['value']['policies'],parameters)
    matches=[entry for entry in policies if entry['suffix']=='/apis/'+api['name']+'/policies/policy']
    self.assertEqual(len(matches),1)
    for published in (deployed,ET.fromstring(matches[0]['value'])):
     inbound=list(published.find('inbound'))
     rewrite=published.find("inbound/choose[@id='core-hosted-response-metadata']")
     self.assertEqual(structure(rewrite),structure(expected))
     self.assertEqual(len(rewrite.findall('when/set-body')),1)
     limits=published.find("inbound/include-fragment[@fragment-id='core-model-limits']")
     validation=published.find("inbound/choose[@id='core-api-target-validation-selection']")
     rate=published.find("inbound/include-fragment[@fragment-id='core-rate-limit']")
     self.assertLess(inbound.index(limits),inbound.index(validation))
     self.assertLess(inbound.index(validation),inbound.index(rewrite))
     self.assertLess(inbound.index(rewrite),inbound.index(rate))
 def test_token_scope_follows_normalized_operation_backend_overrides(self):
  """正規化した追加APIから実配備XMLを生成し、overrideとトークン枠の対象を照合します。"""
  foundry={'backendService':'foundryOpenAi','backendAudience':'https://cognitiveservices.azure.com',
           'backendAuthentication':{'mode':'managedIdentity'}}
  external={'backendService':'external','backendUrl':'https://chat.example.com/v1',
            'backendAudience':'https://chat.example.com','backendAuthentication':{'mode':'none'}}
  diagnostic_logs=self.template['resources']['apiDiagnostics']['properties']['largeLanguageModel']['logs']
  cases=(
   ('foundry-chat-override','chat','foundryCognitive',foundry,True,True),
   ('foundry-responses-override','responses','foundryCognitive',foundry,True,False),
   ('external-chat-override','chat','foundryOpenAi',external,False,False),
  )
  for label,kind,default_backend,override,selected,native_selected in cases:
   with self.subTest(case=label):
    raw=api_fixture()
    api=raw['apiDefinitions'][0]
    operation=api['operations'][0]
    constraint=api['authorization']['operationConstraints'][operation['name']]
    api['backendService']=default_backend
    operation['backendOverride']=copy.deepcopy(override)
    if kind=='chat':
     operation['urlTemplate']='/chat/completions'
     constraint.update(kind='bodyModel',outputTokenParameter='max_completion_tokens')
    api=configuration.normalize_configuration(raw,base_directory=CORE/'infra/config')['apiDefinitions'][0]
    # 公開fixtureと同じく、Core接続先だけ独立した合成URLへ解決します。
    api.setdefault('backendUrl','https://backend.example.invalid')
    api['operations'][0]['backendOverride'].setdefault('backendUrl','https://backend.example.invalid')
    self.parameters['apiDefinitions']=[api]
    expected=[operation['name']] if selected else []
    self.assertEqual(self.evaluate("[__bicep.tokenLimitOperations(parameters('apiDefinitions')[copyIndex()])]"),expected)
    root=ET.fromstring(compiled_policy_value(self.template,self.parameters))
    _,audit,controls=self.api_settings(root)
    self.assertEqual(controls['tokenLimitOperationIds'],expected)
    expected_native=[operation['name']] if native_selected else []
    self.assertEqual(audit['llmOperationIds'],expected_native)
    self.assertEqual(self.evaluate(diagnostic_logs),'enabled' if native_selected else 'disabled')
    self.assertEqual(controls['chatStoreDisabledOperationIds'],expected_native)
    includes=root.findall("inbound/include-fragment[@fragment-id='core-token-limits']")
    self.assertEqual(len(includes),1 if selected else 0)
    if selected:
     inbound=list(root.find('inbound'))
     profile=next(node for node in root.findall('inbound/choose')
                  if node.find("when/include-fragment[@fragment-id='core-entra-user-profile']") is not None)
     rate=root.find("inbound/include-fragment[@fragment-id='core-rate-limit']")
     self.assertIsNotNone(profile)
     self.assertIsNotNone(rate)
     self.assertLess(inbound.index(profile),inbound.index(rate))
     self.assertLess(inbound.index(rate),inbound.index(includes[0]))
  # Responsesの実効接続先制約は維持し、外部Responsesを新たに受け付けません。
  raw=api_fixture()
  raw['apiDefinitions'][0]['operations'][0]['backendOverride']=copy.deepcopy(external)
  with self.assertRaisesRegex(configuration.ConfigurationError,'statelessResponses permits only'):
   configuration.normalize_configuration(raw,base_directory=CORE/'infra/config')
  with self.subTest(case='mixed-chat-backends'):
   raw=api_fixture()
   api=raw['apiDefinitions'][0]
   operation=api['operations'][0]
   constraint=api['authorization']['operationConstraints'][operation['name']]
   operation['urlTemplate']='/chat/completions'
   constraint.update(kind='bodyModel',outputTokenParameter='max_completion_tokens')
   external_operation=copy.deepcopy(operation)
   external_operation.update(name='external-chat',urlTemplate='/external/chat/completions',
                             backendUrlTemplate='/chat/completions',backendOverride=copy.deepcopy(external))
   api['operations'].append(external_operation)
   api['authorization']['operationConstraints'][external_operation['name']]=copy.deepcopy(constraint)
   api=configuration.normalize_configuration(raw,base_directory=CORE/'infra/config')['apiDefinitions'][0]
   api.setdefault('backendUrl','https://backend.example.invalid')
   self.parameters['apiDefinitions']=[api]
   root=ET.fromstring(compiled_policy_value(self.template,self.parameters))
   _,audit,controls=self.api_settings(root)
   # 操作IDはFoundry Chatだけ。nativeログの診断スイッチはAPI全体で有効にします。
   expected=[operation['name']]
   self.assertEqual(audit['llmOperationIds'],expected)
   self.assertEqual(self.evaluate(diagnostic_logs),'enabled')
   self.assertEqual(controls['tokenLimitOperationIds'],expected)
   self.assertEqual(controls['chatStoreDisabledOperationIds'],expected)
   self.assertEqual(len(root.findall("inbound/include-fragment[@fragment-id='core-token-limits']")),1)
 def test_token_scope_preserves_standard_and_protocol_boundaries(self):
  apis=self.parameters['apiDefinitions']
  by_name={api['name']:index for index,api in enumerate(apis)}
  cases=(
   ('standard-chat-responses',by_name['openai-inference-v1'],['chat-completions','responses-create']),
   ('embedding',by_name['openai-embeddings-v1'],[]),
   ('images',by_name['openai-images-v1'],[]),
   ('audio',by_name['openai-audio-v1'],[]),
   ('websocket',next(index for index,api in enumerate(apis) if api['apiType']=='websocket'),[]),
   ('native-mcp',next(index for index,api in enumerate(apis) if api['apiType']=='mcp'),[]),
  )
  for label,index,expected in cases:
   with self.subTest(case=label):
    self.assertEqual(self.evaluate("[__bicep.tokenLimitOperations(parameters('apiDefinitions')[copyIndex()])]",index),expected)
    root=ET.fromstring(compiled_policy_value(self.template,self.parameters,index))
    _,_,controls=self.api_settings(root)
    self.assertEqual(controls['tokenLimitOperationIds'],expected)
    self.assertEqual(len(root.findall("inbound/include-fragment[@fragment-id='core-token-limits']")),1 if expected else 0)
  facade=ET.fromstring(compiled_policy_value(self.template,self.parameters,resource_name='mcpPolicies'))
  _,_,controls=self.api_settings(facade)
  self.assertEqual(controls['tokenLimitOperationIds'],[])
  self.assertEqual(facade.findall(".//include-fragment[@fragment-id='core-token-limits']"),[])
 def test_chat_store_scope_uses_effective_backend_and_exact_operation_declaration(self):
  source=copy.deepcopy(next(api for api in self.parameters['apiDefinitions'] if api['name']=='openai-inference-v1'))
  operation=copy.deepcopy(next(operation for operation in source['operations'] if operation['name']=='chat-completions'))
  constraint=copy.deepcopy(source['authorization']['operationConstraints'][operation['name']])
  source['operations']=[operation]
  source['authorization']['operationConstraints']={operation['name']:constraint}
  cases=(
   ('foundry',{}, {}, {},True),
   ('additional-api',{'name':'synthetic-additional-chat'}, {}, {},True),
   ('override-away',{}, {'backendOverride':{'backendService':'external'}}, {},False),
   ('override-into',{'backendService':'external'}, {'backendOverride':{'backendService':'foundryOpenAi'}}, {},True),
   ('other-backend',{'backendService':'external'}, {}, {},False),
   ('get',{}, {'method':'GET'}, {},False),
   ('prefixed-path',{}, {'urlTemplate':'/v1/chat/completions'}, {},False),
   ('trailing-path',{}, {'urlTemplate':'/chat/completions/'}, {},False),
   ('other-kind',{}, {}, {'kind':'statelessResponses'},False),
   ('websocket',{'apiType':'websocket'}, {}, {},False),
   ('mcp',{'apiType':'mcp'}, {}, {},False),
  )
  for label,api_patch,operation_patch,constraint_patch,selected in cases:
   with self.subTest(case=label):
    api=copy.deepcopy(source)
    api.update(api_patch)
    api['operations'][0].update(operation_patch)
    api['authorization']['operationConstraints'][operation['name']].update(constraint_patch)
    # 生成MCPの制御設定は、生成元のREST APIにあるmcp宣言も読みます。
    api['mcp']={'operationNames':[operation['name']]}
    if api['apiType']=='mcp':
     api['mcpProxy']=copy.deepcopy(next(item['mcpProxy'] for item in self.parameters['apiDefinitions'] if item['apiType']=='mcp'))
    index=len(self.parameters['apiDefinitions'])
    self.parameters['apiDefinitions'].append(api)
    expected=[operation['name']] if selected else []
    self.assertEqual(chat_store_disabled_operations(api),expected)
    actual=self.evaluate("[__bicep.chatStoreDisabledOperations(parameters('apiDefinitions')[copyIndex()])]",index)
    self.assertEqual(actual,expected)
    controls=self.evaluate("[__bicep.controlSettings(parameters('apiDefinitions')[copyIndex()], '', false())]",index)
    self.assertEqual(controls['chatStoreDisabledOperationIds'],expected)
    generated=self.evaluate("[__bicep.controlSettings(parameters('apiDefinitions')[copyIndex()], 'chat-completions', true())]",index)
    self.assertEqual(generated['chatStoreDisabledOperationIds'],[])
    self.assertEqual(chat_store_disabled_operations(api,generated_mcp=True),[])
 def test_path_model_rewrites_encode_path_values_and_preserve_registered_backend_and_query(self):
  """実Bicepのrewrite式をC#で動かす。HTTPの解析・APIM転送・提供元の受付は再現しない。"""
  if not PWSH: self.skipTest('PowerShell is required for actual rewrite-expression evaluation')
  generic=copy.deepcopy(next(api for api in self.parameters['apiDefinitions'] if api['name']=='openai-images-v1'))
  base_constraint=copy.deepcopy(generic['authorization']['operationConstraints']['image-edit'])
  generic.update(name='synthetic-path-model-v1',path='models/synthetic-path/v1',displayName='Synthetic path model')
  generic['operations']=[
   {'name':'invoke-model','displayName':'Synthetic invocation','method':'POST',
    'urlTemplate':'/vendors/{vendor}/models/{modelName}/invoke',
    'templateParameters':[{'name':name,'type':'string','required':True} for name in ('vendor','modelName')],
    'backendUrlTemplate':'/custom/{vendor}/models/{modelName}/invoke?api-version=preview&mode=synthetic'},
   {'name':'native-model','displayName':'Synthetic native route','method':'GET','urlTemplate':'/models/{modelName}',
    'templateParameters':[{'name':'modelName','type':'string','required':True}]},
  ]
  generic['authorization']['operationConstraints']={
   'invoke-model':{**base_constraint,'parameter':'modelName'},
   'native-model':{'kind':'pathModel','parameter':'modelName','bodyFormat':'none',
    'maxRequestBytes':32768,'allowedModels':base_constraint['allowedModels']},
  }
  self.parameters['apiDefinitions'].append(generic)
  requested={('openai-images-v1','image-edit'),('openai-audio-v1','audio-transcriptions'),
             ('synthetic-path-model-v1','invoke-model'),('synthetic-path-model-v1','native-model')}
  entries=self.evaluate("[variables('operationEntries')]")
  methods=[];cases=[];expected={};found=set()
  values=(('plain','future-synthetic-model','future-synthetic-model'),
          ('slash','future/model','future%2Fmodel'),
          ('query','future?api-version=other','future%3Fapi-version%3Dother'),
          ('percent','future%2Fmodel','future%252Fmodel'),
          ('backslash',r'future\model','future%5Cmodel'),
          ('fragment','future#fragment','future%23fragment'),
          ('unicode','合成 model','%E5%90%88%E6%88%90%20model'))
  for index,entry in enumerate(entries):
   key=(entry['apiName'],entry['operation']['name'])
   if key not in requested: continue
   found.add(key)
   operation=ET.fromstring(compiled_policy_value(self.template,self.parameters,index,resource_name='operationPolicies'))
   rewrite=operation.find('inbound/rewrite-uri')
   self.assertIsNone(operation.find('.//set-backend-service'))
   api_index=next(i for i,api in enumerate(self.parameters['apiDefinitions']) if api['name']==entry['apiName'])
   api=self.parameters['apiDefinitions'][api_index]
   self.assertEqual(self.evaluate(self.template['resources']['backends']['properties']['url'],api_index),api['backendUrl'])
   if 'backendUrlTemplate' not in entry['operation']:
    self.assertIsNone(rewrite)
    continue
   self.assertEqual(rewrite.get('copy-unmatched-params'),'false')
   route=len(methods)
   methods.append(f'case {route}: return {single_expression(rewrite.get("template"))};')
   parameter=entry['constraint']['parameter']
   template=entry['operation']['backendUrlTemplate']
   for label,value,encoded in values:
    identifier=entry['apiName']+'/'+entry['operation']['name']+'/'+label
    parameters={parameter:value}
    if key==('synthetic-path-model-v1','invoke-model'): parameters['vendor']='fixed-vendor'
    cases.append({'id':identifier,'route':route,'parameters':parameters})
    expected[identifier]=template.replace('{'+parameter+'}',encoded).replace('{vendor}','fixed-vendor')
   if key==('synthetic-path-model-v1','invoke-model'):
    identifier=entry['apiName']+'/other-parameter'
    cases.append({'id':identifier,'route':route,'parameters':{'modelName':'future-model','vendor':'vendor/other?x=1'}})
    expected[identifier]='/custom/vendor%2Fother%3Fx%3D1/models/future-model/invoke?api-version=preview&mode=synthetic'
  self.assertEqual(found,requested)
  source=r'''
using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public sealed class RewriteRequest { public Dictionary<string,string> MatchedParameters=new Dictionary<string,string>(); }
public sealed class RewriteContext { public RewriteRequest Request=new RewriteRequest(); }
public static class PathRewriteFixture {
 public static string Rewrite(RewriteContext context,int route) { switch(route) { __ROUTES__ default: throw new Exception("Unknown synthetic route"); } }
 public static string Run(string input) {
  var results=new JObject();
  foreach(JObject item in JArray.Parse(input)) {
   var context=new RewriteContext();
   foreach(var property in ((JObject)item["parameters"]).Properties()) context.Request.MatchedParameters[property.Name]=(string)property.Value;
   results[(string)item["id"]]=Rewrite(context,(int)item["route"]);
  }
  return results.ToString(Formatting.None);
 }
}'''.replace('__ROUTES__','\n'.join(methods))
  with tempfile.TemporaryDirectory(prefix='core-path-rewrite-') as directory:
   path=Path(directory)
   (path/'policy.cs').write_text(source,encoding='utf-8')
   (path/'cases.json').write_text(json.dumps(cases),encoding='utf-8')
   script=path/'run.ps1'
   script.write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[PathRewriteFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""",encoding='utf-8')
   result=subprocess.run([PWSH,'-NoLogo','-NoProfile','-NonInteractive','-File',str(script)],capture_output=True,text=True,timeout=60)
   if result.returncode: self.fail('Actual generated rewrite expressions failed offline execution:\n'+result.stderr)
   self.assertEqual(json.loads(result.stdout),expected)
 def test_effective_cm_authentication_metadata_does_not_change_the_authentication_or_route(self):
  # 実Bicepから生成したXMLを比較し、CMの監査表示だけが変わることを確認します。
  config=configuration.normalize_configuration(connection_fixture(),base_directory=CORE/'infra/config')
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
   self.assertIsNotNone(root.find("inbound/include-fragment[@fragment-id='core-credential-connection']"))
   self.assertIsNotNone(root.find("inbound/include-fragment[@fragment-id='core-credential-session-use']"))
   self.assertIsNotNone(root.find("outbound/include-fragment[@fragment-id='core-credential-connection-outbound']"))
   self.assertIsNone(root.find("outbound/choose"))
  # CMを持たないHTTP、native protocol、外側MCPの表示は基底の認証方式を保ちます。
  connection=api.pop('credentialConnection')
  plain=ET.fromstring(compiled_policy_value(self.template,self.parameters,index))
  self.assertIsNone(plain.find("outbound/include-fragment[@fragment-id='core-credential-connection-outbound']"))
  self.assertIsNone(plain.find("inbound/set-variable[@name='gatewayCredentialConnection']"))
  settings=self.evaluate("[__bicep.auditSettings(parameters('apiDefinitions')[copyIndex()], '', false())]",index)
  self.assertEqual(settings['backendAuthentication'],api['backendAuthentication']['mode'])
  api['credentialConnection']=connection
  settings=self.evaluate("[__bicep.auditSettings(parameters('apiDefinitions')[copyIndex()], 'read-profile', true())]",index)
  self.assertEqual(settings['backendAuthentication'],api['backendAuthentication']['mode'])
 def test_credential_caller_canonical_fragment_matches_http_mcp_and_protected_readback_source(self):
  """合法なCM入口から、同名正本・実配備・読戻し期待sourceの一致と先行500を確認する。"""
  directory=CORE/'apps/api-gateway/policies'
  raw_source=policy_fixture_path(directory,'core-credential-connection.xml').read_text(encoding='utf-8')
  self.assertEqual(raw_source.count(CORE_CREDENTIAL_CALLER_MARKER),1)
  session_source=policy_fixture_path(directory,'core-credential-session-use.xml').read_text(encoding='utf-8')
  self.assertEqual(session_source.count(CORE_CREDENTIAL_USE_RESULT_MARKER),1)
  expected_error=ET.parse(directory/'snippets/authentication/credential-caller-error/credential-caller-error.xml').getroot().find('choose')
  def structure(node):
   return (node.tag,tuple(sorted(node.attrib.items())),(node.text or '').strip(),
           tuple(structure(child) for child in node))
  for kind in ('http','mcp'):
   with self.subTest(api_type=kind):
    raw=connection_fixture()
    api=raw['apiDefinitions'][0]
    api.update(name='synthetic-cm-'+kind,path='tools/synthetic-cm-'+kind+'/v1',apiType=kind)
    if kind=='mcp':
     api['operations']=[{'name':'invoke','displayName':'Invoke','method':'POST',
                         'urlTemplate':'/','templateParameters':[]}]
     api['authorization']['operationConstraints']={
      'invoke':{'kind':'registeredOperation','maxRequestBytes':32768}}
     api['mcpProxy']={'transport':'streamable','endpoints':[{'name':'message','uriTemplate':'/mcp'}]}
    config=configuration.normalize_configuration(raw,base_directory=CORE/'infra/config')
    api=config['apiDefinitions'][0]
    login=credential_login.settings(config)
    self.parameters.update(apiDefinitions=[api],credentialLogin=login,publishApis=True)
    parameters={name:definition['defaultValue'] for name,definition in self.template['parameters'].items()
                if 'defaultValue' in definition}
    parameters.update(self.parameters)
    reader=DiscoveryExpression('[true()]',self.template['variables'],functions=self.template['functions'])
    source=reader.resolve(self.template['outputs']['entraPublicationSource']['value']['policies'],parameters)
    fragment_value=compiled_policy_value(self.template,self.parameters,resource_name='credentialConnectionFragment')
    resource_name=self.template['resources']['credentialConnectionFragment']['name']
    self.assertEqual(re.findall(r"'(core-[A-Za-z0-9-]+)'",resource_name),['core-credential-connection'])
    fragment_sources=[entry['value'] for entry in source if entry['suffix']=='/policyFragments/core-credential-connection']
    self.assertEqual(fragment_sources,[fragment_value])
    # ARM読取器の合成reference値と同じ非秘密principalをPython側の設定へ渡す。
    synthetic_identity=reader.resolve("[reference('synthetic-apim')]",parameters)['identity']['principalId']
    rendered=credential_login.render_connection('credential-connection.xml',
      {**login,'apimPrincipalId':synthetic_identity},telemetry='synthetic-enabled')
    self.assertNotIn(CORE_CREDENTIAL_CALLER_MARKER,fragment_value)
    fragment=ET.fromstring(fragment_value)
    self.assertEqual(structure(fragment),structure(ET.fromstring(rendered)))
    expected_caller=ET.fromstring(render_audit_policy(raw_source)).find("set-variable[@name='credentialCallerId']")
    self.assertEqual(fragment.find("set-variable[@name='credentialCallerId']").attrib,
                     expected_caller.attrib)
    # 完成fragmentにも空IDの500分岐を渡し、設定読取り・外部処理より前に戻す。
    selection=fragment.find("set-variable[@name='gatewayConnectionSelection']").get('value')
    self.assertIn('if(String.IsNullOrEmpty((string)context.Variables["credentialCallerId"])) { result["status"]=500; return result.ToString(); }',selection)
    self.assertLess(selection.index('result["status"]=500'),selection.index('Convert.FromBase64String'))
    self.assertEqual(fragment.findall(".//choose[@id='core-credential-caller-required']"),[])
    external_conditions=[node.get('condition') for node in fragment.findall('choose/when')
                         if node.find('send-request') is not None or node.find('get-authorization-context') is not None]
    self.assertEqual(external_conditions,[
     '@((bool)JObject.Parse((string)context.Variables["gatewayConnectionSelection"])["allowed"] && (string)JObject.Parse((string)context.Variables["gatewayConnectionSelection"])["mode"] == "personal")',
     '@((bool)JObject.Parse((string)context.Variables["gatewayConnectionSelection"])["allowed"])',
    ])
    authorize=fragment.find(".//send-request[@response-variable-name='gatewayConnectionSessionResponse']/set-body").text
    self.assertIn('context.Variables["gatewayConnectionRequestSession"]',authorize)
    self.assertNotIn('context.Request.Headers',authorize)
    session_value=compiled_policy_value(self.template,self.parameters,resource_name='credentialSessionUseFragment')
    session_name=self.template['resources']['credentialSessionUseFragment']['name']
    self.assertEqual(re.findall(r"'(core-[A-Za-z0-9-]+)'",session_name),['core-credential-session-use'])
    self.assertEqual([entry['value'] for entry in source if entry['suffix']=='/policyFragments/core-credential-session-use'],[session_value])
    expected_session=credential_login.render_connection('credential-session-use.xml',
      {**login,'apimPrincipalId':synthetic_identity},telemetry='synthetic-enabled')
    session_fragment=ET.fromstring(session_value)
    self.assertNotIn(CORE_CREDENTIAL_USE_RESULT_MARKER,session_value)
    self.assertEqual(structure(session_fragment),structure(ET.fromstring(expected_session)))
    outbound_value=compiled_policy_value(self.template,self.parameters,resource_name='credentialConnectionOutboundFragment')
    expected_outbound=credential_login.render_connection('credential-connection-outbound.xml',
      {**login,'apimPrincipalId':synthetic_identity},telemetry='synthetic-enabled')
    self.assertEqual(structure(ET.fromstring(outbound_value)),structure(ET.fromstring(expected_outbound)))
    self.assertEqual([entry['value'] for entry in source if entry['suffix']=='/policyFragments/core-credential-connection-outbound'],[outbound_value])
    for completed in (session_value,outbound_value):
     self.assertNotIn('__CREDENTIAL_CHALLENGE_RESULT_CSHARP__',completed)
     self.assertEqual(len(ET.fromstring(completed).findall('.//set-variable[@name="gatewayConnectionChallengeResult"]')),2)
    expected_use_result=ET.fromstring(render_audit_policy(session_source)).find(".//set-variable[@name='gatewayConnectionUseResult']")
    self.assertEqual(session_fragment.find(".//set-variable[@name='gatewayConnectionUseResult']").attrib,expected_use_result.attrib)
    # Graph本人確認の成功は先行connectionが確定し、useは同じsessionの実利用を確認する。
    self.assertNotIn('gatewayConnectionIdentityStatus',session_value)
    use=session_fragment.find("choose/when/send-request[@response-variable-name='gatewayConnectionUseResponse']")
    self.assertIsNotNone(use)
    self.assertIn('context.Variables["gatewayConnectionRequestSession"]',use.find('set-body').text)
    self.assertIsNotNone(session_fragment.find("set-header[@name='Authorization'][@exists-action='override']"))
    api_value=compiled_policy_value(self.template,self.parameters)
    api_sources=[entry['value'] for entry in source if entry['suffix']=='/apis/'+api['name']+'/policies/policy']
    self.assertEqual(api_sources,[api_value])
    for value in (api_value,*api_sources):
     published=ET.fromstring(value)
     inbound=list(published.find('inbound'))
     authentication=published.find("inbound/include-fragment[@fragment-id='core-api-authentication-result']") if kind=='http' else published.find("inbound/set-variable[@name='gatewayDecision']")
     connection=published.find("inbound/include-fragment[@fragment-id='core-credential-connection']")
     session=published.find("inbound/include-fragment[@fragment-id='core-credential-session-use']")
     caller_guard=published.find("inbound/choose[@id='core-credential-caller-required']")
     self.assertEqual(len(published.findall("inbound/include-fragment[@fragment-id='core-credential-connection']")),1)
     self.assertEqual(len(published.findall(".//choose[@id='core-credential-caller-required']")),1)
     self.assertLess(inbound.index(published.find('inbound/base')),inbound.index(authentication))
     self.assertLess(inbound.index(authentication),inbound.index(connection))
     self.assertEqual(inbound.index(caller_guard),inbound.index(connection)+1)
     self.assertEqual(inbound.index(session),inbound.index(caller_guard)+1)
     self.assertEqual(structure(caller_guard),structure(expected_error))
     caller_failure=caller_guard.find('when')
     self.assertEqual(caller_failure.get('condition'),
                      '@((int)JObject.Parse((string)context.Variables["gatewayConnectionSelection"])["status"] == 500)')
     self.assertEqual(caller_failure.find("set-variable[@name='gatewayAuditPhase']").get('value'),'policy-error')
     self.assertEqual(caller_failure.find("set-variable[@name='gatewayAuditCode']").get('value'),'AUTHENTICATION_PROCESSING_ERROR')
     self.assertEqual(caller_failure.find("set-variable[@name='gatewayAuditStatus']").get('value'),'@(500)')
     self.assertEqual(len(caller_failure.findall("include-fragment[@fragment-id='core-gateway-telemetry']")),1)
     response=caller_failure.find('return-response')
     self.assertEqual(response.find('set-status').attrib,{'code':'500','reason':'Gateway processing failed'})
     self.assertEqual(response.find("set-header[@name='apim-request-id']").get('exists-action'),'override')
     self.assertEqual(response.find("set-header[@name='apim-request-id']/value").text,'@(context.RequestId.ToString())')
     self.assertEqual(response.find("set-header[@name='Cache-Control']").get('exists-action'),'override')
     self.assertEqual(response.find("set-header[@name='Cache-Control']/value").text,'no-store')
     self.assertEqual(response.findall('set-body'),[])
     failures=[(node,branch) for node in inbound for branch in node.findall('when')
               if 'gatewayDecision' in branch.get('condition','')
               and branch.find("return-response/set-status[@code='500']") is not None]
     self.assertEqual(len(failures),1)
     guard,branch=failures[0]
     self.assertLess(inbound.index(authentication),inbound.index(guard))
     self.assertLess(inbound.index(guard),inbound.index(connection))
     self.assertEqual(branch.find("set-variable[@name='gatewayAuditPhase']").get('value'),'policy-error')
     self.assertEqual(branch.find("set-variable[@name='gatewayAuditCode']").get('value'),'AUTHENTICATION_PROCESSING_ERROR')
     self.assertIsNotNone(branch.find("include-fragment[@fragment-id='core-gateway-telemetry']"))
     setting=published.find("inbound/set-variable[@name='gatewayCredentialConnection']")
     self.assertEqual(json.loads(base64.b64decode(setting.get('value'))),api['credentialConnection'])
 def test_credential_caller_error_is_absent_from_non_cm_http_websocket_and_external_mcp(self):
  """CMなしの3転送入口へguardを持ち込まず、通常MIと生成sourceを維持する。"""
  for kind in ('http','websocket','mcp'):
   with self.subTest(api_type=kind):
    api=copy.deepcopy(next(api for api in type(self).parameters['apiDefinitions']
                          if api['apiType']==kind and 'credentialConnection' not in api
                          and api['backendAuthentication']['mode']=='managedIdentity'
                          and not any('backendOverride' in operation for operation in api['operations'])))
    self.parameters.update(apiDefinitions=[api],publishApis=True)
    parameters={name:definition['defaultValue'] for name,definition in self.template['parameters'].items()
                if 'defaultValue' in definition}
    parameters.update(self.parameters)
    reader=DiscoveryExpression('[true()]',self.template['variables'],functions=self.template['functions'])
    source=reader.resolve(self.template['outputs']['entraPublicationSource']['value']['policies'],parameters)
    value=compiled_policy_value(self.template,self.parameters)
    suffix='/apis/'+api['name']+('/operations/onHandshake' if kind=='websocket' else '')+'/policies/policy'
    self.assertEqual([entry['value'] for entry in source if entry['suffix']==suffix],[value])
    for candidate in (value,*[entry['value'] for entry in source if entry['suffix']==suffix]):
     self.assertNotIn('__CREDENTIAL_CALLER_ERROR_XML__',candidate)
     published=ET.fromstring(candidate)
     self.assertEqual(published.findall(".//choose[@id='core-credential-caller-required']"),[])
     self.assertEqual(published.findall(".//include-fragment[@fragment-id='core-credential-connection']"),[])
     self.assertEqual(published.findall(".//include-fragment[@fragment-id='core-credential-session-use']"),[])
     authentication=published.findall('inbound/authentication-managed-identity')
     self.assertEqual([node.get('resource') for node in authentication],[api['backendAudience']])
     self.assertIsNotNone(published.find("inbound/set-header[@name='Authorization'][@exists-action='delete']"))
 def test_generated_mcp_preserves_existing_cm_unsupported_boundary(self):
  """生成MCPは既存PII契約だけで、今回のCM改修から受付を広げない。"""
  raw=pii_fixture()
  config=configuration.normalize_configuration(raw,base_directory=CORE/'infra/config')
  api=config['apiDefinitions'][0]
  api.setdefault('backendUrl','https://backend.example.invalid')
  self.parameters.update(apiDefinitions=[api],publishApis=True)
  parameters={name:definition['defaultValue'] for name,definition in self.template['parameters'].items()
              if 'defaultValue' in definition}
  parameters.update(self.parameters)
  reader=DiscoveryExpression('[true()]',self.template['variables'],functions=self.template['functions'])
  source=reader.resolve(self.template['outputs']['entraPublicationSource']['value']['policies'],parameters)
  for name,resource in ((api['name'],None),(api['mcp']['name'],'mcpPolicies')):
   value=compiled_policy_value(self.template,self.parameters,resource_name=resource)
   self.assertEqual([entry['value'] for entry in source if entry['suffix']=='/apis/'+name+'/policies/policy'],[value])
   published=ET.fromstring(value)
   self.assertEqual(published.findall(".//include-fragment[@fragment-id='core-credential-connection']"),[])
   self.assertEqual(published.findall(".//include-fragment[@fragment-id='core-credential-session-use']"),[])
   self.assertEqual(published.findall(".//choose[@id='core-credential-caller-required']"),[])
   self.assertNotIn('__CREDENTIAL_CALLER_ERROR_XML__',value)
  unsupported=copy.deepcopy(raw)
  unsupported['apiDefinitions'][0]['credentialConnection']=connection_fixture()['apiDefinitions'][0]['credentialConnection']
  with self.assertRaisesRegex(ValueError,'credentialConnection'):
   configuration.normalize_configuration(unsupported,base_directory=CORE/'infra/config')
 def test_credential_management_api_inherits_without_model_api_settings(self):
  raw=self.template['resources']['credentialLoginApiPolicy']['properties']['value']
  value=self.evaluate(raw) if raw.startswith('[') else raw
  root=ET.fromstring(value)
  for section in ('inbound','backend','outbound','on-error'):
   self.assertEqual([node.tag for node in root.find(section)],['base'])
  self.assertEqual(root.findall('.//include-fragment[@fragment-id="core-api-settings"]'),[])
  self.assertEqual(root.findall('.//set-variable[@name="gatewayApiSettingsSource"]'),[])
 def test_specialized_operation_errors_match_sources_and_preserve_browser_without_new_apis(self):
  """既存9操作の認証エラーだけを先頭へ加え、Browserと公開API集合を維持する。"""
  from tests.gateway.publication.test_api_publication_size import SizeExpression
  marker='__OPERATION_ENTRA_AUTHENTICATION_ERROR_XML__'
  identifier='core-entra-authentication-error'
  browser_values={}
  def structure(node):
   return (node.tag,tuple(sorted(node.attrib.items())),(node.text or '').strip(),
           tuple(structure(child) for child in node))
  for enabled in (False,True):
   with self.subTest(entra_enabled=enabled):
    self.parameters['publishApis']=True
    self.parameters['credentialLogin']=credential_login.settings(self.config)
    self.assertTrue(self.parameters['credentialLogin'])
    self.parameters['entraAuthentication']={'enabled':enabled}
    parameters={name:definition['defaultValue'] for name,definition in self.template['parameters'].items()
                if 'defaultValue' in definition}
    parameters.update(self.parameters)
    parameters['names']={'apim':'apim-test-fixture'}
    reader=SizeExpression('[true()]',self.template['variables'],functions=self.template['functions'])
    source=reader.resolve(self.template['outputs']['entraPublicationSource']['value'],parameters)
    browser=reader.resolve(self.template['outputs']['credentialBrowserPublicationSource']['value'],parameters)
    source_policies={entry['suffix']:entry['value'] for entry in source['policies']}
    browser_policies={entry['suffix']:entry['value'] for entry in browser['policies']}
    browser_values[enabled]=browser_policies
    self.assertEqual({api['name'] for api in source['apis']},
      {api['name'] for api in self.parameters['apiDefinitions']}
      | {api['mcp']['name'] for api in self.parameters['apiDefinitions'] if 'mcp' in api}
      | {'credential-manager-login'})
    self.assertEqual('/policyFragments/'+identifier in source_policies,enabled)
    error_resource=self.template['resources']['entraAuthenticationErrorFragment']
    self.assertEqual(reader.resolve(error_resource['condition'],parameters),enabled)
    for value in (*source_policies.values(),*browser_policies.values()):
     self.assertNotIn(marker,value)
    selected=[]
    counts={kind:0 for kind in ('pii','documentAnalysis','documentAnalysisStatus')}
    entries=reader.resolve("[variables('operationEntries')]",parameters)
    for index,entry in enumerate(entries):
     value=compiled_policy_value(self.template,parameters,index,resource_name='operationPolicies')
     suffix='/apis/'+entry['apiName']+'/operations/'+entry['operation']['name']+'/policies/policy'
     self.assertEqual(source_policies[suffix],value)
     if entry['constraint']['kind'] in counts:
      counts[entry['constraint']['kind']]+=1
      selected.append((suffix,value))
     else:
      self.assertEqual(ET.fromstring(value).findall('on-error/include-fragment[@fragment-id="'+identifier+'"]'),[])
     dependencies=reader.resolve(self.template['resources']['operationPolicies']['dependsOn'],parameters,index)
     if entry['constraint']['kind'] in counts:
      self.assertIn('entraAuthenticationErrorFragment',dependencies)
    self.assertEqual(counts,{'pii':1,'documentAnalysis':1,'documentAnalysisStatus':1})
    login_entries=reader.resolve("[variables('credentialLoginOperations')]",parameters)
    fixture_settings={**parameters['credentialLogin'],'apimPrincipalId':reader.resolve(
      "[reference('fixture').identity.principalId]",parameters)}
    for index,entry in enumerate(login_entries):
     value=compiled_policy_value(self.template,parameters,index,resource_name='credentialLoginOperationPolicy')
     suffix='/apis/'+entry['apiId']+'/operations/'+entry['name']+'/policies/policy'
     protected=entry['apiId']=='credential-manager-login'
     self.assertEqual((source_policies if protected else browser_policies)[suffix],value)
     if protected:
      expected=credential_login.render(entry['name']+'.xml',fixture_settings,entra_enabled=enabled)
      self.assertEqual(structure(ET.fromstring(value)),structure(ET.fromstring(expected)))
      selected.append((suffix,value))
     else:
      self.assertEqual(ET.fromstring(value).findall('on-error/include-fragment[@fragment-id="'+identifier+'"]'),[])
     dependencies=reader.resolve(self.template['resources']['credentialLoginOperationPolicy']['dependsOn'],parameters,index)
     if protected:
      self.assertIn('entraAuthenticationErrorFragment',dependencies)
    self.assertEqual(len(selected),9)
    for suffix,value in selected:
     with self.subTest(operation=suffix):
      root=ET.fromstring(value)
      includes=root.findall('on-error/include-fragment[@fragment-id="'+identifier+'"]')
      self.assertEqual(len(includes),int(enabled))
      self.assertEqual(len(root.findall('.//include-fragment[@fragment-id="'+identifier+'"]')),int(enabled))
      if enabled:
       self.assertIs(list(root.find('on-error'))[0],includes[0])
       self.assertEqual(includes[0].attrib,{'id':identifier,'fragment-id':identifier})
  self.assertEqual(set(browser_values),{False,True})
  self.assertEqual(browser_values[False],browser_values[True])
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
  service=ET.fromstring(render_audit_policy((CORE/'apps/api-gateway/policies/service.xml').read_text()
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
  self.assertEqual([node.tag for node in branch],
                   ['set-variable','set-variable','set-variable','include-fragment','return-response'])
  self.assertEqual({node.get('name'):node.get('value') for node in branch.findall('set-variable')},
                   {'gatewayAuditPhase':'rejected','gatewayAuditCode':'INVALID_MCP_TOOL_ARGUMENTS','gatewayAuditStatus':'@(400)'})
  self.assertEqual(branch.find('include-fragment').get('fragment-id'),'core-gateway-telemetry')
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
   expected=api['apiType']=='http' and any(
    o.get('backendOverride',api)['backendService']=='foundryOpenAi'
    and o['urlTemplate'].endswith('/chat/completions')
    and api['authorization']['operationConstraints'][o['name']]['kind']=='bodyModel' for o in api['operations'])
   self.assertEqual(llm,{'logs':'enabled' if expected else 'disabled'})
   for direction in ('frontend','backend'):
    for phase in ('request','response'): self.assertEqual(actual[direction][phase]['body']['bytes'],0)
  for resource in ('globalDiagnostic','mcpDiagnostics'):
   self.assertEqual(self.template['resources'][resource]['properties']['largeLanguageModel'],{'logs':'disabled'})
  config=configuration.normalize_configuration(connection_fixture(),base_directory=CORE/'infra/config')
  properties=canonical_settings(config,'core')['apim-logs']['properties']
  self.assertIn({'category':'GatewayLlmLogs','enabled':True},properties['logs'])
  self.assertEqual(properties['logAnalyticsDestinationType'],'Dedicated')
  services=compile_bicep('infra/modules/services/services.bicep')
  diagnostic=next(item for item in services['resources']
   if item.get('type')=='Microsoft.Insights/diagnosticSettings' and item.get('name')=='core-gateway-logs'
    and "names').apim" in item.get('scope',''))
  self.assertEqual(diagnostic['properties'],"[parameters('diagnosticSettingProperties')['apim-logs']]")

 def test_only_explicit_api_registration_writes_api_contracts(self):
  main=(CORE/'infra/main.bicep').read_text(); publisher=(CORE/'apps/api-gateway/deploy/api_publication.py').read_text()
  self.assertNotIn('api-publication.bicep',main)
  self.assertNotIn('apiDefinitions: resolvedApis',main)
  self.assertIn('apps/api-gateway/api-publication.bicep',publisher)
  self.assertIn('"apiDefinitions": resolved',publisher)
  self.assertEqual(self.template['resources']['auditContextFragment']['name'],"[format('{0}/{1}', parameters('names').apim, 'core-audit-context')]")
if __name__=='__main__': unittest.main()

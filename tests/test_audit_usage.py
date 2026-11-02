"""公式APIM標準列のfixture。欠落/矛盾/重複と親候補を合成データだけで検査。"""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_usage import gateway_events, join_usage
A='/subscriptions/fixture/resourceGroups/core/providers/Microsoft.ApiManagement/service/core'
P='11111111-1111-4111-8111-111111111111'; C='22222222-2222-4222-8222-222222222222'
T='44444444-4444-4444-8444-444444444444'; O='55555555-5555-4555-8555-555555555555'; Z='66666666-6666-4666-8666-666666666666'
def gateway(rid=C,**changes):
 return {**dict(resourceId=A,requestId=rid,authorization='allowed',phase='backend-response',measurementRole='execution',
  apiId='models',contractId='agent-contract',callerId='agent:approved-agent',allocationId='agent-allocation',verifiedTenantId=T,
  verifiedPrincipalId=O,verifiedClientId=Z,targetModel='approved-model',usageSource='ApiManagementGatewayLlmLog'),**changes}
def provider(**changes):
 return {'_ResourceId':A,'CorrelationId':C,'RequestId':'chatcmpl-fixture','DeploymentName':'approved-model',
  'PromptTokens':12,'CompletionTokens':4,'TotalTokens':16,**changes}
def run(rows,usage): return join_usage(rows,usage,apim_resource_id=A)
class AuditUsageTests(unittest.TestCase):
 def test_strict_correlation_model_resource_and_deduplicate_phases(self):
  g=gateway(); accepted={**g,'phase':'accepted'}
  row=run([g,accepted,g],[provider(),provider()])[0]
  self.assertEqual(row['usage'],{'promptTokens':12,'completionTokens':4,'totalTokens':16})
  self.assertEqual(row['providerRequestId'],'chatcmpl-fixture'); self.assertEqual(row['usageStatus'],'available')
  self.assertEqual(len(run([g,accepted,g],[provider(),provider()])),1)
  for change in ({'_ResourceId':A+'other'},{'CorrelationId':P},{'DeploymentName':'other-model'},{'DeploymentName':'Approved-model'}):
   self.assertEqual(run([g],[provider(**change)])[0]['usageStatus'],'none')
  self.assertEqual(run([{**g,'resourceId':A+'other'}],[provider()]),[])
  self.assertEqual(run([gateway(requestId=C.upper())],[provider(CorrelationId=C.upper())])[0]['usageStatus'],'available')
 def test_absent_invalid_conflicting_or_no_metrics_protocol_never_becomes_zero(self):
  for value in (None,[],[12],True,-1,1.5,'12',float('inf'),10**20):
   row=run([gateway()],[provider(PromptTokens=value)])[0]
   self.assertEqual(row['usageStatus'],'none'); self.assertNotIn('usage',row)
  for p in (provider(TotalTokens=17),provider(PromptTokens=0,CompletionTokens=0,TotalTokens=0),provider(RequestId='invalid private body!')):
   self.assertEqual(run([gateway()],[p])[0]['usageStatus'],'none')
  self.assertEqual(run([gateway()],[provider(),provider(PromptTokens=13,TotalTokens=17)])[0]['usageStatus'],'ambiguous-usage')
  self.assertEqual(run([gateway()],[provider(),provider(RequestId='chatcmpl-second')])[0]['usageStatus'],'ambiguous-usage')
  self.assertEqual(run([gateway(usageSource='none')],[provider()])[0]['usageStatus'],'none')
  self.assertEqual(run([gateway()],[])[0]['usageStatus'],'none')
 def test_parent_candidate_requires_exact_registered_target_identity_keeps_child_contract(self):
  parent=gateway(P,apiId='agent-api',targetModel=None,usageSource='none',targetAgent='approved-agent',targetAgentBindingStatus='configured',
    targetAgentTenantId=T,targetAgentPrincipalId=O,targetAgentClientId=Z,verifiedPrincipalId=P,contractId='person-contract')
  child=gateway(parentLinkStatus='candidate',claimedParentId=P,claimedAgentName='approved-agent')
  row=next(x for x in run([parent,child],[provider()]) if x['requestId']==C)
  self.assertEqual(row['parentTraceStatus'],'matched-candidate'); self.assertEqual(row['contractId'],'agent-contract'); self.assertEqual(row['verifiedPrincipalId'],O)
  for field,value in [('targetAgentPrincipalId',P),('targetAgentClientId',P),('targetAgent','other-agent'),('targetAgentBindingStatus','ambiguous')]:
   row=next(x for x in run([{**parent,field:value},child],[provider()]) if x['requestId']==C)
   self.assertEqual(row['parentTraceStatus'],'unmatched')
 def test_ambiguous_request_is_retained_without_identity_usage_and_no_facade_doublecount(self):
  g=gateway(); row=run([g,{**g,'contractId':'different'}],[provider()])[0]
  self.assertEqual(row['usageStatus'],'ambiguous-request'); self.assertNotIn('usage',row); self.assertNotIn('contractId',row)
  self.assertEqual(run([{**g,'measurementRole':'facade'},{**g,'measurementRole':'observation'}],[provider()]),[])
 def test_trace_adapter_needs_own_schema_and_matching_top_level_id_not_source(self):
  record={**gateway(),'schemaVersion':1,'event':'gateway-request'}
  for field in ('message','Message'):
   logs=[{'_ResourceId':A,'CorrelationId':C.upper(),'TraceRecords':[{field:json.dumps(record)}]}]
   self.assertEqual(gateway_events(logs,apim_resource_id=A)[0]['requestId'],C)
  for change in ({'schemaVersion':True},{'schemaVersion':2},{'event':'other'},{'requestId':P}):
   logs=[{'_ResourceId':A,'CorrelationId':C,'TraceRecords':[{'message':json.dumps({**record,**change})}]}]
   self.assertEqual(gateway_events(logs,apim_resource_id=A),[])
 def test_actual_result_and_allowed_backend_ids_are_retained_without_claiming_accepted_is_finished(self):
  g=gateway(phase='backend-response',status=503,code='BACKEND_RESPONSE',backendResponseIds={'apim-request-id':P})
  row=run([g],[])[0]
  self.assertEqual((row['phase'],row['status'],row['code']),('backend-response',503,'BACKEND_RESPONSE'))
  self.assertEqual(row['backendResponseIds'],{'apim-request-id':P})
  row=run([gateway(phase='accepted',status=200)],[])[0]
  self.assertIsNone(row['status'])
 def test_http_model_and_opaque_protocol_keep_distinct_usage_and_agent_parent_after_split(self):
  """HTTPのモデル名は利用量へ結合し、nativeの空モデルへ他要求の数値を流用しない。"""
  native_id='77777777-7777-4777-8777-777777777777'
  parent=gateway(P,apiId='agent-api',targetModel=None,usageSource='none',targetAgent='approved-agent',
    targetAgentBindingStatus='configured',targetAgentTenantId=T,targetAgentPrincipalId=O,targetAgentClientId=Z,
    verifiedPrincipalId=P,contractId='person-contract')
  child=gateway(parentLinkStatus='candidate',claimedParentId=P,claimedAgentName='approved-agent')
  native=gateway(native_id,apiId='native-grpc',contractId='native-contract',targetModel=None,usageSource='none')
  result=run([parent,child,{**child,'phase':'accepted'},native],[provider(),provider(CorrelationId=native_id)])
  self.assertEqual(len(result),3)
  model=next(x for x in result if x['requestId']==C)
  self.assertEqual(model['usage'],{'promptTokens':12,'completionTokens':4,'totalTokens':16})
  self.assertEqual(model['parentTraceStatus'],'matched-candidate')
  self.assertEqual(model['contractId'],'agent-contract')
  opaque=next(x for x in result if x['requestId']==native_id)
  self.assertEqual(opaque['contractId'],'native-contract')
  self.assertEqual(opaque['usageStatus'],'none')
  self.assertNotIn('usage',opaque)

 def test_key_usage_remains_subscription_scoped_and_parent_claim_is_unverified(self):
  row=gateway(subscriptionId='app-a',method='subscriptionKey',callerId='subscription:app-a',
    verifiedTenantId=None,verifiedPrincipalId=None,verifiedClientId=None,parentLinkStatus='candidate',claimedParentId=P)
  observed=run([row],[provider()])[0]
  self.assertEqual(observed['subscriptionId'],'app-a')
  self.assertEqual(observed['parentTraceStatus'],'unverified-candidate')
  other={**row,'subscriptionId':'app-b','callerId':'subscription:app-b'}
  self.assertEqual(run([row,other],[])[0]['usageStatus'],'ambiguous-request')
 def test_query_uses_official_columns_bag_set_and_no_unsupported_source_or_payload(self):
  query=(Path(__file__).resolve().parents[1]/'scripts/audit-usage.kql').read_text()
  for token in ['_ResourceId =~ apimResourceId','ApiManagementGatewayLlmLog','DeploymentName','PromptTokens','CompletionTokens','TotalTokens',
      'on requestId, targetModel',"'ambiguous-request'",'requestAmbiguous',"measurementRole) == 'execution'",'make_set(bag_pack(',
      'parentPrincipal==tostring(record.verifiedPrincipalId)', 'trace.Message', 'toguid(v)', 'canonicalId(tostring(CorrelationId))','long(null)']:
   self.assertIn(token,query)
  # Azure KQLのdynamic引数へint列を直接渡すとSEM0270になるため、数値型を保つ境界を固定する。
  for column in ('PromptTokens','CompletionTokens','TotalTokens'):
   self.assertIn('boundedNumber(pack_array('+column+')[0])',query)
   self.assertNotIn('boundedNumber('+column+')',query)
  for forbidden in ['RequestMessages','ResponseMessages','RequestBody','ResponseBody','RequestHeaders','ResponseHeaders','sum(',
      'make_set(pack_array','trace.source','AzureOpenAIRequestUsage','properties_s']:
   self.assertNotIn(forbidden,query)
if __name__=='__main__': unittest.main()

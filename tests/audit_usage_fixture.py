"""本文を扱わない監査joinのローカルfixture。実運用queryはaudit-usage.kql。

同じAPIM資源/CorrelationId/配備名と明示された標準LLM列だけを採用する。
ログの欠落はゼロではない。相関は追跡候補であり本人/契約の書換えに使わない。
"""
from collections import defaultdict
import json
import math
import re
import uuid

MAX_NUMBER = 9_007_199_254_740_991
GUID = re.compile(r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
MODEL = re.compile(r'^[A-Za-z0-9_.-]{1,160}$')
PROVIDER_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$')
IDENTITY_FIELDS = ('subscriptionId', 'method', 'contractId', 'callerId', 'allocationId', 'verifiedTenantId', 'verifiedPrincipalId', 'verifiedClientId')
TRACE_FIELDS = (*IDENTITY_FIELDS, 'apiId', 'targetModel', 'targetAgent', 'usageSource', 'targetAgentBindingStatus',
    'targetAgentTenantId', 'targetAgentPrincipalId', 'targetAgentClientId', 'parentLinkStatus', 'claimedParentId', 'claimedAgentName')


def request_id(value):
    if not isinstance(value, str) or not GUID.fullmatch(value): return None
    parsed = uuid.UUID(value)
    return str(parsed) if parsed.int else None


def number(value):
    """標準LLM列の整数だけ。文字列・配列・bool・小数から推測変換しない。"""
    if type(value) not in (int, float) or not 0 <= value <= MAX_NUMBER or not math.isfinite(value) or int(value) != value:
        return None
    return int(value)


def gateway_events(logs, *, apim_resource_id):
    """TraceRecordsはdynamic。message/Messageを明示対応し、自前JSONと上位IDで識別する。"""
    events = []
    for log in logs:
        rid = request_id(log.get('CorrelationId'))
        if not rid or str(log.get('_ResourceId', '')).casefold() != apim_resource_id.casefold(): continue
        for trace in log.get('TraceRecords', []):
            if not isinstance(trace, dict): continue
            try: record = json.loads(trace.get('message') or trace.get('Message') or '')
            except (ValueError, TypeError): continue
            if not isinstance(record, dict) or type(record.get('schemaVersion')) is not int or record['schemaVersion'] != 1: continue
            if record.get('event') != 'gateway-request' or request_id(record.get('requestId')) != rid: continue
            events.append({**record, 'requestId': rid, 'resourceId': apim_resource_id})
    return events


def join_usage(gateway_records, llm_records, *, apim_resource_id):
    """標準CorrelationIdはGateway要求ID、RequestIdは言語モデル側ID。両者を混同しない。"""
    grouped = defaultdict(list)
    for row in gateway_records:
        rid = request_id(row.get('requestId'))
        if (rid and str(row.get('resourceId', '')).casefold() == apim_resource_id.casefold()
            and row.get('authorization') == 'allowed' and row.get('phase') in ('accepted', 'backend-response')
            and row.get('measurementRole') == 'execution'):
            grouped[rid].append(row)
    requests = {}
    for rid, rows in grouped.items():
        variants = {tuple(row.get(k) for k in TRACE_FIELDS) for row in rows}
        if len(variants) != 1:
            requests[rid] = {'requestId': rid, 'usageStatus': 'ambiguous-request', 'parentTraceStatus': 'none'}
            continue
        chosen = next((row for row in rows if row.get('phase') == 'backend-response'), rows[0])
        requests[rid] = {k: v for k, v in chosen.items() if k in TRACE_FIELDS}
        requests[rid].update(requestId=rid, usageStatus='none', parentTraceStatus='none')
        requests[rid].update(phase=chosen.get('phase'), code=chosen.get('code'), status=chosen.get('status') if chosen.get('phase') == 'backend-response' else None, backendResponseIds=chosen.get('backendResponseIds', {}))
    usage = defaultdict(set)
    for row in llm_records:
        if str(row.get('_ResourceId', '')).casefold() != apim_resource_id.casefold(): continue
        rid, model, provider_id = request_id(row.get('CorrelationId')), row.get('DeploymentName'), row.get('RequestId')
        prompt, completion, total = (number(row.get(k)) for k in ('PromptTokens', 'CompletionTokens', 'TotalTokens'))
        if (rid and isinstance(model, str) and MODEL.fullmatch(model)
            and isinstance(provider_id, str) and PROVIDER_ID.fullmatch(provider_id)
            and prompt is not None and completion is not None and total is not None and total > 0 and total == prompt + completion):
            usage[(rid, model)].add((provider_id, prompt, completion, total))
    for rid, row in requests.items():
        if row.get('usageSource') == 'ApiManagementGatewayLlmLog':
            matches = usage.get((rid, row.get('targetModel')), set())
            if len(matches) == 1:
                provider_id, prompt, completion, total = next(iter(matches))
                row.update(usageStatus='available', providerRequestId=provider_id,
                    usage={'promptTokens': prompt, 'completionTokens': completion, 'totalTokens': total})
            elif len(matches) > 1: row['usageStatus'] = 'ambiguous-usage'
        parent = requests.get(request_id(row.get('claimedParentId')))
        if row.get('parentLinkStatus') == 'candidate':
            matching = bool(parent and all(request_id(row.get(field)) for field in ('verifiedTenantId', 'verifiedPrincipalId', 'verifiedClientId'))
                and parent.get('usageStatus') != 'ambiguous-request'
                and parent.get('targetAgentBindingStatus') == 'configured' and parent.get('targetAgent') == row.get('claimedAgentName')
                and parent.get('targetAgentTenantId') == row.get('verifiedTenantId')
                and parent.get('targetAgentPrincipalId') == row.get('verifiedPrincipalId')
                and parent.get('targetAgentClientId') == row.get('verifiedClientId'))
            row['parentTraceStatus'] = 'matched-candidate' if matching else ('unverified-candidate' if row.get('method') == 'subscriptionKey' else 'unmatched')
            if matching: row['traceParentRequestId'] = parent['requestId']
        row.pop('claimedParentId', None)
    return list(requests.values())

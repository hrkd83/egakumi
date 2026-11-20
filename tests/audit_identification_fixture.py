"""独立した合成datatableと期待値。query本文は正本から読み、Azureへ接続しない。"""
import sys
sys.dont_write_bytecode = True
import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUERY = ROOT / 'scripts/audit-identification.kql'
APIM = '/subscriptions/REPLACE/resourceGroups/REPLACE/providers/Microsoft.ApiManagement/service/REPLACE'
APPGW = '/subscriptions/REPLACE/resourceGroups/REPLACE/providers/Microsoft.Network/applicationGateways/REPLACE'
TENANT = '11111111-1111-4111-8111-111111111111'
PRINCIPAL = '22222222-2222-4222-8222-222222222222'
CLIENT = '33333333-3333-4333-8333-333333333333'
TIME = '2026-01-01T01:00:00Z'

def request_id(number):
    return '10000000-0000-4000-8000-' + f'{number:012d}'

def trace_id(number):
    return '20000000-0000-4000-8000-' + f'{number:012d}'

def key_subject():
    return {'status': 'verified', 'kind': 'apimSubscription', 'method': 'subscriptionKey',
            'callerId': 'subscription:synthetic-subscription', 'subscriptionId': 'synthetic-subscription'}

def user_subject(principal=PRINCIPAL):
    caller = 'user:' + TENANT + ':' + principal
    return {'status': 'verified', 'kind': 'entraUser', 'method': 'user', 'callerId': caller,
            'tenantId': TENANT, 'principalId': principal, 'clientId': CLIENT, 'allocationId': caller}

def context(number, **changes):
    value = {'requestId': request_id(number), 'apiId': 'synthetic-api', 'operationId': 'synthetic-operation',
        'requestReceivedAt': TIME, 'httpMethod': 'POST', 'operationTemplate': '/synthetic/{item}',
        'apimPeerIp': '192.0.2.10', 'reportedUserAgent': 'Synthetic-Audit/1.0', 'appGatewayTraceId': trace_id(number)}
    value.update(changes)
    return value

def event(number, subject=None, *, kind='gateway-request', phase='accepted', ctx=None, version=2, schema_version=1):
    value = {'schemaVersion': schema_version, 'event': kind, 'requestId': request_id(number),
        'apiId': 'synthetic-api', 'operationId': 'synthetic-operation', 'phase': phase, 'code': 'SYNTHETIC', 'status': 200}
    if kind == 'query-guard':
        value.pop('code'); value.pop('status')
        value.update(route='liveCreate', httpMethod='POST', presence='present',
                     duplicates='no', statusClass='unknown' if phase == 'received' else '2xx')
        # 旧保存ログだけが持つ項目です。新版の生成式/出力へ戻すための設定ではありません。
        if type(schema_version) is int and schema_version == 1:
            value['countBucket'] = '1'
            value['mode'] = 'observe'
    if version is not None:
        value.update(auditSchemaVersion=version, requestContext=context(number) if ctx is None else ctx,
                     authenticatedSubject={'status': 'unverified'} if subject is None else subject)
    return {'message': json.dumps(value, separators=(',', ':'))}

def fixture():
    """GUID/IP/主体は実環境から独立した合成値。全件をnative要求単位で期待する。"""
    native, appgw, expected = [], [], {}
    def add(number, events, *, subject_status='verified', context_status='available', correlation_status='matched', status=200, header=None):
        rid = request_id(number)
        headers = {'x-appgw-trace-id': [trace_id(number) if header is None else header]}
        native.append([TIME, APIM, rid, 'synthetic-api', 'synthetic-operation', 'POST', status, status, headers, events])
        appgw.append([TIME, APPGW, trace_id(number).replace('-', ''), '192.0.2.20', 'Synthetic-Audit/1.0', status, status])
        expected[rid] = {'authenticatedSubjectStatus': subject_status, 'requestContextStatus': context_status,
                         'appGatewayCorrelationStatus': correlation_status, 'rows': 1}
    add(1, [event(1, kind='query-guard', phase='received'), event(1, key_subject()), event(1, key_subject(), phase='backend-response')])
    expected[request_id(1)].update(callerId='subscription:synthetic-subscription')
    add(2, [event(2, user_subject()), event(2, user_subject(), phase='backend-response')])
    expected[request_id(2)].update(callerId=user_subject()['callerId'], tenantId=TENANT, principalId=PRINCIPAL, clientId=CLIENT)
    add(3, [], subject_status='unverified', context_status='missing', status=401)
    legacy = event(4, version=None)
    record = json.loads(legacy['message']); record.update(authorization='allowed', verifiedTenantId=TENANT, verifiedPrincipalId=PRINCIPAL)
    legacy['message'] = json.dumps(record)
    add(4, [legacy], subject_status='unverified', context_status='missing')
    unverified = user_subject(); unverified['status'] = 'unverified'
    add(5, [event(5, unverified)], subject_status='unverified')
    other = '44444444-4444-4444-8444-444444444444'
    add(6, [event(6, user_subject()), event(6, user_subject(other), phase='backend-response')], subject_status='inconsistent')
    add(7, [event(7, key_subject()), event(7, {'status': 'inconsistent'}, phase='backend-response')], subject_status='inconsistent')
    malformed = key_subject(); malformed['callerId'] = 'subscription:synthetic-other'
    add(8, [event(8, malformed)], subject_status='inconsistent')
    add(9, [event(9, key_subject()), event(9, key_subject(), ctx=context(9, apimPeerIp='192.0.2.11'))], context_status='inconsistent')
    add(10, [event(10, key_subject())], correlation_status='unmatched'); appgw.pop()
    add(11, [event(11, phase='rejected')], subject_status='unverified', status=403)
    add(12, [event(12, key_subject())], correlation_status='ambiguous-log')
    altered = copy.deepcopy(appgw[-1]); altered[3] = '192.0.2.21'; appgw.append(altered)
    add(13, [event(13, key_subject()), event(13, key_subject(), phase='backend-response')])
    native.append(copy.deepcopy(native[-1]))
    bad_link = event(14, key_subject())
    record = json.loads(bad_link['message']); record['requestId'] = request_id(99); bad_link['message'] = json.dumps(record)
    add(14, [bad_link], subject_status='inconsistent', context_status='inconsistent')
    native.append([TIME, APIM, '', '', '', 'GET', 401, 0, {}, []])
    expected[''] = {'authenticatedSubjectStatus': 'unverified', 'requestContextStatus': 'missing',
                    'appGatewayCorrelationStatus': 'no-candidate', 'rows': 1}
    add(16, [event(16, key_subject())], header=trace_id(88), correlation_status='inconsistent-candidate')
    add(17, [event(17, key_subject(), ctx=context(17, reportedUserAgent=CLIENT))])
    expected[request_id(17)].update(reportedUserAgent=CLIENT)
    add(18, [event(18, key_subject(), ctx=context(18, reportedUserAgent=None, apimPeerIp=None))])
    expected[request_id(18)].update(reportedUserAgent='', apimPeerIp='')
    no_client = user_subject(); no_client['clientId'] = None
    add(19, [event(19, no_client, ctx=context(19, apimPeerIp='2001:db8::5'))])
    expected[request_id(19)].update(clientId='', apimPeerIp='2001:db8::5')
    add(20, [event(20, key_subject(), ctx=context(20, operationTemplate='/' + 's' * 512))], context_status='inconsistent')
    zero = user_subject('00000000-0000-0000-0000-000000000000')
    add(21, [event(21, zero)], subject_status='inconsistent')
    add(22, [event(22, key_subject(), ctx=context(22, reportedUserAgent='2026-01-01T01:00:00Z'))])
    expected[request_id(22)].update(reportedUserAgent='2026-01-01T01:00:00Z')
    add(23, [event(23, key_subject()), event(23, key_subject(), ctx=context(23, appGatewayTraceId=trace_id(99)))],
        context_status='inconsistent', correlation_status='inconsistent-candidate')
    add(24, [event(24, key_subject(), ctx=context(24, reportedUserAgent=42))], context_status='inconsistent')
    add(25, [event(25, key_subject(), ctx=context(25, reportedUserAgent='42'))])
    expected[request_id(25)].update(reportedUserAgent='42')
    add(26, [event(26, key_subject(), ctx=context(26, reportedUserAgent='true'))])
    expected[request_id(26)].update(reportedUserAgent='true')
    add(27, [event(27, key_subject(), ctx=context(27, apimPeerIp='synthetic.invalid'))], context_status='inconsistent')
    add(28, [event(28, key_subject(), ctx=context(28, reportedUserAgent='Synthetic "quoted" \\ agent'))])
    expected[request_id(28)].update(reportedUserAgent='Synthetic "quoted" \\ agent')
    add(29, [event(29, key_subject())], correlation_status='ambiguous-log')
    appgw[-1][4] = 's' * 512 + 'a'
    altered = copy.deepcopy(appgw[-1]); altered[4] = 's' * 512 + 'b'; appgw.append(altered)
    expected[request_id(29)].update(appGatewayDistinctVariants=2)
    add(30, [event(30, key_subject(), ctx=context(30, requestReceivedAt='2026-01-01T10:00:00+09:00'))],
        context_status='inconsistent')
    expected[request_id(30)].update(malformedContextCount=1)
    # queryの保存済み形式1と新版形式2を同じ識別queryで扱うための合成入力/期待値。
    # このfixtureはKQLを準備するだけです。下の期待値を実engineの検証結果とは扱いません。
    add(31, [event(31, key_subject(), kind='query-guard', phase='received', schema_version=1)])
    expected[request_id(31)].update(callerId='subscription:synthetic-subscription')
    add(32, [event(32, user_subject(), kind='query-guard', phase='received', schema_version=2)])
    expected[request_id(32)].update(callerId=user_subject()['callerId'], tenantId=TENANT, principalId=PRINCIPAL, clientId=CLIENT)
    add(33, [event(33, key_subject(), kind='query-guard', schema_version=3)],
        subject_status='unverified', context_status='missing')
    add(34, [event(34, key_subject(), schema_version=2)], subject_status='unverified', context_status='missing')
    add(35, [event(35, user_subject(), kind='query-guard', phase='received', schema_version=1),
             event(35, user_subject(), kind='query-guard', phase='backend-response', schema_version=2)])
    expected[request_id(35)].update(callerId=user_subject()['callerId'], tenantId=TENANT, principalId=PRINCIPAL, clientId=CLIENT)
    add(36, [event(36, key_subject(), kind='query-guard', schema_version=True)],
        subject_status='unverified', context_status='missing')
    return {'native': native, 'appgw': appgw, 'expected': expected,
            'expectedRows': len(expected), 'syntheticDataOnly': True}

def literal(value, kind):
    if kind == 'datetime':
        return 'datetime(' + value + ')'
    if kind == 'dynamic':
        return 'dynamic(' + json.dumps(value, ensure_ascii=True, separators=(',', ':')) + ')'
    if kind == 'string':
        return json.dumps(value)
    return str(value)

def datatable(name, columns, rows):
    schema = ','.join(column + ':' + kind for column, kind in columns)
    values = ['  ' + ','.join(literal(value, kind) for value, (_, kind) in zip(row, columns)) for row in rows]
    return 'let ' + name + '=datatable(' + schema + ')[\n' + ',\n'.join(values) + '\n];\n'

def prepare_query():
    data = fixture()
    columns = [('TimeGenerated', 'datetime'), ('_ResourceId', 'string'), ('CorrelationId', 'string'),
        ('ApiId', 'string'), ('OperationId', 'string'), ('Method', 'string'), ('ResponseCode', 'int'),
        ('BackendResponseCode', 'int'), ('RequestHeaders', 'dynamic'), ('TraceRecords', 'dynamic')]
    agw_columns = [('TimeGenerated', 'datetime'), ('_ResourceId', 'string'), ('TransactionId', 'string'),
        ('ClientIp', 'string'), ('UserAgent', 'string'), ('HttpStatus', 'int'), ('ServerStatus', 'int')]
    source = QUERY.read_text(encoding='utf-8')
    source = re.sub(r'\bApiManagementGatewayLogs\b', 'SyntheticNative', source)
    source = re.sub(r'\bAGWAccessLogs\b', 'SyntheticAppGateway', source)
    source = source.replace('let startTime = ago(24h);', 'let startTime = datetime(2026-01-01T00:00:00Z);')
    source = source.replace('let endTime = now();', 'let endTime = datetime(2026-01-02T00:00:00Z);')
    prefix = datatable('SyntheticNative', columns, data['native']) + datatable('SyntheticAppGateway', agw_columns, data['appgw'])
    return {'query': prefix + source, 'expected': data['expected'], 'expectedRows': data['expectedRows'], 'syntheticDataOnly': True}

if __name__ == '__main__':
    result = prepare_query()
    print(json.dumps({'prepared': True, 'cases': result['expectedRows'], 'kqlEngineExecuted': False, 'azureWrites': 0}))

"""合成datatableへ配備正本KQLを渡す。Azureへ接続せず、秘密・実ログを扱わない。

このファイルの標準出力をLog Analyticsで評価すると、各ケースを初回／5秒後に
検証する。PassedとProjectionSafeが全行trueであることを確認する。
ローカルでの生成成功は、KQLエンジンでの実行成功を意味しない。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUERY = ROOT / 'infra/modules/audit-write-failures.kql'
EVALUATION_TIME = datetime(2030, 1, 1, tzinfo=timezone.utc)
ACCOUNTS = ['fixturecollecteast', 'fixturecollectwest', '']
FIELDS = ['TimeGenerated', '_ResourceId', 'AccountName', 'OperationName', 'StatusCode', 'StatusText', 'Uri']


def cases():
    """個別の期待値を持つ13系統の境界・負例。識別子は独立した架空値。"""
    result = []

    def case(name, expected, changes=(), *, failed_at=-10000, status='404', text='BlobNotFound',
             operation='AppendBlock', account=ACCOUNTS[0], uri=None, next_expected=None):
        uri = uri if uri is not None else f'https://storage.example.invalid/audit/{name}.json'
        events = [{'offsetMs': failed_at, '_ResourceId': name, 'AccountName': account,
                   'OperationName': operation, 'StatusCode': status, 'StatusText': text,
                   'Uri': uri, 'availableAfterMs': 0}]
        for delta, op, overrides in changes:
            events.append({**events[0], 'offsetMs': failed_at + delta, 'OperationName': op,
                           'StatusCode': '201', 'StatusText': 'Success', **overrides})
        result.append({'name': name, 'expected': expected,
                       'nextExpected': expected if next_expected is None else next_expected, 'events': events})

    recovered = [(100, 'PutBlob', {}), (200, 'AppendBlock', {})]
    case('ordered-recovery', 0, recovered)
    case('put-only', 1, recovered[:1])
    case('append-only', 1, recovered[1:])
    case('append-before-create', 1, [(100, 'AppendBlock', {}), (200, 'PutBlob', {})])
    case('unrecovered', 1)
    case('forbidden-is-immediate', 1, recovered, failed_at=-1000, status='403', text='AuthorizationFailure')
    case('server-error-is-immediate', 1, recovered, failed_at=-1000, status='500', text='InternalError')
    case('other-not-found-is-immediate', 1, recovered, failed_at=-1000, text='ContainerNotFound')
    case('other-operation-is-immediate', 1, recovered, failed_at=-1000, operation='PutBlock')
    case('different-blob', 1, [(100, 'PutBlob', {'Uri': 'https://storage.example.invalid/audit/other.json'}),
                               (200, 'AppendBlock', {'Uri': 'https://storage.example.invalid/audit/other.json'})])
    case('different-account', 1, [(delta, op, {'AccountName': ACCOUNTS[1]}) for delta, op, _ in recovered])
    case('path-case-difference', 1, [(delta, op, {'Uri': 'https://storage.example.invalid/audit/PATH.json'})
                                    for delta, op, _ in recovered], uri='https://storage.example.invalid/audit/path.json')
    case('empty-uri-is-immediate', 1, recovered, failed_at=-1000, uri='')
    # Empty account is deliberately included in this fixture's Accounts argument only.
    # It must not turn empty keys into a matching initialization sequence.
    case('empty-account-is-immediate', 1, recovered, failed_at=-1000, account='')
    case('query-only-uri-is-immediate', 1, recovered, failed_at=-1000, uri='?operation=append')
    case('query-does-not-identify-blob', 0,
         [(100, 'PutBlob', {'Uri': 'https://storage.example.invalid/audit/query.json?operation=create'}),
          (200, 'AppendBlock', {'Uri': 'https://storage.example.invalid/audit/query.json?operation=append'})],
         uri='https://storage.example.invalid/audit/query.json?operation=failed')
    case('exact-five-seconds', 0, [(100, 'PutBlob', {}), (5000, 'AppendBlock', {})])
    case('five-seconds-plus-one-ms', 1, [(100, 'PutBlob', {}), (5001, 'AppendBlock', {})])
    case('create-after-deadline', 1, [(5001, 'PutBlob', {}), (5002, 'AppendBlock', {})])
    case('create-before-failure', 1, [(-1, 'PutBlob', {}), (100, 'AppendBlock', {})])
    case('mature-window-start', 1, failed_at=-905000, next_expected=0)
    case('mature-window-start-recovered', 0, recovered, failed_at=-905000)
    case('before-mature-window', 0, failed_at=-905001)
    case('exact-maturity', 1, failed_at=-5000)
    case('new-candidate-pending', 0, failed_at=-4999, next_expected=1)
    case('late-ingestion-may-alert', 1, [(100, 'PutBlob', {}),
         (200, 'AppendBlock', {'availableAfterMs': 5000})], next_expected=0)
    case('same-time-forbidden-survives', 1, recovered + [(0, 'AppendBlock',
         {'StatusCode': '403', 'StatusText': 'AuthorizationFailure'})])
    case('same-blob-later-unrecovered', 1, recovered + [(2000, 'AppendBlock',
         {'StatusCode': '404', 'StatusText': 'BlobNotFound'})])
    return result


def build_query():
    """正本関数を複製せず読み込み、合成データと期待件数で実KQL評価を準備する。"""
    fixtures = cases()
    rows = []
    expected = []
    for case in fixtures:
        for event in case['events']:
            timestamp = EVALUATION_TIME + timedelta(milliseconds=event['offsetMs'])
            rows.append('    datetime(' + timestamp.isoformat() + '), ' + ', '.join(
                json.dumps(event[field]) for field in FIELDS[1:]) + ', ' + str(event['availableAfterMs']))
        for phase, count in [('initial', case['expected']), ('after-5s', case['nextExpected'])]:
            expected.append('    ' + ', '.join([json.dumps(phase), json.dumps(case['name']), str(count)]))
    events = ('let FixtureEvents = datatable(TimeGenerated:datetime, _ResourceId:string, AccountName:string, '
              'OperationName:string, StatusCode:string, StatusText:string, Uri:string, AvailableAfterMs:long) [\n'
              + ',\n'.join(rows) + '\n];\n')
    golden = 'let Expected = datatable(Phase:string, _ResourceId:string, ExpectedCount:long) [\n' + ',\n'.join(expected) + '\n];\n'
    projected = ', '.join(FIELDS)
    invocation = f'''let Initial = materialize(AuditWriteFailures(
    (FixtureEvents | where AvailableAfterMs <= 0 | project {projected}),
    datetime({EVALUATION_TIME.isoformat()}), dynamic({json.dumps(ACCOUNTS)})));
let Later = AuditWriteFailures(
    (FixtureEvents | where AvailableAfterMs <= 5000 | project {projected}),
    datetime({(EVALUATION_TIME + timedelta(seconds=5)).isoformat()}), dynamic({json.dumps(ACCOUNTS)}));
let ProjectionSafe = toscalar(Initial | getschema
    | summarize Columns=count(), Unexpected=countif(ColumnName !in
        ("TimeGenerated", "_ResourceId", "OperationName", "StatusCode", "StatusText"))
    | project Columns == 5 and Unexpected == 0);
let Observed = union (Initial | extend Phase="initial"), (Later | extend Phase="after-5s")
    | summarize ActualCount=count() by Phase, _ResourceId;
Expected
| join kind=leftouter (Observed) on Phase, _ResourceId
| extend ActualCount=coalesce(ActualCount, 0)
| project Phase, CaseId=_ResourceId, ExpectedCount, ActualCount,
    Passed=ExpectedCount == ActualCount, ProjectionSafe
| order by CaseId asc, Phase asc
'''
    return QUERY.read_text(encoding='utf-8') + '\n' + events + golden + invocation


if __name__ == '__main__':
    print(build_query(), end='')

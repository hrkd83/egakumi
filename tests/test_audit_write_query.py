"""監査Blobの初期化境界を合成データで確認し、実KQL評価用queryを準備する。

Pythonの参照判定はfixture期待値の独立検査であり、KQL実行の代わりではない。
同じfixtureと配備正本関数を使う実エンジン評価は、生成queryをAzureで実行する。
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
import unittest

from audit_write_query_fixture import ACCOUNTS, EVALUATION_TIME, QUERY, build_query, cases
from bicep_helpers import compile_bicep


def reference_counts(fixtures, advance_ms):
    """単純な列挙で初期化の存在を判定するfixture用oracle（配備コードではない）。"""
    now = EVALUATION_TIME + timedelta(milliseconds=advance_ms)
    mature_end = now - timedelta(seconds=5)
    mature_start = mature_end - timedelta(minutes=15)
    allowed_accounts = {account.casefold() for account in ACCOUNTS}
    writes = []
    for case in fixtures:
        for event in case['events']:
            timestamp = EVALUATION_TIME + timedelta(milliseconds=event['offsetMs'])
            if (event['availableAfterMs'] <= advance_ms and mature_start <= timestamp <= now
                    and event['AccountName'].casefold() in allowed_accounts
                    and event['OperationName'] in {'PutBlob', 'AppendBlock', 'PutBlock', 'PutBlockList'}):
                writes.append({**event, 'timestamp': timestamp})
    counts = Counter()
    for failure in writes:
        if int(failure['StatusCode']) < 400:
            continue
        key = failure['AccountName'].casefold(), failure['Uri'].split('?', 1)[0]
        candidate = (failure['OperationName'], failure['StatusCode'], failure['StatusText']) == (
            'AppendBlock', '404', 'BlobNotFound') and all(key)
        if candidate:
            if failure['timestamp'] > mature_end:
                continue
            deadline = failure['timestamp'] + timedelta(seconds=5)
            same_blob_successes = [event for event in writes
                if event['StatusCode'] == '201'
                and (event['AccountName'].casefold(), event['Uri'].split('?', 1)[0]) == key]
            creates = [event['timestamp'] for event in same_blob_successes
                       if event['OperationName'] == 'PutBlob'
                       and failure['timestamp'] <= event['timestamp'] <= deadline]
            appends = [event['timestamp'] for event in same_blob_successes
                       if event['OperationName'] == 'AppendBlock']
            if any(created <= appended <= deadline for created in creates for appended in appends):
                continue
        elif failure['timestamp'] <= now - timedelta(minutes=15):
            continue
        counts[failure['_ResourceId']] += 1
    return counts


class AuditWriteQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = cases()
        cls.compiled = compile_bicep('infra/modules/monitoring-bindings.bicep')

    def test_fixture_golden_counts_cover_current_and_later_observations(self):
        for advance_ms, expectation in [(0, 'expected'), (5000, 'nextExpected')]:
            actual = reference_counts(self.fixtures, advance_ms)
            for case in self.fixtures:
                with self.subTest(case=case['name'], advance_ms=advance_ms):
                    self.assertEqual(actual[case['name']], case[expectation])

    def test_fixture_contains_exact_time_account_path_and_recovery_boundaries(self):
        by_name = {case['name']: case for case in self.fixtures}
        self.assertEqual(len(by_name), len(self.fixtures))
        self.assertEqual(len(by_name), 28)
        self.assertEqual(by_name['exact-five-seconds']['events'][-1]['offsetMs'], -5000)
        self.assertEqual(by_name['five-seconds-plus-one-ms']['events'][-1]['offsetMs'], -4999)
        self.assertEqual(by_name['mature-window-start']['events'][0]['offsetMs'], -905000)
        for name in ['forbidden-is-immediate', 'server-error-is-immediate', 'empty-uri-is-immediate',
                     'empty-account-is-immediate', 'other-not-found-is-immediate', 'other-operation-is-immediate']:
            self.assertEqual(by_name[name]['events'][0]['offsetMs'], -1000)
            self.assertEqual(by_name[name]['expected'], 1)
        self.assertEqual(by_name['new-candidate-pending']['expected'], 0)
        self.assertEqual(by_name['new-candidate-pending']['nextExpected'], 1)
        self.assertEqual(by_name['late-ingestion-may-alert']['expected'], 1)
        self.assertEqual(by_name['late-ingestion-may-alert']['nextExpected'], 0)

    def test_compiled_alert_uses_canonical_function_with_existing_account_scope(self):
        variables = self.compiled['variables']
        canonical = QUERY.read_text(encoding='utf-8')
        self.assertEqual(sum(value == canonical for value in variables.values()), 1)
        query = variables['auditWriteQuery']
        self.assertIn('AuditWriteFailures(StorageBlobLogs, now(), dynamic(["{1}", "{2}"]))', query)
        self.assertIn("parameters('storage').collectJapan.name", query)
        self.assertIn("parameters('storage').collectFoundry.name", query)
        definition = next(row for row in variables['queryDefinitions'] if row['key'] == 'audit-write-failed')
        self.assertEqual(definition, {'key': 'audit-write-failed', 'query': "[variables('auditWriteQuery')]",
            'range': 'PT30M', 'aggregation': 'Count', 'measure': '', 'threshold': 0, 'severity': 2})
        alerts = next(row for row in self.compiled['resources']
                      if row['type'] == 'Microsoft.Insights/scheduledQueryRules')
        self.assertEqual(alerts['properties']['overrideQueryTimeRange'],
                         "[variables('queryDefinitions')[copyIndex()].range]")
        self.assertEqual(alerts['properties']['evaluationFrequency'], 'PT5M')
        self.assertIs(alerts['properties']['autoMitigate'], True)

    def test_supported_outer_range_is_the_smallest_covering_the_internal_window(self):
        # ScheduledQueryRules validates this discrete service contract beyond the ARM schema.
        supported_minutes = (5, 10, 15, 30, 45, 60, 120, 180, 240, 300, 360, 720, 1440, 2880)
        required = timedelta(minutes=15, seconds=5)
        minimum = min(value for value in supported_minutes if timedelta(minutes=value) >= required)
        definition = next(row for row in self.compiled['variables']['queryDefinitions']
                          if row['key'] == 'audit-write-failed')
        selected = definition['range']
        self.assertTrue(selected.startswith('PT') and selected.endswith('M'))
        selected_minutes = int(selected[2:-1])
        self.assertIn(selected_minutes, supported_minutes)
        self.assertEqual(selected_minutes, minimum)
        self.assertGreater(timedelta(minutes=selected_minutes), required)
        # The larger API range must not enlarge the function's mature event window.
        fixtures = {case['name']: case for case in self.fixtures}
        for advance_ms in (0, 5000):
            counts = reference_counts(self.fixtures, advance_ms)
            self.assertEqual(counts['before-mature-window'], 0)
        self.assertEqual(fixtures['mature-window-start']['events'][0]['offsetMs'], -905000)
        self.assertEqual(fixtures['before-mature-window']['events'][0]['offsetMs'], -905001)

    def test_actual_kql_fixture_reuses_function_and_has_only_synthetic_inputs(self):
        query = build_query()
        self.assertTrue(query.startswith(QUERY.read_text(encoding='utf-8')))
        self.assertEqual(query.count('let AuditWriteFailures = ('), 1)
        self.assertIn('let FixtureEvents = datatable(', query)
        self.assertIn('Passed=ExpectedCount == ActualCount, ProjectionSafe', query)
        self.assertNotIn('StorageBlobLogs', query)
        self.assertNotIn('ingestion_time()', query)
        for case in self.fixtures:
            for event in case['events']:
                self.assertIn(event['AccountName'], ACCOUNTS)
                if event['Uri'] and not event['Uri'].startswith('?'):
                    self.assertTrue(event['Uri'].startswith('https://storage.example.invalid/'))
        self.assertLess(len(query.encode('utf-8')), 32_768)


if __name__ == '__main__':
    unittest.main()

"""SQL保守の固定DB、暗号化、秘密非出力を独立した合成値で検証する。"""
from datetime import datetime
from decimal import Decimal
import io
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from operations import management_client as client
from test_management_client import OPERATOR_ID, fixture


def inventory():
    value = fixture()
    base = value['managementVmId'].split('/providers/', 1)[0]
    value['sqlDatabases'] = [{'alias': alias, 'id': base + '/providers/Microsoft.Sql/servers/sql-fixture/databases/sqldb-fixture-' + alias}
                             for alias in ('credentials', 'portal')]
    return value


class Cursor:
    def __init__(self, results=None):
        self.results = results or [(['value'], [(1,)])]
        self.index, self.offset = 0, 0
        self.query = None

    @property
    def description(self):
        columns = self.results[self.index][0]
        return None if columns is None else [(name,) for name in columns]

    @property
    def rowcount(self):
        return len(self.results[self.index][1])

    def execute(self, query):
        self.query = query

    def fetchone(self):
        rows = self.results[self.index][1]
        if self.offset >= len(rows):
            return None
        result = rows[self.offset]
        self.offset += 1
        return result

    def nextset(self):
        if self.index + 1 == len(self.results):
            return False
        self.index += 1
        self.offset = 0
        return True


class ManagementSqlClientTests(unittest.TestCase):
    def setUp(self):
        self.inventory = inventory()
        self.identity = mock.Mock()
        self.identity.token.return_value = 'synthetic-token'
        self.driver = mock.Mock()
        self.driver.drivers.return_value = [client.SQL_DRIVER]
        self.connection = self.driver.connect.return_value
        self.cursor = Cursor()
        self.connection.cursor.return_value = self.cursor
        self.output = io.BytesIO()

    def query(self, query='SELECT 1', **kwargs):
        client.sql_query(self.inventory, 'credentials', query, self.identity, self.output, driver=self.driver, **kwargs)

    def test_only_constructed_aliases_are_selectable_before_identity_or_connection(self):
        for alias in ('master', 'other', 'tcp:foreign.invalid', None):
            with self.subTest(alias=alias), self.assertRaises(client.MaintenanceError):
                client.sql_query(self.inventory, alias, 'SELECT 1', self.identity, self.output, driver=self.driver)
        self.inventory['sqlDatabases'] = []
        with self.assertRaises(client.MaintenanceError):
            self.query()
        self.identity.token.assert_not_called()
        self.driver.connect.assert_not_called()

    def test_system_foreign_duplicate_and_connection_string_targets_are_rejected(self):
        for mutation in ('master', 'foreign-subscription', 'duplicate-alias', 'duplicate-db', 'semicolon'):
            value = inventory()
            if mutation == 'master': value['sqlDatabases'][0]['id'] = value['sqlDatabases'][0]['id'].rsplit('/', 1)[0] + '/master'
            elif mutation == 'foreign-subscription': value['sqlDatabases'][0]['id'] = value['sqlDatabases'][0]['id'].replace(value['subscriptionId'], '99999999-9999-4999-8999-999999999999')
            elif mutation == 'duplicate-alias': value['sqlDatabases'][1]['alias'] = 'credentials'
            elif mutation == 'duplicate-db': value['sqlDatabases'][1]['id'] = value['sqlDatabases'][0]['id']
            else: value['sqlDatabases'][0]['id'] += ';TrustServerCertificate=yes'
            with self.subTest(mutation=mutation), self.assertRaises(client.MaintenanceError):
                client.validate_inventory(value)

    def test_token_buffer_encrypted_proxy_connection_and_pooling_are_fixed(self):
        self.query()
        self.identity.token.assert_called_once_with(client.SQL_AUDIENCE)
        arguments = self.driver.connect.call_args
        self.assertEqual(arguments.args, ('Driver={ODBC Driver 18 for SQL Server};Server=tcp:sql-fixture.database.windows.net,1433;Database=sqldb-fixture-credentials;Encrypt=yes;TrustServerCertificate=no;',))
        self.assertEqual(arguments.kwargs['attrs_before'], {1256: struct.pack('<I', len('synthetic-token'.encode('utf-16-le'))) + 'synthetic-token'.encode('utf-16-le')})
        self.assertFalse(arguments.kwargs['autocommit'])
        self.assertFalse(self.driver.pooling)
        self.connection.commit.assert_called_once()
        self.connection.close.assert_called_once()
        self.connection.rollback.assert_not_called()
        self.assertEqual(json.loads(self.output.getvalue())['resultSets'][0]['rows'], [[1]])

    def test_missing_driver_fails_without_attempting_an_unapproved_fallback(self):
        self.driver.drivers.return_value = ['ODBC Driver 17 for SQL Server']
        with self.assertRaises(client.MaintenanceError): self.query()
        self.driver.connect.assert_not_called()

    def test_bounded_batches_reject_invalid_input_before_token(self):
        for query, kwargs in (('', {}), ('SELECT\x001', {}), ('SELECT 1', {'seconds': 301}),
                              ('SELECT 1', {'max_rows': 0}), ('SELECT 1', {'max_rows': True})):
            with self.subTest(query=query, kwargs=kwargs), self.assertRaises(client.MaintenanceError):
                self.query(query, **kwargs)
        self.identity.token.assert_not_called()

    def test_bounded_result_failure_rolls_back_without_partial_output(self):
        self.cursor = Cursor([(['value'], [(1,), (2,)])])
        self.connection.cursor.return_value = self.cursor
        with self.assertRaisesRegex(client.MaintenanceError, 'row count'):
            self.query(max_rows=1)
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()
        self.connection.close.assert_called_once()
        self.assertEqual(self.output.getvalue(), b'')

    def test_multiple_read_write_result_sets_commit_together_and_encode_values(self):
        self.cursor = Cursor([(None, []), (['decimal', 'time', 'binary'], [(Decimal('1.25'), datetime(2025, 1, 1), b'\x01\x02')])])
        self.connection.cursor.return_value = self.cursor
        self.query('UPDATE synthetic SET value=1; SELECT value FROM synthetic')
        result = json.loads(self.output.getvalue())
        self.assertEqual(result['resultSets'][1]['rows'], [['1.25', '2025-01-01T00:00:00', {'base64': 'AQI='}]])
        self.connection.commit.assert_called_once()

    def test_sql_errors_do_not_expose_query_token_or_driver_response(self):
        self.connection.cursor.side_effect = RuntimeError('synthetic-driver-secret-response')
        with self.assertRaises(client.MaintenanceError) as failure:
            self.query('SELECT synthetic-query-secret')
        self.assertNotIn('secret', str(failure.exception))
        self.assertNotIn('synthetic-token', str(failure.exception))
        self.assertEqual(self.output.getvalue(), b'')
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()

    def test_committed_batch_and_unknown_commit_are_not_reported_as_rolled_back(self):
        output = mock.Mock()
        output.write.side_effect = OSError('synthetic-save-error')
        with self.assertRaisesRegex(client.MaintenanceError, 'committed.*do not repeat'):
            client.sql_query(self.inventory, 'credentials', 'SELECT 1', self.identity, output, driver=self.driver)
        self.connection.rollback.assert_not_called()
        self.connection.close.assert_called_once()
        self.connection.reset_mock()
        self.connection.commit.side_effect = OSError('synthetic-unknown-commit')
        with self.assertRaisesRegex(client.MaintenanceError, 'outcome could not be confirmed'):
            self.query()
        self.assertEqual(self.output.getvalue(), b'')

    def test_sql_cli_does_not_offer_server_database_id_or_password_overrides(self):
        parser = client.parser()
        arguments = parser.parse_args(['--operator-object-id', OPERATOR_ID, '--output', 'synthetic-output',
                                       'sql-query', '--database', 'portal', '--query-file', 'synthetic-query'])
        self.assertEqual(arguments.database, 'portal')
        self.assertFalse(any(hasattr(arguments, name) for name in ('password', 'server', 'resource_id', 'inventory')))


if __name__ == '__main__': unittest.main()

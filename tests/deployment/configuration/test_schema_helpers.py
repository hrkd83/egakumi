"""分割schemaのローカル参照と、未知外部参照の拒否を新旧runtimeで確認する。"""
import inspect
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import tests.support.schema_helpers as schema_helpers


class LocalSchemaHelperTests(unittest.TestCase):
    def test_existing_split_references_and_internal_defs_resolve_without_network(self):
        from jsonschema import ValidationError
        root = schema_helpers.ROOT
        cases = [
            {'$ref': 'configuration.schema.json#/$defs/guid'},
            {'$ref': schema_helpers.schema_path('configuration').resolve().as_uri() + '#/$defs/guid'},
            {'$defs': {'value': {'type': 'integer'}}, '$ref': '#/$defs/value'},
            {'$ref': 'params.schema.json#/$defs/guid'},
            {'$ref': schema_helpers.schema_path('params').resolve().as_uri() + '#/$defs/guid'},
        ]
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('Network access forbidden')):
            for index, schema in enumerate(cases):
                validator = schema_helpers.local_schema_validator(schema)
                valid = 7 if index == 2 else '11111111-1111-4111-8111-111111111111'
                with self.subTest(schema=index):
                    validator.validate(valid)
                    self.assertTrue(validator.is_valid(valid))
                    with self.assertRaises(ValidationError):
                        validator.validate('not-a-valid-value')
            # 既存の標準config全体も、同じオフラインvalidatorで検証します。
            options = schema_helpers.read_schema('options')
            sample = json.loads((root / 'infra/config/test.json').read_text(encoding='utf-8'))
            schema_helpers.local_schema_validator(options).validate(sample)

    def test_unknown_http_https_file_and_relative_references_are_rejected_without_value_echo(self):
        references = (
            'http://example.com/PRIVATE-URI-SENTINEL.schema.json',
            'https://example.com/PRIVATE-URI-SENTINEL.schema.json',
            'file:///PRIVATE-URI-SENTINEL.schema.json',
            'PRIVATE-URI-SENTINEL.schema.json',
            'https://json-schema.org/draft/2020-12/schema',
        )
        for reference in references:
            for operation in ('validate', 'is_valid', 'iter_errors'):
                with self.subTest(reference=reference, operation=operation), mock.patch(
                        'urllib.request.urlopen', side_effect=AssertionError('Network access forbidden')) as open_mock, mock.patch(
                        'socket.socket', side_effect=AssertionError('Network access forbidden')) as socket_mock:
                    validator = schema_helpers.local_schema_validator({'$ref': reference})
                    with self.assertRaisesRegex(ValueError, '^Schema reference is not available in the local store$') as caught:
                        result = getattr(validator, operation)('PRIVATE-INSTANCE-SENTINEL')
                        if operation == 'iter_errors':
                            list(result)
                    self.assertNotIn(reference, str(caught.exception))
                    self.assertNotIn('PRIVATE-INSTANCE-SENTINEL', str(caught.exception))
                    open_mock.assert_not_called()
                    socket_mock.assert_not_called()

    def test_legacy_fallback_resolves_local_id_alias_and_rejects_unknown_nested_reference(self):
        fake_module = types.ModuleType('referencing')
        fake_module.Registry = mock.Mock(side_effect=AssertionError('Legacy constructor cannot use registry'))
        fake_module.Resource = mock.Mock()
        with tempfile.TemporaryDirectory(prefix='synthetic-schema-store-') as directory:
            root = Path(directory)
            (root / 'infra/config').mkdir(parents=True)
            document = {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                        '$id': 'https://schemas.example.com/synthetic.schema.json',
                        '$defs': {'integer': {'type': 'integer'}},
                        '$ref': '#/$defs/integer'}
            path = root / 'infra/config/synthetic.schema.json'
            path.write_text(json.dumps(document), encoding='utf-8')
            with mock.patch.dict('sys.modules', {'referencing': fake_module}), mock.patch.object(
                    schema_helpers, 'ROOT', root), mock.patch.object(
                    schema_helpers, 'SCHEMA_PATHS', {'synthetic': 'infra/config/synthetic.schema.json'}), mock.patch.object(
                    schema_helpers.inspect, 'signature', return_value=inspect.Signature()):
                for alias in ('synthetic.schema.json', path.resolve().as_uri(), document['$id']):
                    with self.subTest(alias=alias):
                        validator = schema_helpers.local_schema_validator({'$ref': alias})
                        self.assertTrue(validator.is_valid(4))
                        self.assertFalse(validator.is_valid('four'))
                document['$ref'] = 'PRIVATE-NESTED-SENTINEL.schema.json'
                path.write_text(json.dumps(document), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'local store') as caught:
                    schema_helpers.local_schema_validator({'$ref': document['$id']}).validate(4)
                self.assertNotIn('PRIVATE-NESTED-SENTINEL', str(caught.exception))
                fake_module.Registry.assert_not_called()

    def test_registry_constructor_support_is_checked_before_selecting_new_adapter(self):
        # 実schemaの評価は上のケースで行います。このケースは新APIの受渡しを検証します。
        fake_module = types.ModuleType('referencing')
        registry = mock.Mock()
        registry.with_resources.return_value = registry
        fake_module.Registry = mock.Mock(return_value=registry)
        fake_module.Resource = mock.Mock()
        fake_specification = types.ModuleType('referencing.jsonschema')
        fake_specification.DRAFT202012 = object()

        def modern(schema, *, registry, _resolver):
            return types.SimpleNamespace(schema=schema, registry=registry, resolver=_resolver)

        with mock.patch.dict('sys.modules', {'referencing': fake_module,
                                           'referencing.jsonschema': fake_specification}), mock.patch(
                'jsonschema.Draft202012Validator', modern):
            validator = schema_helpers.local_schema_validator({'type': 'integer'})
            self.assertEqual(validator.schema, {'type': 'integer'})
            fake_module.Registry.assert_called_once()
            retrieve = fake_module.Registry.call_args.kwargs['retrieve']
            with self.assertRaisesRegex(ValueError, 'local store'):
                retrieve('https://example.com/PRIVATE-URI-SENTINEL')
            registered = registry.with_resources.call_args.args[0]
            self.assertIn('params.schema.json', {alias for alias, _ in registered})
            registry.resolver_with_root.assert_called_once()


if __name__ == '__main__':
    unittest.main()

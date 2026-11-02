"""CMの固定資源と利用者APIのID衝突を、大小文字を変えた入力で検査する。"""
import copy
import unittest

from configuration_fixtures import api_fixture, connection_fixture, configuration


class ReservedGatewayResourceTests(unittest.TestCase):
    def test_reserved_ids_and_browser_paths_are_case_insensitive(self):
        for field, value in (("name", "Credential-Manager-Login"),
                             ("name", "Credential-Manager-Browser"),
                             ("path", "Auth/Connections/Browser")):
            with self.subTest(field=field, value=value):
                raw = api_fixture()
                raw["apiDefinitions"][0][field] = value
                with self.assertRaises(configuration.ConfigurationError):
                    configuration.normalize_configuration(raw)

    def test_case_only_api_or_operation_duplicates_are_rejected(self):
        raw = api_fixture()
        duplicate = copy.deepcopy(raw["apiDefinitions"][0])
        duplicate.update(name="MODELS", path="other/models")
        raw["apiDefinitions"].append(duplicate)
        with self.assertRaises(configuration.ConfigurationError):
            configuration.normalize_configuration(raw)
        raw = api_fixture()
        api = raw["apiDefinitions"][0]
        operation = copy.deepcopy(api["operations"][0])
        operation.update(name=operation["name"].upper(), urlTemplate="/other")
        api["operations"].append(operation)
        with self.assertRaises(configuration.ConfigurationError):
            configuration.normalize_configuration(raw)

    def test_provider_ids_respect_case_insensitive_resource_identity(self):
        raw=connection_fixture()
        first=raw["credentialProviders"][0]
        duplicate=copy.deepcopy(first)
        duplicate["name"]=first["name"].upper()
        raw["credentialProviders"].append(duplicate)
        with self.assertRaises(configuration.ConfigurationError):
            configuration.normalize_configuration(raw)
        raw=connection_fixture()
        name="graph."+"p"*74
        raw["credentialProviders"][0]["name"]=name
        raw["gateway"]["authentication"]["credentialLogin"]["providerNames"]=[name]
        raw["apiDefinitions"][0]["credentialConnection"]["providerName"]=name
        actual=configuration.normalize_configuration(raw)
        self.assertEqual(actual["credentialProviders"][0]["name"],name)

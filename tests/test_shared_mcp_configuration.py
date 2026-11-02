"""MCPも共通の購読キーを使い、本人接続が必要なAPIはCMを利用する。"""
import copy
import unittest
from test_api_protocols import CORE, bound_external_coverage, configuration

class SharedMcpConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.raw = bound_external_coverage()
        self.original = next(api for api in self.raw["apiDefinitions"] if api.get("apiType") == "mcp")

    def normalize(self):
        return configuration.normalize_configuration(self.raw, base_directory=CORE / "config")

    def append_mcp(self):
        added = copy.deepcopy(self.original)
        added.update(name="another-mcp", displayName="Another MCP", path="tools/another-mcp")
        self.raw["apiDefinitions"].append(added)
        return added

    def test_added_mcp_uses_common_key_header_without_global_token_registration(self):
        self.append_mcp()
        result = self.normalize()
        self.assertEqual(result["gateway"]["authentication"]["subscriptionKeyHeader"], "api-key")
        self.assertTrue(all(api["authorization"]["mode"] == "subscriptionKey" for api in result["apiDefinitions"]))


    def test_key_mcp_can_use_managed_identity_for_its_backend(self):
        api = self.append_mcp()
        api["backendAuthentication"] = {"mode":"managedIdentity"}
        api["backendAudience"] = "https://cognitiveservices.azure.com"
        result = self.normalize()
        normalized = next(item for item in result["apiDefinitions"] if item["name"] == api["name"])
        self.assertEqual(normalized["authorization"]["mode"], "subscriptionKey")


    def test_nonempty_generated_mcp_oauth_scopes_are_rejected(self):
        api = next(api for api in self.raw["apiDefinitions"] if "mcp" in api)
        api["mcp"]["oauthScopes"] = ["https://gateway.example.com/Gateway.Invoke"]
        with self.assertRaisesRegex(configuration.ConfigurationError, "unsupported.*oauthScopes"):
            self.normalize()

    def test_metadata_paths_remain_reserved_from_caller_defined_routes(self):
        for path in (".well-known/oauth-protected-resource", ".well-known/oauth-protected-resource/tools/new/mcp"):
            with self.subTest(path=path):
                self.original["path"] = path
                with self.assertRaisesRegex(configuration.ConfigurationError, "reserved"):
                    self.normalize()

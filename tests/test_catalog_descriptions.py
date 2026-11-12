"""公開契約から接続説明を作り、動作・秘密・試験結果と混同しないことを確認する。

合成の接続先とモデルだけを使う。Azure/認証先/モデルへの通信は行わず、
ネイティブ配備とAPI再公開へ渡す説明の共通性は別の統合ケースでも調べる。
"""
import copy
from pathlib import Path
import re
import sys
import unittest
from urllib.parse import urlsplit

from configuration_fixtures import api_fixture, pii_fixture, configuration

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from deployment.helpers.catalog_descriptions import describe_api, with_catalog_descriptions


def public_contract():
    """説明の生成に十分な公開契約を、正規化済みのモデル定義から得る。"""
    return configuration.normalize_configuration(api_fixture())


class CatalogDescriptionTests(unittest.TestCase):
    """正確な公開入口と必要権限を示し、内部接続先や主体情報は公開しない。"""

    def test_adds_only_descriptions_and_keeps_every_authorization_and_backend_field(self):
        config = public_contract()
        before = copy.deepcopy(config)
        expected = copy.deepcopy(config["apiDefinitions"])
        described = with_catalog_descriptions(config)
        description = described[0].pop("catalogDescription")
        self.assertEqual(described, expected)
        self.assertEqual(config["services"], before["services"])
        self.assertNotIn("catalogDescription", config["apiDefinitions"][0])
        for forbidden in ("PRIVATE_PRINCIPAL", "PRIVATE_CLIENT", config["apiDefinitions"][0].get("backendUrl", "PRIVATE_BACKEND")):
            if forbidden:
                self.assertNotIn(forbidden, description)
        self.assertIn("Model Gateway / Product=model-users", description)
        self.assertIn("APIM購読キー", description)
        self.assertIn("gpt-approved [OpenAI/gpt-approved, v=2026-01-01]", description)
        self.assertIn("実機の試験結果は別記録", description)

    def test_grpc_uses_fixed_public_port_sni_and_proto_service_without_http_prefix(self):
        config = public_contract()
        config["gateway"]["publicHostName"] = "rpc.enterprise.example"
        config["gateway"]["grpc"]["publicHostName"] = "rpc.enterprise.example"
        api = config["apiDefinitions"][0]
        api.update(apiType="grpc", path="", productId="tool-users",
                   grpc={"definition": 'syntax = "proto3"; package sample.v1; service Probe { rpc Echo (X) returns (X); }'})
        description = describe_api(config, api)
        self.assertIn("接続: https://rpc.enterprise.example:8443/", description)
        self.assertIn("SNI=rpc.enterprise.example、ALPN=h2", description)
        self.assertIn("sample.v1.Probe", description)
        self.assertIn("API Centerの同期URLに公開ポートがない場合も、上記の公開URLで接続", description)
        self.assertIn("HTTPのAPI prefixはRPC pathへ付けません", description)
        self.assertIn("TCP入口はHTTP WAF検査対象外", description)
        self.assertNotIn(":9443", description)
        self.assertNotIn("https://rpc.enterprise.example:8443/models/", description)

    def test_copyable_endpoint_links_end_before_japanese_protocol_explanations(self):
        """空白までを自動リンク化しても、補足文を接続先の一部に含めない。"""
        config = public_contract()
        config["gateway"]["publicHostName"] = "api.enterprise.example"
        config["gateway"]["grpc"]["publicHostName"] = "api.enterprise.example"
        api = copy.deepcopy(config["apiDefinitions"][0])
        cases = [
            ({"apiType": "http", "path": "tools/rest"}, ["https://api.enterprise.example/tools/rest"]),
            ({"apiType": "websocket", "path": "tools/socket"}, ["wss://api.enterprise.example/tools/socket"]),
            ({"apiType": "grpc", "path": "", "grpc": {"definition": 'syntax = "proto3"; package test; service Probe {}'}}, ["https://api.enterprise.example:8443/"]),
            ({"apiType": "mcp", "path": "tools/mcp", "mcpProxy": {"transport": "streamable"}}, ["https://api.enterprise.example/tools/mcp/mcp"]),
            ({"apiType": "mcp", "path": "tools/legacy", "mcpProxy": {"transport": "sse", "endpoints": [
                {"name": "sse", "uriTemplate": "/legacy/sse"}, {"name": "message", "uriTemplate": "/legacy/messages/"}]}},
             ["https://api.enterprise.example/tools/legacy/sse", "https://api.enterprise.example/tools/legacy/legacy/messages/"]),
        ]
        for changes, expected in cases:
            with self.subTest(protocol=changes["apiType"], path=changes["path"]):
                candidate = {**api, **changes}
                links = re.findall(r"(?:https|wss)://\S+", describe_api(config, candidate))
                self.assertEqual(links, expected)
                self.assertTrue(all(urlsplit(link).hostname.endswith("enterprise.example") for link in links))
        pii = configuration.normalize_configuration(pii_fixture())
        pii["gateway"]["publicHostName"] = "api.enterprise.example"
        description = describe_api(pii, pii["apiDefinitions"][0], generated_mcp=True)
        self.assertEqual(re.findall(r"https://\S+", description), [
            "https://api.enterprise.example/tools/pii-v1-mcp/mcp"])

    def test_native_mcp_describes_connection_and_registered_public_message_path(self):
        config = public_contract()
        config["gateway"]["publicHostName"] = "api.enterprise.example"
        api = config["apiDefinitions"][0]
        api.update(apiType="mcp", path="tools/custom", productId="tool-users",
                   mcpProxy={"transport": "sse", "endpoints": [{"name": "sse", "uriTemplate": "/PRIVATE_BACKEND_EVENTS"},
                                                              {"name": "message", "uriTemplate": "/legacy/messages/"}]})
        description = describe_api(config, api)
        self.assertIn("GET https://api.enterprise.example/tools/custom/sse", description)
        self.assertIn("POST https://api.enterprise.example/tools/custom/legacy/messages/", description)
        self.assertNotIn("PRIVATE_BACKEND_EVENTS", description)
        api["mcpProxy"]["transport"] = "streamable"
        self.assertIn("https://api.enterprise.example/tools/custom/mcp", describe_api(config, api))

    def test_websocket_and_webrtc_control_do_not_claim_a_gateway_media_route(self):
        config = public_contract()
        api = config["apiDefinitions"][0]
        api.update(apiType="websocket", path="models/voice/webrtc", backendUrl="wss://PRIVATE_BACKEND/voice-live/realtime/calls")
        description = describe_api(config, api)
        self.assertIn("wss://" + config["gateway"]["publicHostName"] + "/models/voice/webrtc", description)
        self.assertIn("mediaは端末↔サービスのICE経路", description)
        self.assertIn("Gateway/PE経由を保証しません", description)
        self.assertNotIn("PRIVATE_BACKEND", description)
        api.update(apiType="http", path="models/realtime/v1")
        api["authorization"]["operationConstraints"] = {"create": {"kind": "realtimeCreate", "maxRequestBytes": 4096}}
        self.assertIn("接続制御はGateway経由", describe_api(config, api))
        api.update(apiType="websocket", backendUrl="wss://PRIVATE_BACKEND/realtime")
        api["authorization"]["operationConstraints"] = {"invoke": {"kind": "registeredOperation"}}
        self.assertIn("双方向WSS", describe_api(config, api))
        self.assertNotIn("ICE経路", describe_api(config, api))

    def test_generated_mcp_keeps_key_authorization_and_original_definition(self):
        config = configuration.normalize_configuration(pii_fixture())
        api = config["apiDefinitions"][0]
        before = copy.deepcopy(config)
        result = with_catalog_descriptions(config)
        description = result[0]["mcp"].pop("catalogDescription")
        result[0].pop("catalogDescription")
        self.assertEqual(result, before["apiDefinitions"])
        self.assertEqual(config, before)
        self.assertIn("APIM購読キー", description)
        self.assertIn("https://" + config["gateway"]["publicHostName"] + "/" + api["mcp"]["path"] + "/mcp", description)

    def test_generated_mcp_description_tracks_its_public_path(self):
        """MCPの追加・改名に合わせて、説明の接続URLを更新する。"""
        config = configuration.normalize_configuration(pii_fixture())
        config["gateway"]["publicHostName"] = "api.enterprise.example"
        api = config["apiDefinitions"][0]
        for path in ("tools/first", "tools/second"):
            with self.subTest(path=path):
                api["mcp"]["path"] = path
                urls = re.findall(r"https://\S+", describe_api(config, api, generated_mcp=True))
                self.assertEqual(urls, [f"https://api.enterprise.example/{path}/mcp"])

    def test_many_models_are_explicitly_summarized(self):
        config = public_contract()
        api = config["apiDefinitions"][0]
        api["authorization"]["operationConstraints"]["responses-create"]["allowedModels"] = ["model-" + str(index) for index in range(6)]
        description = describe_api(config, api)
        self.assertIn("登録モデル: model-0, model-1 ほか4件", description)
        self.assertLessEqual(len(description), 1000)

    def test_service_class_and_profile_models_limits_are_published_without_flattening_contracts(self):
        config = public_contract()
        api = config["apiDefinitions"][0]
        api["serviceClass"] = "sample"
        api["authorization"]["operationConstraints"] = {"chat-completions": {
            "defaultProfile": "chat", "profiles": [
                {"profileId": "chat", "kind": "bodyModel", "allowedModels": ["plain-approved"], "maxRequestBytes": 2048},
                {"profileId": "vision", "kind": "bodyModel", "allowedModels": ["vision-approved"], "maxRequestBytes": 4096}]}}
        description = describe_api(config, api)
        self.assertIn("提供区分: 別配備サンプルAPI", description)
        self.assertIn("plain-approved, vision-approved", description)
        self.assertIn("要求上限byte=2048, 4096", description)
        self.assertLessEqual(len(description), 1000)

    def test_websocket_allowed_models_and_default_are_in_catalog(self):
        config = public_contract()
        api = config["apiDefinitions"][0]
        api.update(apiType="websocket", websocket={"allowedModels": ["realtime", "realtime-mini"],
                   "defaultModel": "realtime", "queryParameters": {"api-version": "fixture"}})
        api["authorization"]["operationConstraints"] = {"invoke": {"kind": "registeredOperation"}}
        self.assertIn("登録モデル: realtime, realtime-mini", describe_api(config, api))

    def test_oversized_public_contract_is_rejected_in_free_configuration_validation(self):
        config = api_fixture()
        api = config["apiDefinitions"][0]
        # 各フィールド単体の長さが有効でも、接続先と説明の合計は超え得る。
        host = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])
        config["gateway"]["publicHostName"] = host
        config["gateway"]["grpc"]["publicHostName"] = host
        api["path"] = "models/" + "x" * 390
        api["productId"] = "p" * 80
        api["subscriptionKeyHeader"] = "k" * 128
        config["gateway"]["authentication"]["subscriptionKeyHeader"] = "k" * 128
        with self.assertRaisesRegex(ValueError, "Catalog description exceeds 1000"):
            configuration.normalize_configuration(config)


if __name__ == "__main__":
    unittest.main()

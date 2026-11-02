"""MCP公開要求を実コンパイルし、配列の公開設定からAzure用辞書への変換を確認する。

公開Swaggerの配列例は実サービスで拒否されたため、Microsoft公式APIM拡張の
SSE辞書とFoundryサンプルのmcp辞書に合わせる。これはAzure受入試験の代替ではない。
"""
from arm_helpers import ArmExpression
from bicep_helpers import compile_bicep

import json
from pathlib import Path
import re
import unittest


CORE = Path(__file__).resolve().parents[1]


class McpPublicationTests(unittest.TestCase):
    """配備するAPI本体・URL・バックエンド作成順の具体値を検査する。"""

    @classmethod
    def setUpClass(cls):
        template = compile_bicep('apps/gateway/apim/api-publication.bicep')
        cls.resource = template["resources"]["apis"]
        cls.generated_description = ArmExpression(template["resources"]["mcpServers"]["properties"]["description"])
        routing = next(item for item in template["variables"]["copy"] if item["name"] == "mcpProxyRoutingXml")
        cls.routing = ArmExpression(routing["input"])
        cls.expression = ArmExpression(cls.resource["properties"])

    def properties(self, api):
        definition = {"name": "fixture", "displayName": "Fixture", "path": "tools/fixture",
                      "apiType": "http", "authorization": {"mode": "subscriptionKey"},
                      "subscriptionKeyHeader": "api-key",
                      "backendUrl": "https://backend.example.com", **api}
        return self.expression.evaluate({"apiDefinitions": [definition]}, 0)

    def test_streamable_json_and_sse_responses_use_one_mcp_endpoint(self):
        """同じStreamable方式のJSON/SSE応答は、設定URLを変えずmcpキーへ送る。"""
        for url in ("/mcp", "/mcp-stream"):
            with self.subTest(url=url):
                actual = self.properties({"apiType": "mcp", "mcpProxy": {
                    "transport": "streamable", "endpoints": [{"name": "message", "uriTemplate": url}]}})
                self.assertEqual(actual["mcpProperties"], {
                    "transportType": "streamable", "endpoints": {"mcp": {"uriTemplate": "/mcp"}}})
                self.assertEqual(actual["backendId"], "fixture-backend")
                self.assertNotIn("serviceUrl", actual)
                self.assertEqual(actual["type"], "mcp")
                self.assertEqual(actual["path"], "tools/fixture")
                self.assertTrue(actual["subscriptionRequired"])

    def test_compiled_normal_and_generated_mcp_apis_receive_their_own_public_descriptions(self):
        """説明をREST本体と生成MCPへ別々に渡し、認証方式やURLは変えない。"""
        actual = self.properties({"catalogDescription": "REST_PUBLIC_DESCRIPTION"})
        self.assertEqual(actual["description"], "REST_PUBLIC_DESCRIPTION")
        self.assertEqual(actual["path"], "tools/fixture")
        self.assertTrue(actual["subscriptionRequired"])
        self.assertEqual(self.generated_description.evaluate({"__variables": {"mcpApis": [{
            "mcp": {"catalogDescription": "MCP_DELEGATED_PUBLIC_DESCRIPTION"}}]}}, 0), "MCP_DELEGATED_PUBLIC_DESCRIPTION")

    def test_legacy_sse_uses_distinct_sse_and_messages_dictionary_keys(self):
        """従来SSEはGET接続とPOST送信を分け、元の相対パスを保つ。"""
        actual = self.properties({"apiType": "mcp", "mcpProxy": {
            "transport": "sse", "endpoints": [
                {"name": "sse", "uriTemplate": "/legacy/events"},
                {"name": "message", "uriTemplate": "/legacy/messages"}]}})
        self.assertEqual(actual["mcpProperties"], {"transportType": "sse", "endpoints": {
            "sse": {"method": "GET", "uriTemplate": "/sse"},
            "messages": {"method": "POST", "uriTemplate": "/legacy/messages"}}})
        self.assertEqual(actual["backendId"], "fixture-backend")
        self.assertEqual(self.resource["dependsOn"], ["[format('backends[{0}]', copyIndex())]"])

    def test_backend_rewrite_is_generated_from_registered_paths_and_keeps_session_query(self):
        """接続先と転送先を区別し、旧SSEの案内先・POST登録・queryを揃える。"""
        import xml.etree.ElementTree as ET
        overlay = json.loads((CORE / "tests/fixtures/external-api-definitions.json").read_text())
        proxies = [api for api in overlay["apiDefinitions"] if api.get("apiType") == "mcp"]
        self.assertEqual({api["name"] for api in proxies}, {"sample-mcp-v1", "sample-mcp-legacy"})
        observed = {}
        for api in proxies:
            xml = self.routing.evaluate({"apiDefinitions": [api]}, 0)
            root = ET.fromstring("<routing>" + xml + "</routing>")
            rewrites = root.findall(".//rewrite-uri")
            self.assertTrue(all(item.get("copy-unmatched-params") == "true" for item in rewrites))
            configured = {item["name"]: item["uriTemplate"] for item in api["mcpProxy"]["endpoints"]}
            if api["mcpProxy"]["transport"] == "streamable":
                self.assertEqual([item.get("template") for item in rewrites], [configured["message"]])
                self.assertEqual(self.properties(api)["mcpProperties"]["endpoints"], {"mcp": {"uriTemplate": "/mcp"}})
                observed[api["name"]] = rewrites[0].get("template")
            else:
                conditions = [item.get("condition") for item in root.findall("./choose/when")]
                self.assertEqual(conditions, [f'@(context.Request.OriginalUrl.Path == "/{api["path"]}/sse" && context.Request.Method == "GET")',
                                              f'@(context.Request.OriginalUrl.Path == "/{api["path"]}{configured["message"]}" && context.Request.Method == "POST")'])
                self.assertEqual([item.get("template") for item in rewrites], [configured["sse"], configured["message"]])
                self.assertEqual(self.properties(api)["mcpProperties"]["endpoints"]["messages"],
                                 {"method": "POST", "uriTemplate": configured["message"]})
                self.assertEqual(root.find("./choose/otherwise/return-response/set-status").get("code"), "404")
        self.assertEqual(observed, {"sample-mcp-v1": "/mcp"})
        self.assertEqual(self.routing.evaluate({"apiDefinitions": [{"apiType": "http"}]}, 0), "")
        # native MCP型と入口ポリシーを保ち、rewriteのためのHTTP APIへ置き換えない。
        policy = ET.parse(CORE / "apps/gateway/apim/policies/protocol.xml")
        backend = policy.find("./inbound/set-backend-service")
        self.assertIsNotNone(backend)
        source = (CORE / "apps/gateway/apim/policies/protocol.xml").read_text()
        self.assertLess(source.index("<set-backend-service"), source.index("__MCP_PROXY_ROUTE_XML__"))

    def test_other_protocols_keep_their_existing_wire_contracts(self):
        """MCPの変換をHTTP・WebSocket・gRPCへ適用しない。"""
        for kind, extra in (("http", {}), ("websocket", {}), ("grpc", {"grpc": {"definition": "syntax = \"proto3\";"}})):
            with self.subTest(kind=kind):
                actual = self.properties({"apiType": kind, **extra})
                self.assertNotIn("mcpProperties", actual)
                self.assertNotIn("backendId", actual)
                if kind != "http":
                    self.assertEqual(actual["serviceUrl"], "https://backend.example.com")
                if kind == "grpc":
                    self.assertEqual(actual["format"], "grpc")

    def test_legacy_server_advertisement_matches_the_registered_public_post_and_backend(self):
        """単体サーバーの相対案内を変えず、prefix付与後の要求が元のPOSTへ戻る。

        native APIMのprefix付与は以前の実測を前提とした写像です。ここで使うのは
        実際にコンパイルしたAPIM登録・policyであり、新しいAzure配備の合格ではありません。
        """
        from urllib.parse import urljoin, urlsplit, urlunsplit
        import xml.etree.ElementTree as ET

        api = next(api for api in json.loads((CORE / "tests/fixtures/external-api-definitions.json").read_text())["apiDefinitions"]
                   if api["name"] == "sample-mcp-legacy")
        api = {**api, "backendUrl": "https://tools.example.com"}
        server_advertisement = "/legacy/messages/?session_id=SYNTHETIC_SESSION"
        prefixed = "/" + api["path"] + server_advertisement
        posted = urlsplit(prefixed)
        properties = self.properties(api)
        registration = properties["mcpProperties"]["endpoints"]["messages"]
        self.assertEqual("/" + api["path"] + registration["uriTemplate"], posted.path)
        self.assertEqual(registration["method"], "POST")
        routing = ET.fromstring("<routing>" + self.routing.evaluate({"apiDefinitions": [api]}, 0) + "</routing>")
        message = routing.findall("./choose/when")[1]
        self.assertEqual(message.get("condition"),
                         f'@(context.Request.OriginalUrl.Path == "{posted.path}" && context.Request.Method == "POST")')
        rewrite = message.find("rewrite-uri")
        self.assertEqual(rewrite.get("copy-unmatched-params"), "true")
        backend_path = urlsplit(urljoin(api["backendUrl"], rewrite.get("template")))
        target = urlunsplit((*backend_path[:3], posted.query, ""))
        self.assertEqual(target, "https://tools.example.com/legacy/messages/?session_id=SYNTHETIC_SESSION")
        self.assertEqual(routing.find("./choose/otherwise/return-response/set-status").get("code"), "404")

    def test_legacy_routing_accepts_only_the_two_registered_paths_and_methods(self):
        """似たprefix・他API・旧POST先・異なるHTTP方式を中継へ落とさない。"""
        import xml.etree.ElementTree as ET

        api = {"apiType": "mcp", "path": "tools/legacy", "mcpProxy": {
            "transport": "sse", "endpoints": [{"name": "sse", "uriTemplate": "/legacy/sse"},
                                                 {"name": "message", "uriTemplate": "/legacy/messages/"}]}}
        routing = ET.fromstring("<routing>" + self.routing.evaluate({"apiDefinitions": [api]}, 0) + "</routing>")
        registered = []
        for when in routing.findall("./choose/when"):
            match = re.fullmatch(r'@\(context.Request.OriginalUrl.Path == "([^"]+)" && context.Request.Method == "(GET|POST)"\)',
                                 when.get("condition"))
            self.assertIsNotNone(match)
            registered.append((match[1], match[2]))
        self.assertEqual(registered, [("/tools/legacy/sse", "GET"), ("/tools/legacy/legacy/messages/", "POST")])
        for path, method in (("/tools/legacy/messages/", "POST"), ("/tools/other/legacy/messages/", "POST"),
                             ("/tools/legacy/legacy/messages/extra", "POST"), ("/tools/legacy/sse", "POST"),
                             ("/tools/legacy/legacy/messages/", "GET"), ("/tools/legacy/legacy/messages", "POST")):
            with self.subTest(path=path, method=method):
                self.assertNotIn((path, method), registered)
        self.assertEqual(routing.find("./choose/otherwise/return-response/set-status").get("code"), "404")


if __name__ == "__main__":
    unittest.main()

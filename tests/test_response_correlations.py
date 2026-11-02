"""APIM自身の要求IDが、通常・拒否・例外応答から取得できる配線を確認する。

return-responseはoutboundを実行しないため、共通outboundの指定だけでは足りない。
XML全分岐と実コンパイルしたBicepのinline応答を調べ、記録対象に本文を加えない。
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


CORE = Path(__file__).resolve().parents[1]
POLICIES = CORE / "apps/gateway/apim/policies"
SERVICE_POLICY = CORE / "apps/gateway/apim/policies/service.xml"
EXPRESSION = "@(context.RequestId.ToString())"


def string_values(value):
    """テンプレート内の文字列だけをたどる。ARM式を実行したりAzureへ接続しない。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from string_values(item)


class ResponseCorrelationTests(unittest.TestCase):
    """実配備に使う全早期応答と、親ポリシーを継承する経路を検査する。"""

    def assert_header(self, parent):
        headers = parent.findall("./set-header[@name='apim-request-id']")
        self.assertEqual(len(headers), 1)
        header = headers[0]
        self.assertEqual(header.get("exists-action"), "override")
        self.assertEqual(header.find("value").text.strip(), EXPRESSION)
        return header

    def assert_early_response(self, response):
        header = self.assert_header(response)
        children = list(response)
        # APIMの公式順序を保ち、既存status/bodyや認証challengeには触れない。
        self.assertLess(children.index(response.find("set-status")), children.index(header))
        body = response.find("set-body")
        if body is not None:
            self.assertLess(children.index(header), children.index(body))

    def test_all_xml_early_responses_include_gateway_owned_correlation_header(self):
        count = 0
        for filename in sorted([*POLICIES.glob("*.xml"), SERVICE_POLICY]):
            for response in ET.parse(filename).getroot().iter("return-response"):
                with self.subTest(filename=filename.name, index=count):
                    self.assert_early_response(response)
                count += 1
        self.assertGreater(count, 0)

    def test_normal_and_implicit_error_paths_inherit_the_same_header_without_reading_body(self):
        service = ET.parse(SERVICE_POLICY).getroot()
        self.assert_header(service.find("outbound"))
        self.assert_header(service.find("on-error"))
        self.assertFalse(service.findall(".//base"))
        for filename in ("gateway.xml", "protocol.xml", "mcp.xml", "pii.xml"):
            policy = ET.parse(POLICIES / filename)
            for section in ("outbound", "on-error"):
                with self.subTest(filename=filename, section=section):
                    inherited = policy.find(f"./{section}/base")
                    if inherited is None:
                        # PIIの専用on-errorは親を継承せず、必ず自分で整形した早期応答を返す。
                        self.assertEqual((filename, section), ("pii.xml", "on-error"))
                        self.assert_early_response(policy.find("./on-error/return-response"))
                    else:
                        self.assertEqual(inherited.tag, "base")

    def test_compiled_inline_and_loaded_early_responses_have_the_header(self):
        bicep = os.environ.get("BICEP_CLI") or shutil.which("bicep") or str(Path.home() / ".azure/bin/bicep")
        if not Path(bicep).is_file():
            self.skipTest("A local Bicep compiler is required")
        count = 0
        with tempfile.TemporaryDirectory(prefix="core-response-correlation-") as folder:
            for relative in ("apps/gateway/apim/api-publication.bicep", "infra/modules/services.bicep", "apps/gateway/deploy/apim-settings.bicep"):
                filename = Path(relative).name
                output = Path(folder) / (filename + ".json")
                result = subprocess.run([bicep, "build", str(CORE / relative), "--outfile", str(output)],
                    env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": tempfile.gettempdir() + "/workforce-bicep-bundle"},
                    capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                for text in string_values(json.loads(output.read_text())):
                    for block in re.findall(r"<return-response\b[^>]*>.*?</return-response>", text, re.S):
                        with self.subTest(template=filename, index=count):
                            self.assert_early_response(ET.fromstring(block))
                        count += 1
        # XML読取だけでは漏れる共通ポリシーと静的応答も、コンパイラーの出力で対象にする。
        self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()

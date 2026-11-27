"""生成ARM/XMLまで展開し、全protocolへの上限・安全429・計数単位を検査する。

Azureの分散counterを再実装する試験ではない。実際のBicep置換で生じる未展開marker、
WebSocket非対応body、PII独自errorによる429消失、差分公開の設定欠落を無料で検出する。
"""
import copy
import unittest
import xml.etree.ElementTree as ET

from tests.support.arm_helpers import PolicyExpression, compiled_policy_value
from tests.support.rate_publication import rate_publication_fixture


class ContractRatePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template, _, cls.parameters = rate_publication_fixture()

    def setUp(self):
        """設定変更の試験が、別ケースの中央設定へ残らないようにする。"""
        self.parameters=copy.deepcopy(type(self).parameters)

    def evaluate(self,value,index=0):
        return PolicyExpression(value,self.template["variables"],functions=self.template.get("functions",[])).evaluate(self.parameters,index)

    def test_compiled_each_http_protocol_has_one_counter_before_backend_and_safe_error(self):
        count=len(self.parameters["apiDefinitions"])
        for index in range(count):
            api=self.parameters["apiDefinitions"][index]
            xml=compiled_policy_value(self.template,self.parameters,index)
            with self.subTest(api=api["name"],kind=api["apiType"]):
                self.assertNotIn("__RATE_LIMIT_",xml)
                parsed=ET.fromstring(xml)
                calls=parsed.findall("inbound/include-fragment[@fragment-id='core-rate-limit']")
                self.assertEqual(len(calls),1)
                inbound=parsed.find("inbound")
                decision=inbound.find('include-fragment[@fragment-id="core-api-authentication-result"]') if api["apiType"]=="http" else inbound.find('set-variable[@name="gatewayDecision"]')
                self.assertLess(list(inbound).index(decision),list(inbound).index(calls[0]))
                self.assertLess(xml.index('fragment-id="core-rate-limit"'),xml.index('__BACKEND_AUTHENTICATION_XML__') if '__BACKEND_AUTHENTICATION_XML__' in xml else xml.index('name="gatewayAuditPhase" value="accepted"'))
                on_error = parsed.find("on-error")
                handlers = list(on_error)
                handler = handlers[handlers.index(on_error.find("base")) + 1].find("when")
                self.assertIn('core-rate-limit',handler.attrib["condition"])
                self.assertEqual(handler.find("return-response/set-status").attrib["code"],"429")
                self.assertEqual(handler.find("set-variable[@name='gatewayAuditCode']").get("value"), "RATE_LIMIT_EXCEEDED")
                self.assertIsNone(handler.find("return-response/set-body"))
                self.assertIsNone(handler.find("return-response/set-header[@name='WWW-Authenticate']"))
                if api["apiType"]=="websocket":
                    self.assertIsNone(parsed.find(".//set-body"))

    def test_generated_mcp_counts_only_backing_rest_and_specialized_errors_keep_429(self):
        xml=compiled_policy_value(self.template,self.parameters,resource_name='mcpPolicies')
        generated=ET.fromstring(xml)
        self.assertIsNone(generated.find(".//include-fragment[@fragment-id='core-rate-limit']"))
        handlers = list(generated.find("on-error"))
        self.assertEqual(handlers[0].tag, "base")
        self.assertEqual(handlers[1].find("when/return-response/set-status").get("code"), "429")
        self.assertEqual(handlers[1].find("when/set-variable[@name='gatewayAuditCode']").get("value"), "RATE_LIMIT_EXCEEDED")
        # PIIとDocumentの最終操作値では、認証エラー処理の直後に429を維持します。
        entries=self.evaluate("[variables('operationEntries')]")
        indexes=[index for index,entry in enumerate(entries)
                 if entry['constraint']['kind'] in ('pii','documentAnalysis','documentAnalysisStatus')]
        self.assertEqual(len(indexes),3)
        for enabled in (False,True):
            self.parameters['entraAuthentication']={'enabled':enabled}
            for index in indexes:
                with self.subTest(entra_enabled=enabled,kind=entries[index]['constraint']['kind']):
                    xml=compiled_policy_value(self.template,self.parameters,index,resource_name='operationPolicies')
                    handlers=list(ET.fromstring(xml).find('on-error'))
                    if enabled:
                        self.assertEqual(handlers[0].attrib,{'id':'core-entra-authentication-error','fragment-id':'core-entra-authentication-error'})
                    first=handlers[int(enabled)]
                    self.assertEqual(first.tag,"choose")
                    self.assertIn('core-rate-limit',first.find('when').get('condition'))
                    self.assertEqual(first.find("when/return-response/set-status").attrib["code"],"429")
                    self.assertEqual(first.find("when/set-variable[@name='gatewayAuditCode']").get("value"), "RATE_LIMIT_EXCEEDED")
                    self.assertEqual(handlers[-1].tag, "return-response")
                    self.assertNotIn("__RATE_LIMIT_",xml)
                    self.assertNotIn("__OPERATION_ENTRA_AUTHENTICATION_ERROR_XML__",xml)

    def test_shared_fragment_has_single_central_limits_and_no_deferred_counting(self):
        expression=self.template["resources"]["rateLimitFragment"]["properties"]["value"]
        xml=self.evaluate(expression)
        parsed=ET.fromstring(xml)
        rate=parsed.find("choose/when/rate-limit-by-key")
        self.assertEqual(rate.attrib["id"],"core-rate-limit")
        self.assertNotIn("increment-condition",rate.attrib)
        self.assertNotIn("increment-count",rate.attrib)
        self.assertNotIn("include-fragment",xml)

if __name__=="__main__": unittest.main()

"""生成ARM/XMLまで展開し、全protocolへの上限・安全429・計数単位を検査する。

Azureの分散counterを再実装する試験ではない。実際のBicep置換で生じる未展開marker、
WebSocket非対応body、PII独自errorによる429消失、差分公開の設定欠落を無料で検出する。
"""
from bicep_helpers import compile_bicep

import base64
import copy
import json
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

from test_api_protocols import bound_coverage, bound_external_coverage, configuration
from arm_helpers import PolicyExpression
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/gateway/deploy"))
import api_publication as publish_apis

CORE = Path(__file__).resolve().parents[1]


class ContractRatePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/gateway/apim/api-publication.bicep')
        raw=bound_external_coverage()
        # Explicit external test fixtures exercise every supported protocol; they are not standard Core APIs.
        cls.config=configuration.normalize_configuration(raw,base_directory=CORE/"config")
        cls.parameters={"apiDefinitions":cls.config["apiDefinitions"],
            "credentialLogin":{},
            "publicHostName":cls.config["gateway"]["publicHostName"],
            "entraTenantId":cls.config["entraTenantId"],
            "entraTenantId":cls.config["entraTenantId"],
            "rateLimits":cls.config["gateway"]["rateLimits"]}

    def setUp(self):
        """設定変更の試験が、別ケースの中央設定へ残らないようにする。"""
        self.parameters=copy.deepcopy(type(self).parameters)

    def evaluate(self,value,index=0):
        return PolicyExpression(value,self.template["variables"],functions=self.template.get("functions",[])).evaluate(self.parameters,index)

    def test_omitted_common_rate_limit_stays_disabled_through_publication(self):
        """設定省略時も、公開計画とBicepの既定値から回数上限を有効にしない。"""
        from test_contract_rate_configuration import DEFAULT
        from test_publish_apis import PublicationTests
        fixture = PublicationTests(); fixture.setUp()
        self.assertNotIn("rateLimits", fixture.config["gateway"])
        self.assertEqual(fixture.plan["parameters"]["rateLimits"]["value"], DEFAULT)
        self.assertEqual(self.template["parameters"]["rateLimits"]["defaultValue"], DEFAULT)

    def test_compiled_each_http_protocol_has_one_counter_before_backend_and_safe_error(self):
        count=len(self.parameters["apiDefinitions"])
        for index in range(count):
            api=self.parameters["apiDefinitions"][index]
            xml=self.evaluate("[variables('policyValues')[copyIndex()]]",index)
            with self.subTest(api=api["name"],kind=api["apiType"]):
                self.assertNotIn("__RATE_LIMIT_",xml)
                parsed=ET.fromstring(xml)
                calls=parsed.findall("inbound/include-fragment[@fragment-id='core-rate-limit']")
                self.assertEqual(len(calls),1)
                self.assertLess(xml.index('name="gatewayDecision"'),xml.index('fragment-id="core-rate-limit"'))
                self.assertLess(xml.index('fragment-id="core-rate-limit"'),xml.index('__BACKEND_AUTHENTICATION_XML__') if '__BACKEND_AUTHENTICATION_XML__' in xml else xml.index('name="gatewayAuditPhase" value="accepted"'))
                handler=parsed.find("on-error/choose/when")
                self.assertIn('core-rate-limit',handler.attrib["condition"])
                self.assertEqual(handler.find("return-response/set-status").attrib["code"],"429")
                self.assertIsNone(handler.find("return-response/set-body"))
                self.assertIsNone(handler.find("return-response/set-header[@name='WWW-Authenticate']"))
                if api["apiType"]=="websocket":
                    self.assertIsNone(parsed.find(".//set-body"))

    def test_generated_mcp_counts_only_backing_rest_and_pii_error_keeps_429(self):
        xml=self.evaluate("[variables('mcpPolicyValues')[copyIndex()]]")
        generated=ET.fromstring(xml)
        self.assertIsNone(generated.find(".//include-fragment[@fragment-id='core-rate-limit']"))
        self.assertEqual(generated.find("on-error/choose/when/return-response/set-status").attrib["code"],"429")
        xml=self.evaluate("[variables('piiPolicyTemplate')]")
        pii=ET.fromstring(xml)
        first=list(pii.find("on-error"))[0]
        self.assertEqual(first.tag,"choose")
        self.assertEqual(first.find("when/return-response/set-status").attrib["code"],"429")
        self.assertNotIn("__RATE_LIMIT_",xml)

    def test_shared_fragment_has_single_central_limits_and_no_deferred_counting(self):
        settings={"user":{"enabled":False,"calls":12,"renewalPeriodSeconds":30},
            "subscriptionKey":{"enabled":True,"calls":120,"renewalPeriodSeconds":60,"overrides":[]},
            "application":{"enabled":False,"calls":30,"renewalPeriodSeconds":60,
                "overrides":[{"callerId":"same-contract","calls":3,"renewalPeriodSeconds":60}]}}
        self.parameters["rateLimits"]=settings
        expression=self.template["resources"]["rateLimitFragment"]["properties"]["value"]
        xml=self.evaluate(expression)
        parsed=ET.fromstring(xml)
        rate=parsed.find("choose/when/rate-limit-by-key")
        self.assertEqual(rate.attrib["id"],"core-rate-limit")
        self.assertNotIn("increment-condition",rate.attrib)
        self.assertNotIn("increment-count",rate.attrib)
        self.assertIn(base64.b64encode(json.dumps(settings,separators=(",",":")).encode()).decode(),xml)
        self.assertNotIn("include-fragment",xml)

    def test_api_registration_uses_rate_limits_independently_of_foundation(self):
        raw=bound_coverage()
        raw["publishApis"]=True
        raw["gateway"]["rateLimits"]={"subscriptionKey":{"enabled":True,"calls":7,"renewalPeriodSeconds":20}}
        config=configuration.normalize_configuration(raw,base_directory=CORE/"config")
        from test_publish_apis import PublicationTests
        fixture=PublicationTests(); fixture.setUp()
        fixture.config["gateway"]["rateLimits"]=config["gateway"]["rateLimits"]
        plan=publish_apis.prepare_plan(fixture.config,fixture.state)
        self.assertEqual(plan["parameters"]["rateLimits"]["value"],config["gateway"]["rateLimits"])
        self.assertEqual(configuration.build_bicep_configuration(config)["gateway"]["rateLimits"],config["gateway"]["rateLimits"])
        main=(CORE/"infra/main.bicep").read_text()
        self.assertNotIn("api-publication.bicep",main)


if __name__=="__main__": unittest.main()

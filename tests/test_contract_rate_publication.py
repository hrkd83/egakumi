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
from arm_helpers import PolicyExpression, compiled_policy_value
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/apim/deploy"))
import api_publication as publish_apis

CORE = Path(__file__).resolve().parents[1]


class ContractRatePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep('apps/apim/api-publication.bicep')
        raw=bound_external_coverage()
        # Explicit external test fixtures exercise every supported protocol; they are not standard Core APIs.
        cls.config=configuration.normalize_configuration(raw,base_directory=CORE/"config")
        # The publication module receives the resolved Core backend URLs.
        # Supply independent synthetic endpoints while preserving external URLs.
        definitions=copy.deepcopy(cls.config['apiDefinitions'])
        for api in definitions:
            api.setdefault('backendUrl','https://backend.example.invalid'+api.get('backendPath',''))
            for operation in api['operations']:
                if 'backendOverride' in operation:
                    operation['backendOverride'].setdefault('backendUrl','https://backend.example.invalid')
        cls.parameters={"apiDefinitions":definitions,
            "credentialLogin":{},
            "subscriptionKeyHeader":cls.config["gateway"]["authentication"]["subscriptionKeyHeader"],
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
                handler=next(node for node in parsed.findall("on-error/choose/when")
                             if 'core-rate-limit' in node.get('condition',''))
                self.assertIn('core-rate-limit',handler.attrib["condition"])
                self.assertEqual(handler.find("return-response/set-status").attrib["code"],"429")
                self.assertIsNone(handler.find("return-response/set-body"))
                self.assertIsNone(handler.find("return-response/set-header[@name='WWW-Authenticate']"))
                if api["apiType"]=="websocket":
                    self.assertIsNone(parsed.find(".//set-body"))

    def test_generated_mcp_counts_only_backing_rest_and_specialized_errors_keep_429(self):
        xml=compiled_policy_value(self.template,self.parameters,resource_name='mcpPolicies')
        generated=ET.fromstring(xml)
        self.assertIsNone(generated.find(".//include-fragment[@fragment-id='core-rate-limit']"))
        self.assertEqual(generated.find("on-error/choose/when/return-response/set-status").attrib["code"],"429")
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
                    self.assertNotIn("__RATE_LIMIT_",xml)
                    self.assertNotIn("__OPERATION_ENTRA_AUTHENTICATION_ERROR_XML__",xml)

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

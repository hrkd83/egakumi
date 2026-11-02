"""CM 接続の追加条件と下流エラー変換の公開契約を検証する。"""
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from deployment.helpers import credential_login
from test_credential_login import settings

CORE=Path(__file__).resolve().parents[1]
POLICIES=CORE/"apps/gateway/apim/policies"


class ConnectionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        telemetry=(POLICIES/"gateway-telemetry.xml").read_text(encoding="utf-8").replace("<fragment>","").replace("</fragment>","")
        cls.inbound=ET.fromstring(credential_login.render_connection("credential-connection.xml",settings(),telemetry))
        cls.use=ET.fromstring(credential_login.render_connection("credential-session-use.xml",settings(),telemetry))
        cls.outbound=ET.fromstring(credential_login.render_connection("credential-connection-outbound.xml",settings(),telemetry))

    def test_subscription_session_selection_precedes_cm_acquisition(self):
        children=list(self.inbound)
        self.assertEqual(children[0].get("name"),"gatewayConnectionSelection")
        acquire=self.inbound.find(".//get-authorization-context")
        session=self.inbound.find(".//send-request[@response-variable-name='gatewayConnectionSessionResponse']")
        self.assertIsNotNone(session)
        self.assertLess(next(i for i,n in enumerate(children) if session in n.iter()), next(i for i,n in enumerate(children) if acquire in n.iter()))
        guard=next(n for n in self.inbound.findall('choose/when') if acquire in n.iter())
        self.assertIn('["allowed"]',guard.get('condition'))
        self.assertEqual(acquire.tag,"get-authorization-context")
        self.assertEqual(acquire.get("identity-type"),"managed")
        self.assertEqual(acquire.get("ignore-error"),"true")
        for attribute in ("provider-id","authorization-id"):
            self.assertIn("gatewayConnectionSelection",acquire.get(attribute))
            self.assertNotIn("context.Request",acquire.get(attribute))

    def test_downstream_forbidden_is_safe_and_distinct_from_reauthentication(self):
        branches=[node for node in self.outbound.findall("choose/when") if node.get('condition').startswith('@(context.Response.StatusCode ==')]
        self.assertEqual(len(branches),2)
        for branch,(upstream,status,code) in zip(branches,[(401,409,"ConnectionRequired"),(403,403,"ConnectionPermissionDenied")]):
            with self.subTest(upstream=upstream):
                self.assertEqual(branch.get("condition"),f"@(context.Response.StatusCode == {upstream})")
                response=branch.find("return-response")
                self.assertIsNone(response.get("response-variable-name"))
                if upstream==403:
                    self.assertEqual(int(response.find("set-status").get("code")),status)
                    body=json.loads(response.find("set-body").text)
                    self.assertEqual(body["error"]["code"],code)
                else:
                    self.assertIn('gatewayAuditStatus',response.find("set-status").get("code"))
                    self.assertIn(code,response.find("set-body").text)
                for name in ("WWW-Authenticate","Location","Set-Cookie","Authentication-Info"):
                    self.assertEqual(response.find(f"set-header[@name='{name}']").get("exists-action"),"delete")
                self.assertNotIn("context.Response",ET.tostring(response,encoding="unicode"))
                self.assertEqual(response.find("set-header[@name='Cache-Control']/value").text,"no-store")
        reauth=branches[0].find("choose/when")
        self.assertIn('personal',reauth.get('condition'))
        self.assertEqual(branches[0].find("set-variable[@name='gatewayConnectionChallengeReason']").get('value'),'token_rejected')
        self.assertEqual(reauth.find("send-request/set-url").text,'{{core-credential-broker-url}}/v1/challenge')

    def test_recoverable_session_starts_authentication_and_sends_verified_subscription(self):
        branch=next(n for n in self.use.findall('choose/when') if n.find(".//send-request[@response-variable-name='gatewayConnectionChallengeResponse']") is not None)
        auth=branch.find('choose/when')
        self.assertIn('409',auth.get('condition'))
        request=auth.find('send-request')
        self.assertEqual(request.find("set-header[@name='X-Core-Subscription-Id']/value").text,'@(context.Subscription.Id)')
        body=request.find('set-body').text
        self.assertIn('selection["profileName"]',body)
        self.assertIn('gatewayConnectionRequestSession',body)
        self.assertNotIn('context.Request.Body',body)
        response=branch.find('return-response')
        self.assertIn('body["authentication"]',response.find('set-body').text)
        self.assertEqual(response.find("set-header[@name='Referrer-Policy']/value").text,'no-referrer')

    def test_twelve_pool_fragments_fit_the_apim_fragment_size_limit(self):
        from test_credential_pool_deployment import configured
        telemetry=(POLICIES/'gateway-telemetry.xml').read_text(encoding='utf-8').replace('<fragment>','').replace('</fragment>','')
        for filename in ('credential-connection.xml','credential-session-use.xml','credential-connection-outbound.xml'):
            rendered=credential_login.render_connection(filename,credential_login.settings(configured()),telemetry)
            self.assertLess(len(rendered.encode('utf-8')),32768,filename)
            self.assertNotIn('__CREDENTIAL_',rendered)

    def test_cm_token_only_replaces_the_downstream_authorization_header(self):
        header=self.use.find("set-header[@name='Authorization']")
        self.assertEqual(header.get("exists-action"),"override")
        self.assertIn("gatewayConnectionContext",header.find("value").text)
        for name in ("X-Core-Connection-Session","X-Core-Connection-Poll","X-Connection-Id","X-Provider-Id"):
            self.assertEqual(self.use.find(f"set-header[@name='{name}']").get("exists-action"),"delete")
        self.assertEqual(self.inbound.findall(".//cache-lookup-value"),[])

    def test_expiry_comes_from_cm_public_metadata(self):
        expression=self.use.find("set-variable[@name='gatewayConnectionExpiresOn']").get("value")
        self.assertIn("expires_on",expression)
        self.assertNotIn("AccessToken",expression)
        self.assertNotIn("expires_in",expression)
        self.assertNotIn("AsJwt",expression)

    def test_rejections_have_audit_codes_without_raw_credentials(self):
        for root in (self.inbound,self.use,self.outbound):
            self.assertIsNone(root.find(".//include-fragment"))
            for when in root.findall("choose/when"):
                if when.find("return-response") is None:
                    continue
                self.assertIsNotNone(when.find("set-variable[@name='gatewayAuditStatus']"))
                self.assertTrue(any(code in when.find("set-variable[@name='gatewayAuditCode']").get("value")
                              for code in ("CREDENTIAL_CONNECTION_DENIED","CONNECTION_REAUTHORIZATION_REQUIRED","CONNECTION_PERMISSION_DENIED","CONNECTION_CLAIMS_REQUIRED","SESSION_UPDATE_REQUIRED")))
                self.assertEqual(when.find("trace").get("source"),"core-gateway")

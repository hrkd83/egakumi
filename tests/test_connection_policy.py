"""CM 接続の追加条件と下流エラー変換の公開契約を検証する。"""
import base64
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from deployment.helpers import credential_login
from deployment.helpers.apim_audit import render_audit_policy
from test_credential_login import settings
from policy_fixture_paths import policy_fixture_path

CORE=Path(__file__).resolve().parents[1]
POLICIES=CORE/"apps/apim/policies"


class ConnectionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        telemetry=policy_fixture_path(POLICIES,"core-gateway-telemetry.xml").read_text(encoding="utf-8").replace("<fragment>","").replace("</fragment>","")
        cls.inbound=ET.fromstring(credential_login.render_connection("credential-connection.xml",settings(),telemetry))
        cls.use=ET.fromstring(credential_login.render_connection("credential-session-use.xml",settings(),telemetry))
        cls.outbound=ET.fromstring(credential_login.render_connection("credential-connection-outbound.xml",settings(),telemetry))
        cls.caller_error=ET.parse(POLICIES/"snippets/authentication/credential-caller-error/credential-caller-error.xml").getroot()

    def test_subscription_session_selection_precedes_cm_acquisition(self):
        children=list(self.inbound)
        self.assertEqual(children[0].get("name"),"credentialCallerId")
        self.assertEqual(children[1].get("name"),"gatewayConnectionSelection")
        self.assertIsNone(self.inbound.find("choose[@id='core-credential-caller-required']"))
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

    def test_internal_caller_error_is_audited_by_the_api_before_session_use(self):
        guard=self.caller_error.find("choose[@id='core-credential-caller-required']")
        when=guard.find('when')
        self.assertEqual(when.find('include-fragment').get('fragment-id'),'core-gateway-telemetry')
        self.assertEqual(len(when.findall('include-fragment')),1)
        self.assertEqual(when.find("set-variable[@name='gatewayAuditPhase']").get('value'),'policy-error')
        self.assertEqual(when.find("set-variable[@name='gatewayAuditCode']").get('value'),'AUTHENTICATION_PROCESSING_ERROR')
        response=when.find('return-response')
        self.assertEqual(response.find('set-status').attrib,{'code':'500','reason':'Gateway processing failed'})
        self.assertIsNone(response.find('set-body'))
        self.assertEqual(response.find("set-header[@name='Cache-Control']/value").text,'no-store')
        gateway=ET.parse(POLICIES/'api-templates/rest/rest.xml').getroot()
        connection=gateway.find(".//include-fragment[@fragment-id='core-credential-connection']")
        self.assertIn('__CREDENTIAL_CALLER_ERROR_XML__',connection.tail)
        parent=next(node for node in gateway.iter() if connection in list(node))
        self.assertEqual(list(parent)[list(parent).index(connection)+1].get('fragment-id'),'core-credential-session-use')
        # 外部MCPも同じsnippetを同じ接続→sessionの間に展開します。
        publication=(CORE/'apps/apim/api-publication.bicep').read_text(encoding='utf-8')
        mcp=next(line for line in publication.splitlines() if line.lstrip().startswith('credentialInbound:'))
        self.assertLess(mcp.index('core-credential-connection'),mcp.index('${credentialCallerErrorXml}'))
        self.assertLess(mcp.index('${credentialCallerErrorXml}'),mcp.index('core-credential-session-use'))
        accepted=gateway.find("inbound/set-variable[@name='gatewayAuditPhase'][@value='accepted']")
        self.assertIsNotNone(accepted)
        inbound=list(gateway.find('inbound'))
        cm=next(node for node in inbound if connection in node.iter())
        self.assertLess(inbound.index(cm),inbound.index(accepted))

    def test_sql_use_keeps_the_verified_selection_gate_without_a_second_identity_guard(self):
        call=self.use.find(".//send-request[@response-variable-name='gatewayConnectionUseResponse']")
        guard=self.use.find('choose/when')
        self.assertIn('["allowed"]',guard.get('condition'))
        self.assertIn('personal',guard.get('condition'))
        self.assertIn(call,list(guard))
        self.assertFalse(any('gatewayConnectionIdentityStatus' in node.get('condition','') for node in self.use.iter('when')))
        self.assertIsNone(self.inbound.find(".//set-variable[@name='gatewayVerifiedConnectionSubject']"))

    def test_legacy_sql_use_basename_renders_the_canonical_xml_and_csharp(self):
        source=POLICIES/'fragments/authentication/core-credential-session-use/core-credential-session-use.xml'
        expected=ET.fromstring(render_audit_policy(source.read_text(encoding='utf-8'),source_comments='compact'))
        path=".//set-variable[@name='gatewayConnectionUseResult']"
        self.assertEqual(self.use.find(path).get('value'),expected.find(path).get('value'))
        self.assertFalse((POLICIES/'credential-session-use.xml').exists())

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
        self.assertIn('context.Subscription.Id',request.find("set-header[@name='X-Core-Subscription-Id']/value").text)
        caller=request.find("set-header[@name='X-Core-Caller-Id']")
        self.assertIn('credentialCallerId',caller.find('value').text)
        self.assertIn('delete',caller.get('exists-action'))
        body=request.find('set-body').text
        self.assertIn('selection["profileName"]',body)
        self.assertIn('gatewayConnectionRequestSession',body)
        self.assertNotIn('context.Request.Body',body)
        response=branch.find('return-response')
        self.assertIn('body["authentication"]',response.find('set-body').text)
        self.assertEqual(response.find("set-header[@name='Referrer-Policy']/value").text,'no-referrer')

    def test_twelve_pool_fragments_fit_the_apim_fragment_size_limit(self):
        from test_credential_pool_deployment import configured
        telemetry=policy_fixture_path(POLICIES,'core-gateway-telemetry.xml').read_text(encoding='utf-8').replace('<fragment>','').replace('</fragment>','')
        for filename in ('credential-connection.xml','credential-session-use.xml','credential-connection-outbound.xml'):
            rendered=credential_login.render_connection(filename,credential_login.settings(configured()),telemetry)
            self.assertLess(len(rendered.encode('utf-8')),32768,filename)
            self.assertNotIn('__CREDENTIAL_',rendered)

    def test_provider_growth_does_not_expand_the_connection_validation_expression(self):
        from test_credential_pool_deployment import configured
        roots=[self.inbound,ET.fromstring(credential_login.render_connection(
            'credential-connection.xml',credential_login.settings(configured())))]
        values=[]
        for root in roots:
            constant=root.find(".//set-variable[@name='gatewayConnectionProviderSettings']").get('value')
            self.assertFalse(constant.startswith('@'))
            self.assertIn('providers',json.loads(base64.b64decode(constant)))
            validation=root.findall(".//set-variable[@name='gatewayConnectionSelection']")[1].get('value')
            self.assertIn('context.Variables["gatewayConnectionProviderSettings"]',validation)
            self.assertNotIn(constant,validation)
            values.append(validation)
        self.assertEqual(values[0],values[1])

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

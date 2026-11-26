"""Observe only the strict successful PII child GUID; keep protocol behavior."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import sys
import types
import unittest
from unittest import mock

import integration_client as probe
from operations import apim_phase12_transports as transports

CHILD_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OUTER_ID = "11111111-1111-4111-8111-111111111111"
RAW_TEXT = "SYNTHETIC_RAW_TOOL_TEXT_NOT_FOR_EVIDENCE"
RAW_INPUT = "SYNTHETIC_RAW_TOOL_INPUT_NOT_FOR_EVIDENCE"
URL = "https://gateway.example.com/tools/pii/mcp/v1/mcp"


def pii_value(request_id=CHILD_ID):
    return {"redactedText":RAW_TEXT,"language":"en","detectedCount":1,
            "modelVersion":"2025-11-01","warningCodes":[],"requestId":request_id}


async def sdk_probe(value=None, *, strict=True, text_fallback=False, content=None,
                    is_error=False, exit_error=False):
    calls=[]
    clients=[]
    value=pii_value() if value is None else value
    schema={"type":"object","properties":{"text":{"type":"string"},"language":{"type":"string"}},
            "required":["text","language"]}
    class FakeClient:
        def __init__(self,**kwargs):
            self.hooks=kwargs["event_hooks"]
            clients.append(self)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
    class FakeSession:
        def __init__(self,*args,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):
            if exit_error:raise RuntimeError(RAW_TEXT)
            return False
        async def initialize(self):
            return types.SimpleNamespace(capabilities=types.SimpleNamespace(tools={}))
        async def list_tools(self):
            return types.SimpleNamespace(tools=[types.SimpleNamespace(name="pii_redact_text",input_schema=schema)],next_cursor=None)
        async def call_tool(self,name,arguments):
            request=types.SimpleNamespace(method="POST",url=URL,extensions={},headers={},
                content=json.dumps({"method":"tools/call","params":{"name":name,"arguments":arguments}}).encode())
            await clients[0].hooks["request"][0](request)
            response=types.SimpleNamespace(request=request,status_code=200,
                headers={"Content-Type":"application/json","apim-request-id":OUTER_ID})
            await clients[0].hooks["response"][0](response)
            calls.append((name,arguments))
            blocks=content if content is not None else [types.SimpleNamespace(type="text",text=json.dumps(value))] if text_fallback else []
            return types.SimpleNamespace(structured_content=None if text_fallback or content is not None else value,
                                         content=blocks,is_error=is_error)
    @asynccontextmanager
    async def fake_stream(*args,**kwargs):yield object(),object()
    http=types.ModuleType("httpx2")
    http.AsyncClient=FakeClient
    http.AsyncHTTPTransport=lambda **kwargs:object()
    session=types.ModuleType("mcp.client.session")
    session.ClientSession=FakeSession
    stream=types.ModuleType("mcp.client.streamable_http")
    stream.streamable_http_client=fake_stream
    with mock.patch.dict(sys.modules,{"httpx2":http,"mcp.client.session":session,"mcp.client.streamable_http":stream}), \
         mock.patch.object(probe,"verified_tls_context",return_value=object()):
        result=await probe.mcp_probe(URL,{"text":RAW_INPUT,"language":"en"},
            {"Authorization":"Bearer <SYNTHETIC_TOKEN>"},strict_tool_contract=strict)
    return result,calls


def protocol_success():
    return {"status":200,"initialized":True,"toolsCapabilityAdvertised":True,"toolsListReceived":True,
        "toolsAdvertisedCount":1,"toolsListMorePages":False,"toolPresent":True,"toolInputFieldsValid":True,
        "toolPostCount":1,"toolContractCheckedBeforeCall":True,"contractShapeValid":True,
        "normalTerminal":True,"succeeded":True,"piiResultRequestId":CHILD_ID,
        "toolDescriptors":[{"expectedNameEquals":True,"inputSchemaObject":True,"textPropertyPresent":True,
            "languagePropertyPresent":True,"textPropertyString":True,"languagePropertyString":True,
            "requiredFieldsMatch":True,"inputPropertyCount":2}],
        "httpSteps":[{"role":"mcp","httpMethod":"POST","rpcMethod":"tools/call","status":200,
            "contentType":"application/json","correlationIds":{"apim-request-id":OUTER_ID}}]}


class PiiRequestIdClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_structured_and_single_text_results_keep_only_child_guid(self):
        for fallback in (False,True):
            with self.subTest(text_fallback=fallback):
                result,calls=await sdk_probe(text_fallback=fallback)
                self.assertTrue(result["succeeded"])
                self.assertEqual(result["piiResultRequestId"],CHILD_ID)
                self.assertEqual(result["httpSteps"][0]["correlationIds"],{"apim-request-id":OUTER_ID})
                self.assertEqual(len(calls),1)
                self.assertNotIn(RAW_TEXT,json.dumps(result))
                self.assertNotIn(RAW_INPUT,json.dumps(result))
                self.assertNotIn("<SYNTHETIC_TOKEN>",json.dumps(result))
    async def test_default_probe_has_same_success_without_new_id(self):
        result,calls=await sdk_probe(strict=False)
        self.assertTrue(result["succeeded"])
        self.assertNotIn("piiResultRequestId",result)
        self.assertEqual(len(calls),1)
    async def test_strict_guid_is_canonical_nonzero_without_extra_accepted_forms(self):
        valid,calls=await sdk_probe(pii_value(CHILD_ID.upper()))
        self.assertEqual(valid["piiResultRequestId"],CHILD_ID)
        bad=("00000000-0000-0000-0000-000000000000","bad-id",CHILD_ID.replace("-",""),
             "{"+CHILD_ID+"}","urn:uuid:"+CHILD_ID," "+CHILD_ID,CHILD_ID+"\n","ａａａａａａａａ-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        for value in bad:
            with self.subTest(value=value):
                result,calls=await sdk_probe(pii_value(value))
                self.assertTrue(result["succeeded"])
                self.assertNotIn("piiResultRequestId",result)
                self.assertEqual(len(calls),1)
    async def test_error_inexact_or_missing_pii_shape_never_provides_a_child(self):
        missing=pii_value();missing.pop("requestId")
        for value,options in ((pii_value(),{"is_error":True}),({**pii_value(),"arbitrary":RAW_TEXT},{}),
                              ({"requestId":CHILD_ID},{}),(missing,{}),(pii_value(None),{})):
            with self.subTest(options=options,fields=list(value)):
                result,calls=await sdk_probe(value,**options)
                self.assertFalse(result["succeeded"])
                self.assertNotIn("piiResultRequestId",result)
                self.assertEqual(len(calls),1)
                self.assertNotIn(RAW_TEXT,json.dumps(result))
    async def test_sdk_cleanup_failure_removes_observation_without_tool_replay(self):
        result,calls=await sdk_probe(exit_error=True)
        self.assertFalse(result["succeeded"])
        self.assertNotIn("piiResultRequestId",result)
        self.assertEqual(len(calls),1)
        self.assertNotIn(RAW_TEXT,json.dumps(result))
    async def test_multiple_text_parts_are_not_searched_for_arbitrary_ids(self):
        content=[types.SimpleNamespace(type="text",text=json.dumps(pii_value())),
                 types.SimpleNamespace(type="text",text=json.dumps({"requestId":CHILD_ID}))]
        result,calls=await sdk_probe(content=content)
        self.assertFalse(result["succeeded"])
        self.assertNotIn("piiResultRequestId",result)
        self.assertEqual(len(calls),1)


class PiiRequestIdSanitizerTests(unittest.IsolatedAsyncioTestCase):
    async def sanitized(self,raw):
        runner=mock.AsyncMock(return_value=raw)
        case=transports.Case("pii-v1-mcp",URL,"api-key","mcp")
        result=await transports.mcp_probe(case,{},runner=runner)
        return result,runner
    async def test_sanitized_child_id_remains_distinct_from_outer_headers(self):
        raw=protocol_success();raw["piiResultRequestId"]=CHILD_ID.upper()
        raw.update(body=RAW_TEXT,nested={"requestId":CHILD_ID},content=RAW_TEXT,requestId=OUTER_ID)
        result,runner=await self.sanitized(raw)
        self.assertTrue(result["passed"])
        self.assertEqual(result["piiResultRequestId"],CHILD_ID)
        self.assertEqual(result["httpSteps"][0]["correlationIds"],{"apim-request-id":OUTER_ID})
        self.assertNotIn(RAW_TEXT,json.dumps(result))
        self.assertNotIn("nested",result)
        self.assertNotIn("body",result)
        self.assertEqual(runner.await_count,1)
    async def test_missing_malformed_or_nil_id_leaves_existing_protocol_success(self):
        for value in (None,True,1,[],{},"bad-id",CHILD_ID.replace("-",""),"00000000-0000-0000-0000-000000000000"):
            with self.subTest(value=value):
                raw=protocol_success();raw["piiResultRequestId"]=value
                result,runner=await self.sanitized(raw)
                self.assertTrue(result["passed"])
                self.assertNotIn("piiResultRequestId",result)
                self.assertEqual(runner.await_count,1)
        raw=protocol_success();raw.pop("piiResultRequestId");raw["nested"]={"requestId":CHILD_ID}
        result,runner=await self.sanitized(raw)
        self.assertTrue(result["passed"])
        self.assertNotIn("piiResultRequestId",result)
    async def test_failed_or_replayed_protocol_summary_cannot_supply_a_child(self):
        for change in ({"succeeded":False},{"normalTerminal":False},{"contractShapeValid":False},
                       {"toolPostCount":2},{"toolContractCheckedBeforeCall":False}):
            raw={**protocol_success(),**change}
            with self.subTest(change=change):
                result,runner=await self.sanitized(raw)
                self.assertFalse(result["passed"])
                self.assertNotIn("piiResultRequestId",result)
                self.assertEqual(runner.await_count,1)
    async def test_actual_sdk_summary_survives_wrapper_without_raw_body(self):
        raw,calls=await sdk_probe()
        result,runner=await self.sanitized(raw)
        self.assertTrue(result["passed"])
        self.assertEqual(result["piiResultRequestId"],CHILD_ID)
        self.assertEqual(len(calls),1)
        self.assertEqual(runner.await_count,1)
        self.assertNotIn(RAW_TEXT,json.dumps(result))

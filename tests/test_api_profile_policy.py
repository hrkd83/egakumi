"""統合APIの実C#ポリシーを実行し、購読と操作別の入力制約を確認する。

ネットワークやAzureを呼ばず、profile選択・購読の認可・WSモデル選択と
選択後の既存上限検査を、本番XMLから取り出した式で検証する。
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from test_api_protocols import CSHARP as EXISTING_CSHARP, actual_expression, bound_coverage, configuration
from test_gateway_limits_telemetry import LIMIT_EXPRESSION, PWSH


CORE = Path(__file__).resolve().parents[1]
CLASSES = EXISTING_CSHARP.split("public static class ExpandedPolicyFixture")[0].replace(
    'public class Body { public string Data; public int Reads; public T As<T>(bool preserveContent=false) { Reads++; return (T)(object)JObject.Parse(Data); } }',
    '''public class Body {
 public string Data; public int Reads;
 public T As<T>(bool preserveContent=false) {
  Reads++;
  if (!preserveContent) throw new Exception("Request body was consumed");
  return typeof(T)==typeof(byte[]) ? (T)(object)Encoding.UTF8.GetBytes(Data) : (T)(object)JObject.Parse(Data);
 }
}''')

SOURCE = CLASSES + r'''
public static class IntegratedProfileFixture {
 public static bool InferenceRequest(Context context) { __INFERENCE_REQUEST__ }
 public static string Profile(Context context) { __PROFILE__ }
 public static string Auth(Context context) { __AUTH__ }
 public static string Limits(Context context) { __LIMITS__ }
 public static bool Target(Context context) { __TARGET__ }
 public static string Model(Context context) { __MODEL__ }
 public static string NormalizeModel(Context context) { __NORMALIZE_MODEL__ }
 public static string ProtocolAuth(Context context) { __PROTOCOL_AUTH__ }
 public static bool Query(Context context) { __QUERY__ }
 public static string Run(string input) {
  var results = new JObject();
  foreach (JObject item in JArray.Parse(input)) {
   var c = new Context();
   c.Api.Id = (string)item["api"] ?? "synthetic-api";
   c.Operation.Id = (string)item["operation"] ?? "chat-completions";
   c.Operation.UrlTemplate = (string)item["path"] ?? "/responses";
   c.Request.Method = (string)item["method"] ?? "POST";
   c.Request.Body = new Body {Data=item["rawBody"]!=null?(string)item["rawBody"]:(item["body"]??new JObject()).ToString(Formatting.None)};
   c.Variables["config"] = item["config"].ToString(Formatting.None);
   c.Variables["websocket"] = (item["websocket"]??new JObject()).ToString(Formatting.None);
   c.Variables["gatewayKeyHeader"] = "api-key";
   foreach(var entry in ((JObject)(item["headers"]??new JObject())).Properties()) c.Request.Headers[entry.Name]=entry.Value is JArray?entry.Value.Select(x=>(string)x).ToArray():new[] {(string)entry.Value};
   foreach(var entry in ((JObject)(item["query"]??new JObject())).Properties()) c.Request.Url.Query[entry.Name]=entry.Value is JArray?entry.Value.Select(x=>(string)x).ToArray():new[] {(string)entry.Value};
   foreach(var entry in ((JObject)(item["parameters"]??new JObject())).Properties()) c.Request.MatchedParameters[entry.Name]=(string)entry.Value;
   if(item["key"]!=null) { c.Subscription=new Named {Id=(string)item["key"]};c.Product=new Named {Id="tool-users"};c.Request.Headers["api-key"]=new[] {"fixture-value"}; }
   var result=new JObject();
   if((string)item["mode"]=="ws") {
    var model=Model(c);c.Variables["gatewayProtocolModel"]=model;
    result["model"]=model;
    result["decision"]=JObject.Parse(ProtocolAuth(c));
    result["queryAllowed"]=Query(c);
   } else {
    result["requestAllowed"]=InferenceRequest(c);
    result["queryAfter"]=JObject.FromObject(c.Request.Url.Query);
    if (!(bool)result["requestAllowed"]) {
     result["reads"]=c.Request.Body.Reads;
     result["bodyAfter"]=c.Request.Body.Data;
     results[(string)item["id"]]=result;
     continue;
    }
    var selection=JObject.Parse(Profile(c));c.Variables["gatewayProfileSelection"]=selection.ToString(Formatting.None);
    result["profile"]=selection;
    if((bool)selection["allowed"]) {
     result["decision"]=JObject.Parse(Auth(c));
     c.Variables["gatewayOperationConstraint"]=selection["constraint"].ToString(Formatting.None);
     c.Variables["gatewayEffectiveModel"]=(string)selection["effectiveModel"]??"";
     result["limits"]=JObject.Parse(Limits(c));
     result["targetAllowed"]=Target(c);
     result["bodyBeforeNormalization"]=c.Request.Body.Data;
     if ((bool)result["decision"]["allowed"] && (bool)result["limits"]["allowed"] && (bool)result["targetAllowed"]
         && !String.IsNullOrEmpty((string)selection["effectiveModel"]) && (string)selection["requestedModel"]!=(string)selection["effectiveModel"]) {
      c.Request.Body.Data=NormalizeModel(c);
     }
    }
   }
   result["reads"]=c.Request.Body.Reads;
   result["bodyAfter"]=c.Request.Body.Data;
   results[(string)item["id"]]=result;
  }
  return results.ToString(Formatting.None);
 }
}
'''


def case(name, **patch):
    profiles = [{"profileId": profile, "kind": "bodyModel", "bodyFormat": "json", "maxRequestBytes": size,
                 "allowedModels": models, "modalities": modalities,
                 "maxOutputTokens": 32, "outputTokenParameter": "max_completion_tokens"}
                for profile, size, models, modalities in (
                    ("chat", 512, ["shared"], ["text"]),
                    ("vision", 2048, ["shared"], ["text", "image"]),
                    ("audio-chat", 2048, ["audio"], ["text", "audio"]),
                    ("pdf", 2048, ["shared"], ["text", "file"]))]
    config = {"mode": "subscriptionKey",
              "operationConstraints": {"chat-completions": {"kind": "bodyModel", "defaultProfile": "chat", "profiles": profiles}}}
    item = {"id": name, "config": config, "key": "subscription-fixture",
            "body": {"model": "shared", "messages": [{"role": "user", "content": "synthetic"}], "max_completion_tokens": 16}}
    item.update(patch)
    return item


class ProfilePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("Actual C# policy execution requires PowerShell")
        cases = [case("default"), case("explicit", headers={"X-Egakumi-Profile": "vision"}),
                 case("unknown", headers={"X-Egakumi-Profile": "unknown"}),
                 case("duplicate", headers={"X-Egakumi-Profile": ["chat", "vision"]}),
                 case("wrong-model", headers={"X-Egakumi-Profile": "vision"}, body={"model": "audio"}),
                 case("huge-malformed", rawBody="{" + "x" * 2049)]
        # 実際の登録例から取り出したprofileで、モデルと出力項目の組を実行検査します。
        apis = {a["name"]: a for a in configuration.normalize_configuration(bound_coverage(), base_directory=CORE / "config")["apiDefinitions"]}
        inference = apis["openai-inference-v1"]["authorization"]
        for model in ("DeepSeek-V4-Flash", "DeepSeek-V4-Pro", "Mistral-Large-3", "grok-4.7"):
            cases.append(case(model, config=inference, headers={"X-Egakumi-Profile": "compatible"}, body={"model": model, "max_tokens": 64}))
        cases.append(case("compatible-wrong-token-field", config=inference, headers={"X-Egakumi-Profile": "compatible"}, body={"model": "grok-4.7", "max_completion_tokens": 64}))
        cases.append(case("router", config=inference, headers={"X-Egakumi-Profile": "router"}, body={"model": "model-router", "max_completion_tokens": 64}))
        flux = apis["blackforest-images-v1"]["authorization"]
        flux = dict(flux, operationConstraints={"chat-completions": flux["operationConstraints"]["flux2-generate"]})
        cases.append(case("flux-pro", config=flux, headers={"X-Egakumi-Profile": "pro"}, body={"model": "FLUX.2-pro", "prompt": "synthetic"}))
        cases.append(case("flux-wrong-profile", config=flux, headers={"X-Egakumi-Profile": "flex"}, body={"model": "FLUX.2-pro", "prompt": "synthetic"}))
        # Use a small synthetic bound to exercise equality/overflow without allocating
        # the actual Classic APIM 500 MiB buffer ceiling in a unit fixture.
        for profile in flux["operationConstraints"]["chat-completions"]["profiles"]:
            profile["maxRequestBytes"] = 2048
        maximum = 2048
        for excess in (0, 1):
            body = json.dumps({"model": "FLUX.2-flex", "prompt": ""}, separators=(",", ":"))
            body = body.replace('"prompt":""', '"prompt":"' + "a" * (maximum + excess - len(body.encode("utf-8"))) + '"')
            cases.append(case("flux-byte-limit-" + str(excess), config=flux, headers={"X-Egakumi-Profile": "flex"}, rawBody=body))
        image = {"model": "shared", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}], "max_completion_tokens": 16}
        for name, headers in (("image-default", {}), ("image-authorized", {"X-Egakumi-Profile": "vision"})):
            cases.append(case(name, headers=headers, body=image))
        cases.append(case("audio-default", body={"model": "shared", "modalities": ["text", "audio"], "max_completion_tokens": 16}))
        cases.append(case("file-default", body={"model": "shared", "messages": [{"content": [{"type": "file", "file": {"file_data": "AA=="}}]}]}))
        # Use the shipped default Responses profile, with no special profile
        # header, to reproduce Codex tool schemas and native multimodal content.
        responses = dict(inference, operationConstraints={"chat-completions": inference["operationConstraints"]["responses-create"]})
        tool_body = {"model": "gpt-6.1-sol", "input": "synthetic text", "tools": [{"type": "function", "name": "synthetic_tool",
            "parameters": {"type": "object", "properties": {"image": {"type": "string"}, "audio": {"type": "string"},
                "file": {"type": "object", "properties": {"type": {"const": "input_image"}}}}}}]}
        cases.append(case("responses-tool-schema", config=responses, body=tool_body))
        image_body = {"model": "gpt-6.1-sol", "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]}]}
        cases.append(case("responses-default-image", config=responses, body=image_body))
        cases.append(case("responses-image-unknown-model", config=responses, body={**image_body, "model": "unapproved-synthetic-model"}))
        # Execute the shipped alias through profile, auth, original-byte limits,
        # target checks and the actual model-only normalization expression.
        alias_body = {"model": "codex-auto-review", "input": [{"type": "reasoning", "encrypted_content": "synthetic opaque"},
            {"type": "function_call_output", "call_id": "synthetic_call", "output": "synthetic output"}],
            "stream": True, "max_output_tokens": 32, "text": {"format": {"type": "json_schema", "name": "synthetic_review",
            "schema": {"type": "object", "properties": {"approved": {"type": "boolean"}}, "required": ["approved"], "additionalProperties": False}}}}
        cls.alias_body = alias_body
        alias_base = {"config": inference, "api": "openai-inference-v1", "operation": "responses-create", "path": "/responses",
            "headers": {"Content-Type": "application/json"}, "body": alias_body}
        cases.append(case("alias-default", **alias_base))
        cases.append(case("alias-explicit", **{**alias_base, "headers": {"Content-Type": "application/json", "X-Egakumi-Profile": "response"}}))
        cases.append(case("alias-direct", **{**alias_base, "body": {**alias_body, "model": "gpt-5.6-luna"}}))
        cases.append(case("alias-unknown", **{**alias_base, "body": {**alias_body, "model": "unknown-alias"}}))
        cases.append(case("alias-pdf", **{**alias_base, "headers": {"Content-Type": "application/json", "X-Egakumi-Profile": "pdf"}}))
        cases.append(case("alias-chat", config=inference, api="openai-inference-v1", path="/chat/completions",
            headers={"Content-Type": "application/json"}, body={"model": "codex-auto-review", "messages": []}))
        cases.append(case("alias-no-key", **{**alias_base, "key": None}))
        for label, patch in (("state", {"previous_response_id": "synthetic"}), ("token-limit", {"max_output_tokens": 9223372036854775807}),
                ("wrong-token", {"max_tokens": 1})):
            cases.append(case("alias-reject-" + label, **{**alias_base, "body": {**alias_body, **patch}}))
        bounded = json.loads(json.dumps(inference))
        for profile in bounded["operationConstraints"]["responses-create"]["profiles"]:
            profile["maxRequestBytes"] = 2048
        # Whitespace must count before any JSON compaction/model rewrite.
        cases.append(case("alias-wire-overflow", **{**alias_base, "config": bounded, "rawBody": " " * 2048 + json.dumps(alias_body)}))
        bounded["operationConstraints"]["responses-create"]["profiles"][0]["maxRequestBytes"] = 256
        cases.append(case("alias-profile-overflow", **{**alias_base, "config": bounded}))
        cases.append(case("selected-size", body={"model": "shared", "messages": [{"content": "x" * 600}], "max_completion_tokens": 16}))
        native_body = {"model": "gpt-6.1-sol", "stream": True,
            "provider_extension": {"arbitrary": ["SELECT * FROM synthetic;", "<?php $function(); ?>"]},
            "tools": [{"type": "function", "name": "synthetic_tool", "parameters": {
                "type": "object", "properties": {"arbitrary-code-key": {"type": "string"}},
                "additionalProperties": True}}]}
        for label, operation, path, data in (
            ("chat", "chat-completions", "/chat/completions", {"messages": [{"role": "tool", "content": "synthetic code"}]}),
            ("responses", "responses-create", "/responses", {"input": [
                {"type": "function_call_output", "call_id": "synthetic_call", "output": "synthetic code"},
                {"type": "reasoning", "encrypted_content": "synthetic opaque value"}]})):
            base = {"config": inference, "api": "openai-inference-v1", "operation": operation,
                "path": path, "body": {**native_body, **data}, "headers": {"Content-Type": "application/json"}}
            cases.append(case(label + "-native-request", **base))
            cases.append(case(label + "-json-charset", **{**base, "headers": {"Content-Type": "Application/JSON; charset=utf-8"}}))
            cases.append(case(label + "-revision-query", **{**base, "api": "openai-inference-v1;rev=2", "query": {"input.input": "synthetic"}}))
            for suffix, patch in (
                ("query", {"query": {"arbitrary": "synthetic"}}),
                ("same-name-query", {"query": {"input.output.text": "synthetic"}}),
                ("duplicate-query", {"query": {"messages.content": ["synthetic", "second"]}}),
                ("query-before-body", {"query": {"input": "synthetic"}, "rawBody": "{"}),
                ("method", {"method": "GET"}),
                ("missing-type", {"headers": {}}),
                ("wrong-type", {"headers": {"Content-Type": "text/plain"}}),
                ("duplicate-type", {"headers": {"Content-Type": ["application/json", "application/json"]}})):
                cases.append(case(label + "-reject-" + suffix, **{**base, **patch}))
            cases.append(case(label + "-other-api", **{**base, "api": "another-api", "query": {"provider-option": "synthetic"}, "headers": {"Content-Type": "text/plain"}}))
            cases.append(case(label + "-other-operation", **{**base, "path": "/different-operation", "query": {"provider-option": "synthetic"}}))
        multipart = ('--synthetic-image-boundary\r\nContent-Disposition: form-data; name="prompt"\r\n\r\n'
            'Keep the synthetic <script> example as text.\r\n--synthetic-image-boundary\r\n'
            'Content-Disposition: form-data; name="image"; filename="synthetic.png"\r\n'
            'Content-Type: image/png\r\n\r\nsynthetic image bytes\r\n--synthetic-image-boundary--\r\n')
        image_contracts = [
            ("openai-image", "openai-images-v1", "image-generate", "/generations", "json", None),
            ("flux-generate", "blackforest-images-v1", "flux2-generate", "/flux-2/generations", "json", None),
            ("flux-edit", "blackforest-images-v1", "flux2-edit", "/flux-2/edits", "json", None),
        ]
        for deployment in apis["openai-images-v1"]["authorization"]["operationConstraints"]["image-edit"]["allowedModels"]:
            image_contracts.append(("openai-edit-" + deployment, "openai-images-v1", "image-edit", "/deployments/{deployment}/edits", "multipart", deployment))
        cls.image_cases = []
        for label, api, operation, path, body_format, deployment in image_contracts:
            cls.image_cases.append(label)
            media = "multipart/form-data; boundary=synthetic-image-boundary" if body_format == "multipart" else "application/json; charset=utf-8"
            body = ({"model": "gpt-image-2", "prompt": "Draw the synthetic SELECT and <script> examples.", "n": 2,
                "provider_extension": {"synthetic": True}} if api == "openai-images-v1" else {
                "model": "FLUX.2-flex", "prompt": "Draw the synthetic SELECT example.", "input_image": "c3ludGhldGlj",
                "input_image_2": "c2Vjb25k", "provider_extension": {"synthetic": True}})
            base = {"config": apis[api]["authorization"], "api": api, "operation": operation,
                "path": path, "headers": {"Content-Type": media}, "body": body}
            if deployment is not None:
                base.update(rawBody=multipart, parameters={"deployment": deployment})
            cases.append(case(label + "-native-request", **base))
            cases.append(case(label + "-revision-request", **{**base, "api": api + ";rev=2"}))
            for suffix, patch in (
                ("query", {"query": {"arbitrary": "synthetic"}}),
                ("same-name-query", {"query": {"prompt": "synthetic"}}),
                ("base64-query", {"query": {"input_image": "synthetic"}}),
                ("api-version", {"query": {"api-version": "preview"}}),
                ("duplicate-query", {"query": {"prompt": ["synthetic", "second"]}}),
                ("query-before-body", {"query": {"prompt": "synthetic"}, "rawBody": "{"}),
                ("method", {"method": "GET"}),
                ("missing-type", {"headers": {}}),
                ("wrong-type", {"headers": {"Content-Type": "application/json" if body_format == "multipart" else "multipart/form-data; boundary=synthetic"}}),
                ("duplicate-type", {"headers": {"Content-Type": [media, media]}}),
                ("wrong-route", {"path": "/different-operation"})):
                cases.append(case(label + "-reject-" + suffix, **{**base, **patch}))
            # Wrongly configured known operations must not bypass the media guard.
            for suffix, field, value in (("wrong-kind", "kind", "registeredOperation"),
                                          ("wrong-format", "bodyFormat", "json" if body_format == "multipart" else "multipart")):
                changed_config = json.loads(json.dumps(base["config"]))
                changed_config["operationConstraints"][operation][field] = value
                cases.append(case(label + "-reject-" + suffix, **{**base, "config": changed_config}))
            cases.append(case(label + "-other-api", **{**base, "api": "another-api", "query": {"provider-option": "synthetic"}}))
        cls.embedding_cases = []
        cls.embedding_bodies = {}
        for label, api, operation, path, body in (
            ("openai-embedding", "openai-embeddings-v1", "embeddings", "/embeddings", {
                "model": "text-embedding-3-small", "input": ["Synthetic SELECT and <script> examples."],
                "dimensions": 256, "encoding_format": "base64", "provider_extension": {"synthetic": True}}),
            ("cohere-embedding", "cohere-inference-v1", "cohere-embeddings", "/embed", {
                "model": "embed-v-4-0", "texts": ["Synthetic SELECT and <script> examples."],
                "input_type": "search_query", "output_dimension": 256, "embedding_types": ["float"],
                "truncate": "END", "provider_extension": {"synthetic": True}})):
            cls.embedding_cases.append(label)
            cls.embedding_bodies[label] = body
            base = {"config": apis[api]["authorization"], "api": api, "operation": operation,
                "path": path, "headers": {"Content-Type": "application/json"}, "body": body}
            cases.append(case(label + "-native-request", **base))
            cases.append(case(label + "-json-charset", **{**base, "headers": {"Content-Type": "Application/JSON; charset=utf-8"}}))
            cases.append(case(label + "-revision-request", **{**base, "api": api + ";rev=2"}))
            for suffix, patch in (
                ("query", {"query": {"arbitrary": "synthetic"}}),
                ("input-query", {"query": {"input": "synthetic"}}),
                ("texts-query", {"query": {"texts": "synthetic"}}),
                ("duplicate-query", {"query": {"texts": ["synthetic", "second"]}}),
                ("query-before-body", {"query": {"input": "synthetic"}, "rawBody": "{"}),
                ("method", {"method": "GET"}),
                ("missing-type", {"headers": {}}),
                ("wrong-type", {"headers": {"Content-Type": "text/plain"}}),
                ("duplicate-type", {"headers": {"Content-Type": ["application/json", "application/json"]}}),
                ("wrong-route", {"path": "/different-operation"})):
                cases.append(case(label + "-reject-" + suffix, **{**base, **patch}))
            cases.append(case(label + "-revision-query", **{**base, "api": api + ";rev=2", "query": {"texts": "synthetic"}}))
            for suffix, field, value in (("wrong-kind", "kind", "bodyModel"),
                                          ("wrong-format", "bodyFormat", "multipart"),
                                          ("wrong-input-field", "embeddingInputField", "texts" if label == "openai-embedding" else "input")):
                changed = json.loads(json.dumps(base["config"]))
                changed["operationConstraints"][operation][field] = value
                cases.append(case(label + "-reject-" + suffix, **{**base, "config": changed}))
            cases.append(case(label + "-other-api", **{**base, "api": "another-api", "query": {"input": "synthetic"}, "headers": {"Content-Type": "text/plain"}}))
            if label == "openai-embedding":
                for version in ("v1", "preview"):
                    cases.append(case(label + "-version-" + version, **{**base, "query": {"api-version": version}}))
                    cases.append(case(label + "-revision-version-" + version, **{**base, "api": api + ";rev=2", "query": {"api-version": version}}))
                for suffix, query in (
                    ("both-versions", {"api-version": ["v1", "preview"]}),
                    ("duplicate-version", {"api-version": ["v1", "v1"]}),
                    ("empty-version", {"api-version": ""}),
                    ("unknown-version", {"api-version": "synthetic-version"}),
                    ("uppercase-name", {"API-VERSION": "v1"}),
                    ("uppercase-value", {"api-version": "V1"}),
                    ("version-with-input", {"api-version": "v1", "input": "synthetic"})):
                    cases.append(case(label + "-reject-" + suffix, **{**base, "query": query}))
                for suffix, native_input in (("scalar", "Synthetic text"), ("tokens", [1, 2]), ("token-batches", [[1, 2], [3]])):
                    cases.append(case(label + "-input-" + suffix, **{**base, "body": {**body, "input": native_input}}))
            else:
                cases.append(case(label + "-reject-api-version", **{**base, "query": {"api-version": "v1"}}))
        cls.native_contract_cases = []
        cls.native_contract_bodies = {}
        transcription_body = ('--synthetic-audio-boundary\r\nContent-Disposition: form-data; name="prompt"\r\n\r\n'
            'Transcribe the synthetic SELECT and <script> examples.\r\n--synthetic-audio-boundary\r\n'
            'Content-Disposition: form-data; name="file"; filename="synthetic.wav"\r\n'
            'Content-Type: audio/wav\r\n\r\nRIFF synthetic audio\r\n--synthetic-audio-boundary--\r\n')
        for label, api, operation, path, kind, body_format, media, raw in (
            ("rerank", "cohere-inference-v1", "rerank", "/rerank", "rerank", "json", "application/json", None),
            ("transcription", "openai-audio-v1", "audio-transcriptions", "/deployments/{deployment}/transcriptions",
             "pathModel", "multipart", "multipart/form-data; boundary=synthetic-audio-boundary", transcription_body),
            ("speech-tts", "azure-speech-v1", "tts", "/synthesize", "registeredOperation", "text",
             "application/ssml+xml", '<speak version="1.0"><voice name="synthetic">Synthetic &amp; text.</voice></speak>'),
            ("speech-stt", "azure-speech-v1", "stt", "/recognize", "registeredOperation", "binary",
             "audio/wav; codecs=audio/pcm; samplerate=16000", "RIFF synthetic audio")):
            cls.native_contract_cases.append(label)
            config = apis[api]["authorization"]
            body = {"model": "Cohere-rerank-v4.0-fast", "query": "Synthetic SELECT and <script> examples.",
                    "documents": ["Synthetic document"], "provider_extension": {"synthetic": True}}
            base = {"config": config, "api": api, "operation": operation, "path": path,
                    "headers": {"Content-Type": media}, "body": body}
            if raw is not None:
                base["rawBody"] = raw
            if label == "transcription":
                base["parameters"] = {"deployment": config["operationConstraints"][operation]["allowedModels"][0]}
            cls.native_contract_bodies[label] = raw if raw is not None else json.dumps(body, separators=(",", ":"))
            cases.append(case(label + "-native-request", **base))
            mixed_media = media.replace(media.split(";", 1)[0], media.split(";", 1)[0].upper(), 1)
            cases.append(case(label + "-mixed-media", **{**base, "headers": {"Content-Type": mixed_media}}))
            cases.append(case(label + "-revision-request", **{**base, "api": api + ";rev=2"}))
            for suffix, patch in (
                ("query", {"query": {"arbitrary": "synthetic"}}),
                ("excluded-field-query", {"query": {"documents" if label == "rerank" else "prompt": "synthetic"}}),
                ("duplicate-query", {"query": {"input": ["synthetic", "second"]}}),
                ("api-version", {"query": {"api-version": "preview"}}),
                ("query-before-body", {"query": {"input": "synthetic"}, "rawBody": "{"}),
                ("method", {"method": "GET"}),
                ("missing-type", {"headers": {}}),
                ("wrong-type", {"headers": {"Content-Type": "text/plain"}}),
                ("duplicate-type", {"headers": {"Content-Type": [media, media]}}),
                ("combined-type", {"headers": {"Content-Type": media.split(";")[0] + ", " + media.split(";")[0]}}),
                ("wrong-route", {"path": path + "/neighbor"})):
                cases.append(case(label + "-reject-" + suffix, **{**base, **patch}))
            for suffix, field, value in (("wrong-kind", "kind", "bodyModel"),
                                        ("wrong-format", "bodyFormat", "binary" if body_format != "binary" else "text")):
                changed = json.loads(json.dumps(config))
                changed["operationConstraints"][operation][field] = value
                cases.append(case(label + "-reject-" + suffix, **{**base, "config": changed}))
            cases.append(case(label + "-reject-missing-contract", **{**base,
                              "config": {**config, "operationConstraints": {}}}))
            cases.append(case(label + "-revision-query", **{**base, "api": api + ";rev=2", "query": {"input": "synthetic"}}))
            for suffix, patch in (("other-api", {"api": "another-api"}),
                                  ("other-operation", {"operation": "synthetic-other", "path": "/synthetic-other",
                                   "config": {**config, "operationConstraints": {"synthetic-other": config["operationConstraints"][operation]}}})):
                cases.append(case(label + "-" + suffix, **{**base, **patch,
                                  "headers": {"Content-Type": "text/plain"}, "query": {"provider-option": "synthetic"}}))
            if label in ("rerank", "transcription"):
                future = json.loads(json.dumps(config))
                future["operationConstraints"][operation]["allowedModels"] = ["synthetic-future-model"]
                patch = {"body": {**body, "model": "synthetic-future-model"}} if label == "rerank" else {"parameters": {"deployment": "synthetic-future-model"}}
                cases.append(case(label + "-future-model", **{**base, "config": future, **patch}))
        literal = "${jndi:ldap://waf-probe.invalid/synthetic}"
        cls.decision_body = {"model": "decision-1", "state": {literal: {"text": "Synthetic code example"}},
                             "questions": {literal: {"type": "choice", "instructions": "Review the inert source text.",
                                                     "criteria": {literal: "A synthetic source label", "other": "Other"}}}}
        decision_config = apis["microsoft-decision-v1"]["authorization"]
        decision = {"config": decision_config, "api": "microsoft-decision-v1", "operation": "systemone",
                    "path": "/systemone", "headers": {"Content-Type": "application/json"}, "body": cls.decision_body}
        cases.append(case("decision-native-request", **decision))
        cases.append(case("decision-json-charset", **{**decision, "headers": {"Content-Type": "Application/JSON; charset=UTF-8"}}))
        cases.append(case("decision-revision-request", **{**decision, "api": "microsoft-decision-v1;rev=2"}))
        for suffix, patch in (
            ("query", {"query": {"arbitrary": "synthetic"}}),
            ("state-query", {"query": {"state.message": literal}}),
            ("question-key-query", {"query": {"questions." + literal: "synthetic"}}),
            ("duplicate-query", {"query": {"state.message": ["one", "two"]}}),
            ("api-version", {"query": {"api-version": "v1"}}),
            ("query-before-body", {"query": {"state.message": literal}, "rawBody": "{"}),
            ("method", {"method": "GET"}),
            ("missing-type", {"headers": {}}),
            ("wrong-type", {"headers": {"Content-Type": "text/plain"}}),
            ("duplicate-type", {"headers": {"Content-Type": ["application/json", "application/json"]}}),
            ("combined-type", {"headers": {"Content-Type": "application/json, application/json"}}),
            ("wrong-route", {"path": "/systemone/neighbor"})):
            cases.append(case("decision-reject-" + suffix, **{**decision, **patch}))
        for suffix, field, value in (("wrong-kind", "kind", "registeredOperation"),
                                    ("wrong-format", "bodyFormat", "multipart")):
            changed = json.loads(json.dumps(decision_config))
            changed["operationConstraints"]["systemone"][field] = value
            cases.append(case("decision-reject-" + suffix, **{**decision, "config": changed}))
        cases.append(case("decision-reject-missing-contract", **{**decision,
                          "config": {**decision_config, "operationConstraints": {}}}))
        cases.append(case("decision-revision-query", **{**decision, "api": "microsoft-decision-v1;rev=2",
                          "query": {"questions.synthetic.instructions": literal}}))
        cases.append(case("decision-wrong-model", **{**decision, "body": {**cls.decision_body, "model": "unapproved-synthetic-model"}}))
        cases.append(case("decision-other-api", **{**decision, "api": "another-api", "query": {"state.message": literal},
                          "headers": {"Content-Type": "text/plain"}}))
        other_config = {**decision_config, "operationConstraints": {
            "synthetic-other": decision_config["operationConstraints"]["systemone"]}}
        cases.append(case("decision-other-operation", **{**decision, "operation": "synthetic-other", "path": "/synthetic-other",
                          "config": other_config, "query": {"state.message": literal}, "headers": {"Content-Type": "text/plain"}}))
        for name, query, models in (("ws-default", {}, ["standard"]), ("ws-mini", {"model": "mini"}, ["standard", "mini"]),
                                    ("ws-mini-denied", {"model": "mini"}, ["standard"]),
                                    ("ws-unknown", {"model": "unknown"}, ["standard", "mini"]),
                                    ("ws-duplicate", {"model": ["standard", "mini"]}, ["standard", "mini"]),
                                    ("ws-unknown-query", {"model": "mini", "backend": "other"}, ["mini"])):
            item = case(name, mode="ws", query=query, websocket={"allowedModels": models, "defaultModel": "standard", "queryParameters": {"api-version": "fixed"}})
            cases.append(item)
        source = SOURCE.replace("__PROFILE__", actual_expression("gateway.xml", "gatewayProfileSelection"))
        normalize = ET.parse(CORE / "apps/gateway/apim/policies/gateway.xml").find("./inbound/choose[@id='core-model-alias']/when/set-body").text.strip()
        source = source.replace("__NORMALIZE_MODEL__", normalize[2:-1])
        source = source.replace("__INFERENCE_REQUEST__", actual_expression("gateway.xml", "gatewayInferenceRequestAllowed"))
        source = source.replace("__AUTH__", actual_expression("gateway.xml", "gatewayDecision")).replace("__LIMITS__", LIMIT_EXPRESSION).replace("__TARGET__", actual_expression("gateway.xml", "gatewayTargetAllowed"))
        model = actual_expression("protocol.xml", "gatewayProtocolModel").replace(
            'JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(encoded)))', 'JObject.Parse((string)context.Variables["websocket"])').replace('var encoded = "__WEBSOCKET_SETTINGS_BASE64__";', 'var encoded = "fixture";')
        query = actual_expression("protocol.xml", "gatewayProtocolQueryAllowed").replace(
            'JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(encoded)))', 'JObject.Parse((string)context.Variables["websocket"])["queryParameters"] as JObject').replace('var encoded = "__WEBSOCKET_QUERY_BASE64__";', 'var encoded = "fixture";')
        source = source.replace("__MODEL__", model).replace("__PROTOCOL_AUTH__", actual_expression("protocol.xml", "gatewayDecision")).replace("__QUERY__", query)
        with tempfile.TemporaryDirectory(prefix="core-profile-policy-") as temporary:
            path = Path(temporary)
            (path / "policy.cs").write_text(source)
            (path / "cases.json").write_text(json.dumps(cases))
            (path / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[IntegratedProfileFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            run = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path / "run.ps1")],
                                 capture_output=True, text=True, timeout=60,
                                 env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(path / "dotnet")})
            if run.returncode:
                raise AssertionError("Actual integrated policy execution failed:\n" + run.stderr)
            cls.results = json.loads(run.stdout)

    def test_model_alias_resolves_only_within_its_selected_profile(self):
        for name in ("alias-default", "alias-explicit"):
            result = self.results[name]
            self.assertEqual(result["profile"]["requestedModel"], "codex-auto-review")
            self.assertEqual(result["profile"]["effectiveModel"], "gpt-5.6-luna")
            for stage in ("profile", "decision", "limits"):
                self.assertTrue(result[stage]["allowed"], (name, stage))
            self.assertTrue(result["targetAllowed"])
            self.assertEqual(json.loads(result["bodyBeforeNormalization"]), self.alias_body)
            self.assertEqual(json.loads(result["bodyAfter"]), {**self.alias_body, "model": "gpt-5.6-luna"})
        direct = self.results["alias-direct"]
        self.assertEqual(direct["bodyBeforeNormalization"], direct["bodyAfter"])
        for name in ("alias-unknown", "alias-pdf", "alias-chat"):
            self.assertFalse(self.results[name]["profile"]["allowed"], name)
            self.assertEqual(json.loads(self.results[name]["bodyAfter"])["model"], "unknown-alias" if name == "alias-unknown" else "codex-auto-review")

    def test_alias_cannot_bypass_auth_original_wire_bounds_or_target_model_limits(self):
        self.assertFalse(self.results["alias-no-key"]["decision"]["allowed"])
        self.assertFalse(self.results["alias-reject-state"]["targetAllowed"])
        self.assertEqual(self.results["alias-wire-overflow"]["profile"]["status"], 413)
        self.assertEqual(self.results["alias-profile-overflow"]["limits"]["status"], 413)
        self.assertEqual(self.results["alias-reject-token-limit"]["limits"]["code"], "OUTPUT_LIMIT_EXCEEDED")
        self.assertEqual(self.results["alias-reject-wrong-token"]["limits"]["code"], "INVALID_OUTPUT_LIMIT")
        for name in ("alias-no-key", "alias-reject-state", "alias-profile-overflow", "alias-reject-token-limit", "alias-reject-wrong-token"):
            self.assertEqual(json.loads(self.results[name]["bodyAfter"])["model"], "codex-auto-review", name)

    def test_alias_normalization_order_preserves_original_byte_and_token_checks(self):
        inbound = ET.parse(CORE / "apps/gateway/apim/policies/gateway.xml").getroot().find("inbound")
        nodes = list(inbound)
        normalize = inbound.find("choose[@id='core-model-alias']")
        self.assertLess(nodes.index(inbound.find("include-fragment[@fragment-id='core-model-limits']")), nodes.index(normalize))
        self.assertLess(nodes.index(inbound.find("set-variable[@name='gatewayTargetAllowed']")), nodes.index(normalize))
        self.assertLess(nodes.index(normalize), nodes.index(inbound.find("include-fragment[@fragment-id='core-audit-body-model']")))

    def test_profile_selection_preserves_the_authenticated_caller(self):
        self.assertTrue(self.results["default"]["decision"]["allowed"])
        self.assertTrue(self.results["explicit"]["decision"]["allowed"])
        self.assertEqual(self.results["explicit"]["decision"]["profileId"], "vision")
        self.assertEqual(self.results["default"]["decision"]["callerId"], self.results["explicit"]["decision"]["callerId"])

    def test_openai_inference_query_and_media_contract_rejects_before_reading_body(self):
        for label in ("chat", "responses"):
            for suffix in ("query", "same-name-query", "duplicate-query", "query-before-body", "method", "missing-type", "wrong-type", "duplicate-type"):
                name = label + "-reject-" + suffix
                self.assertFalse(self.results[name]["requestAllowed"], name)
                self.assertEqual(self.results[name]["reads"], 0, name)
            self.assertFalse(self.results[label + "-revision-query"]["requestAllowed"])

    def test_openai_inference_guard_preserves_native_data_and_other_api_contracts(self):
        for label in ("chat", "responses"):
            for suffix in ("native-request", "json-charset", "other-api", "other-operation"):
                name = label + "-" + suffix
                result = self.results[name]
                self.assertTrue(result["requestAllowed"], name)
                self.assertTrue(result["profile"]["allowed"], name)
                self.assertTrue(result["decision"]["allowed"], name)
                self.assertTrue(result["limits"]["allowed"], name)
                if name == "responses-other-operation":
                    self.assertFalse(result["targetAllowed"], name)
                else:
                    self.assertTrue(result["targetAllowed"], name)
                self.assertIn("provider_extension", json.loads(result["bodyAfter"]))

    def test_inference_contract_rejection_is_audited_after_authentication(self):
        root = ET.parse(CORE / "apps/gateway/apim/policies/gateway.xml").getroot()
        inbound = list(root.find("inbound"))
        guard = next(node for node in inbound if node.tag == "set-variable" and node.get("name") == "gatewayInferenceRequestAllowed")
        profile = next(node for node in inbound if node.tag == "set-variable" and node.get("name") == "gatewayProfileSelection")
        self.assertLess(next(i for i, node in enumerate(inbound) if node.tag == "base"), inbound.index(guard))
        self.assertLess(inbound.index(guard), inbound.index(profile))
        rejection = inbound[inbound.index(guard) + 1].find("when")
        self.assertEqual(rejection.find("set-variable[@name='gatewayAuditCode']").get("value"), "INVALID_INFERENCE_REQUEST")
        self.assertEqual(rejection.find("set-variable[@name='gatewayAuditStatus']").get("value"), "@(400)")
        self.assertIsNotNone(rejection.find("include-fragment[@fragment-id='core-gateway-telemetry']"))
        response = rejection.find("return-response")
        self.assertEqual(response.find("set-status").get("code"), "400")
        self.assertIsNotNone(response.find("set-header[@name='apim-request-id']"))
        self.assertIsNone(response.find("set-body"))

    def test_image_operations_enforce_query_media_and_fixed_contract_before_body_reads(self):
        for label in self.image_cases:
            for suffix in ("query", "same-name-query", "base64-query", "api-version", "duplicate-query", "query-before-body",
                           "method", "missing-type", "wrong-type", "duplicate-type", "wrong-route", "wrong-kind", "wrong-format"):
                name = label + "-reject-" + suffix
                self.assertFalse(self.results[name]["requestAllowed"], name)
                self.assertEqual(self.results[name]["reads"], 0, name)

    def test_image_operations_preserve_json_extensions_multipart_and_model_binding(self):
        for label in self.image_cases:
            for suffix in ("native-request", "revision-request", "other-api"):
                name = label + "-" + suffix
                result = self.results[name]
                self.assertTrue(result["requestAllowed"], name)
                for stage in ("profile", "decision", "limits"):
                    self.assertTrue(result[stage]["allowed"], (name, stage))
                self.assertTrue(result["targetAllowed"], name)
                if label.startswith("openai-edit-"):
                    self.assertTrue(result["bodyAfter"].startswith("--synthetic-image-boundary\r\n"), name)
                    self.assertEqual(result["reads"], 1, name)
                else:
                    self.assertIn("provider_extension", json.loads(result["bodyAfter"]), name)

    def test_embedding_operations_reject_invalid_query_media_or_binding_before_body_reads(self):
        for label in self.embedding_cases:
            for suffix in ("query", "input-query", "texts-query", "duplicate-query", "query-before-body", "method",
                           "missing-type", "wrong-type", "duplicate-type", "wrong-route", "wrong-kind", "wrong-format", "wrong-input-field"):
                name = label + "-reject-" + suffix
                self.assertFalse(self.results[name]["requestAllowed"], name)
                self.assertEqual(self.results[name]["reads"], 0, name)
            self.assertFalse(self.results[label + "-revision-query"]["requestAllowed"])
            self.assertEqual(self.results[label + "-revision-query"]["reads"], 0)
        for suffix in ("both-versions", "duplicate-version", "empty-version", "unknown-version", "uppercase-name", "uppercase-value", "version-with-input"):
            name = "openai-embedding-reject-" + suffix
            self.assertFalse(self.results[name]["requestAllowed"], name)
            self.assertEqual(self.results[name]["reads"], 0, name)
        self.assertFalse(self.results["cohere-embedding-reject-api-version"]["requestAllowed"])
        self.assertEqual(self.results["cohere-embedding-reject-api-version"]["reads"], 0)

    def test_embedding_guard_preserves_body_options_versions_and_other_operations(self):
        for label in self.embedding_cases:
            for suffix in ("native-request", "json-charset", "revision-request", "other-api"):
                name = label + "-" + suffix
                result = self.results[name]
                self.assertTrue(result["requestAllowed"], name)
                for stage in ("profile", "decision", "limits"):
                    self.assertTrue(result[stage]["allowed"], (name, stage))
                self.assertTrue(result["targetAllowed"], name)
                self.assertEqual(json.loads(result["bodyAfter"]), self.embedding_bodies[label], name)
        for version in ("v1", "preview"):
            for prefix in ("openai-embedding-version-", "openai-embedding-revision-version-"):
                result = self.results[prefix + version]
                self.assertTrue(result["requestAllowed"])
                self.assertTrue(result["limits"]["allowed"])
                self.assertTrue(result["targetAllowed"])
                self.assertEqual(result["queryAfter"], {"api-version": [version]})
                self.assertEqual(json.loads(result["bodyAfter"]), self.embedding_bodies["openai-embedding"])
        for suffix, native_input in (("scalar", "Synthetic text"), ("tokens", [1, 2]), ("token-batches", [[1, 2], [3]])):
            result = self.results["openai-embedding-input-" + suffix]
            self.assertTrue(result["requestAllowed"])
            self.assertTrue(result["limits"]["allowed"])
            self.assertTrue(result["targetAllowed"])
            self.assertEqual(json.loads(result["bodyAfter"])["input"], native_input)

    def test_rerank_and_http_audio_reject_query_and_invalid_contracts_before_body_reads(self):
        for label in self.native_contract_cases:
            for suffix in ("query", "excluded-field-query", "duplicate-query", "api-version", "query-before-body", "method",
                           "missing-type", "wrong-type", "duplicate-type", "combined-type", "wrong-route",
                           "wrong-kind", "wrong-format", "missing-contract"):
                name = label + "-reject-" + suffix
                self.assertFalse(self.results[name]["requestAllowed"], name)
                self.assertEqual(self.results[name]["reads"], 0, name)
            self.assertFalse(self.results[label + "-revision-query"]["requestAllowed"])
            self.assertEqual(self.results[label + "-revision-query"]["reads"], 0)

    def test_rerank_and_http_audio_preserve_native_bodies_and_future_models(self):
        for label in self.native_contract_cases:
            for suffix in ("native-request", "mixed-media", "revision-request"):
                name = label + "-" + suffix
                result = self.results[name]
                self.assertTrue(result["requestAllowed"], name)
                for stage in ("profile", "decision", "limits"):
                    self.assertTrue(result[stage]["allowed"], (name, stage))
                self.assertTrue(result["targetAllowed"], name)
                if label == "rerank":
                    self.assertEqual(json.loads(result["bodyAfter"]), json.loads(self.native_contract_bodies[label]), name)
                else:
                    self.assertEqual(result["bodyAfter"], self.native_contract_bodies[label], name)
                self.assertEqual(result["queryAfter"], {}, name)
            for suffix in ("other-api", "other-operation"):
                result = self.results[label + "-" + suffix]
                self.assertTrue(result["requestAllowed"])
                self.assertEqual(result["queryAfter"], {"provider-option": ["synthetic"]})
        for label in ("rerank", "transcription"):
            result = self.results[label + "-future-model"]
            self.assertTrue(result["requestAllowed"])
            self.assertTrue(result["limits"]["allowed"])
            self.assertTrue(result["targetAllowed"])

    def test_decision_contract_rejects_query_or_wrong_media_before_reading_body(self):
        for suffix in ("query", "state-query", "question-key-query", "duplicate-query", "api-version", "query-before-body",
                       "method", "missing-type", "wrong-type", "duplicate-type", "combined-type", "wrong-route",
                       "wrong-kind", "wrong-format", "missing-contract"):
            name = "decision-reject-" + suffix
            self.assertFalse(self.results[name]["requestAllowed"], name)
            self.assertEqual(self.results[name]["reads"], 0, name)
        self.assertFalse(self.results["decision-revision-query"]["requestAllowed"])
        self.assertEqual(self.results["decision-revision-query"]["reads"], 0)

    def test_decision_native_data_and_unrelated_contracts_keep_their_existing_behavior(self):
        for suffix in ("native-request", "json-charset", "revision-request"):
            name = "decision-" + suffix
            result = self.results[name]
            self.assertTrue(result["requestAllowed"], name)
            for stage in ("profile", "decision", "limits"):
                self.assertTrue(result[stage]["allowed"], (name, stage))
            self.assertTrue(result["targetAllowed"], name)
            self.assertEqual(json.loads(result["bodyAfter"]), self.decision_body, name)
            self.assertEqual(result["queryAfter"], {}, name)
        for suffix in ("other-api", "other-operation"):
            name = "decision-" + suffix
            self.assertTrue(self.results[name]["requestAllowed"], name)
            self.assertIn("state.message", self.results[name]["queryAfter"], name)
        rejected_model = self.results["decision-wrong-model"]
        self.assertTrue(rejected_model["requestAllowed"])
        self.assertFalse(rejected_model["targetAllowed"])

    def test_profile_and_model_binding_remain_while_native_modalities_reach_provider(self):
        for name in ("unknown", "duplicate", "wrong-model", "responses-image-unknown-model"):
            self.assertFalse(self.results[name]["profile"]["allowed"], name)
        for name in ("image-default", "image-authorized", "audio-default", "file-default"):
            self.assertTrue(self.results[name]["profile"]["allowed"], name)

    def test_tool_schema_keys_and_native_responses_images_do_not_require_special_profile(self):
        for name in ("responses-tool-schema", "responses-default-image"):
            result = self.results[name]
            self.assertTrue(result["profile"]["allowed"], name)
            self.assertEqual(result["profile"]["profileId"], "response")
            self.assertTrue(result["limits"]["allowed"], name)
            self.assertTrue(result["targetAllowed"], name)

    def test_bytes_are_bounded_before_json_and_again_for_selected_profile(self):
        self.assertEqual(self.results["huge-malformed"]["profile"]["status"], 413)
        self.assertEqual(self.results["huge-malformed"]["reads"], 1)
        self.assertTrue(self.results["selected-size"]["profile"]["allowed"])
        self.assertEqual(self.results["selected-size"]["limits"]["status"], 413)

    def test_ws_selection_enforces_the_api_model_and_query_allowlists(self):
        for name in ("ws-default", "ws-mini"):
            self.assertTrue(self.results[name]["decision"]["allowed"], name)
            self.assertTrue(self.results[name]["queryAllowed"], name)
        for name in ("ws-mini-denied", "ws-unknown", "ws-duplicate"):
            self.assertEqual(self.results[name]["model"], "!invalid-model!")
            self.assertFalse(self.results[name]["queryAllowed"])
        self.assertFalse(self.results["ws-unknown-query"]["queryAllowed"])
        for name, result in self.results.items():
            if name.startswith("ws-"):
                self.assertEqual(result["reads"], 0)

    def test_added_chat_and_flux_models_use_their_own_profile_contracts(self):
        for name in ("DeepSeek-V4-Flash", "DeepSeek-V4-Pro", "Mistral-Large-3", "grok-4.7", "router", "flux-pro"):
            self.assertTrue(self.results[name]["profile"]["allowed"], name)
            self.assertTrue(self.results[name]["limits"]["allowed"], name)
        self.assertFalse(self.results["compatible-wrong-token-field"]["limits"]["allowed"])
        self.assertFalse(self.results["flux-wrong-profile"]["profile"]["allowed"])

    def test_ai_requests_use_the_classic_buffer_ceiling_independently_of_waf_inspection(self):
        accepted = self.results["flux-byte-limit-0"]
        self.assertTrue(accepted["profile"]["allowed"])
        self.assertTrue(accepted["limits"]["allowed"])
        self.assertEqual(self.results["flux-byte-limit-1"]["profile"]["status"], 413)
        for name in ("blackforest-images-v1", "openai-inference-v1"):
            definition = json.loads((CORE / "apps/gateway/apim/apis" / (name + ".json")).read_text())
            def inspect(value):
                if isinstance(value, dict):
                    if value.get("maxRequestBytes", 0) > 32768:
                        self.assertEqual(value["maxRequestBytes"], 524288000)
                    for child in value.values(): inspect(child)
                elif isinstance(value, list):
                    for child in value: inspect(child)
            inspect(definition)


if __name__ == "__main__":
    unittest.main()

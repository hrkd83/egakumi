"use client";

import { useFieldIds } from "@/lib/use-field-ids";
import { deactivatePanelRequest } from "@/lib/panel-request-lifecycle";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { Check, Copy, LoaderCircle, Play, Square } from "lucide-react";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getPiiDictionary } from "@/lib/i18n/pii";
import { getPiiMcpDictionary } from "@/lib/i18n/pii-mcp";
import { INTEGRATION_LANGUAGES, type IntegrationLanguage } from "@/lib/model-integration";
import { loadModelKeyChoices, type ModelKeyChoice } from "@/lib/model-key-choices";
import { ModelResponseError } from "@/lib/model-response";
import type { TextGenerationFailure } from "@/lib/model-request";
import { PII_API, PII_SAMPLE, buildPiiRequest, getPiiRequestBytes, type PiiInput, type PiiResult } from "@/lib/pii-contract";
import { getPiiEndpoint, getPiiHeaders, getPiiIntegrationExample, getPiiRequestSchema, getPiiResponseSchema } from "@/lib/pii-integration";
import { readPiiResponse } from "@/lib/pii-response";
import { PII_MCP, getPiiMcpEndpoint, type PiiMcpResult } from "@/lib/pii-mcp-contract";
import { PII_MCP_LANGUAGES, buildPiiMcpCall, getPiiMcpHeaders, getPiiMcpIntegrationExample, getPiiMcpRequestSchema, getPiiMcpResponseSchema } from "@/lib/pii-mcp-integration";
import { readPiiMcpResponse } from "@/lib/pii-mcp-response";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** 入力と試行結果はこのページのメモリーだけに保持します。 */
type PanelState = {
  input: PiiInput; apiKeyId: string; language: IntegrationLanguage | "json";
  tab: "request" | "schema" | "response"; loading: boolean;
  result: (PiiResult & { mcp?: PiiMcpResult["mcp"] }) | null; failure: TextGenerationFailure | null; cancelled: boolean;
};
const languageNames: Record<IntegrationLanguage, string> = { typescript: "TypeScript", python: "Python", php: "PHP", csharp: "C#", curl: "cURL" };

/** 表示中のコードだけをコピーします。秘密のAPIキーは補完しません。 */
function CodeBlock({ code, title, dictionary }: { code: string; title: string; dictionary: Dictionary }) {
  const [copied, setCopied] = useState<"idle" | "complete" | "failed">("idle");
  /** クリップボードへの書き込み結果を表示します。 */
  async function copy() { try { await navigator.clipboard.writeText(code); setCopied("complete"); } catch { setCopied("failed"); } }
  return <div className="overflow-hidden rounded-xl border border-border bg-muted/30"><div className="flex items-center justify-between gap-3 border-b border-border bg-white px-4 py-2"><span className="text-sm font-semibold">{title}</span><Button type="button" variant="ghost" size="sm" onClick={copy}>{copied === "complete" ? <Check className="size-4" /> : <Copy className="size-4" />}{copied === "complete" ? dictionary.modelGateway.copied : dictionary.modelGateway.integration.copyCode}</Button></div><pre className="whitespace-pre-wrap break-words p-5 font-mono text-[14px] leading-7"><code>{code}</code></pre>{copied === "failed" && <p role="status" className="px-4 pb-3 text-sm text-destructive">{dictionary.modelGateway.copyFailed}</p>}</div>;
}

/** PIIマスキングのガイド、固定契約、本人の購読を使った実行結果を提供します。 */
export function PiiRedactionPanel({ dictionary, locale, gatewayOrigin, protocol = "rest" }: { dictionary: Dictionary; locale: Locale; gatewayOrigin: string | null; protocol?: "rest" | "mcp" }) {
  const formId = useFieldIds();
  const [store] = useState(() => createStore<PanelState>(() => ({ input: { ...PII_SAMPLE }, apiKeyId: "", language: "json", tab: "request", loading: false, result: null, failure: null, cancelled: false })));
  const state = useStore(store), text = dictionary.modelGateway, guide = text.integration, isMcp = protocol === "mcp", pii = isMcp ? getPiiMcpDictionary(locale) : getPiiDictionary(locale);
  const apiId = isMcp ? PII_MCP.apiId : PII_API.apiId;
  const languages: readonly IntegrationLanguage[] = isMcp ? PII_MCP_LANGUAGES : INTEGRATION_LANGUAGES;
  const [choices, setChoices] = useState<{ status: "loading" | "ready" | "error"; keys: ModelKeyChoice[] }>({ status: "loading", keys: [] });
  const [refresh, setRefresh] = useState(0), requestRef = useRef<AbortController | null>(null);
  const selectedKey = choices.status === "ready" ? choices.keys.find((entry) => entry.id === state.apiKeyId) : undefined;
  let payload: PiiInput | null = null;
  try { payload = buildPiiRequest(state.input); } catch { /* 無効な入力はコード例と実送信の両方で拒否します。 */ }

  useEffect(() => () => deactivatePanelRequest(requestRef, (pending) => store.setState({ loading: false, ...(pending ? { cancelled: true } : {}) })), [store]);
  useEffect(() => {
    const controller = new AbortController();
    void loadModelKeyChoices(apiId, controller.signal).then((keys) => { if (!controller.signal.aborted) setChoices({ status: "ready", keys }); }).catch(() => { if (!controller.signal.aborted) setChoices({ status: "error", keys: [] }); });
    return () => controller.abort();
  }, [apiId, refresh]);

  /** 編集後の入力は保持し、古い結果は残しません。 */
  function updateInput(patch: Partial<PiiInput>) { store.setState((current) => ({ input: { ...current.input, ...patch }, result: null, failure: null, cancelled: false })); }
  /** 現在の所属・利用可否を確認してキー候補を再読込します。 */
  function refreshKeys() { setChoices({ status: "loading", keys: [] }); setRefresh((value) => value + 1); }
  /** 本文を一度だけ送信し、公開応答に含まれるマスキング済み結果を表示します。 */
  async function execute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (requestRef.current || !payload || !selectedKey) return;
    const controller = new AbortController(); requestRef.current = controller;
    store.setState({ loading: true, result: null, failure: null, cancelled: false, tab: "response" });
    try {
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(100_000)]);
      const response = await fetch(isMcp ? "/api/tools/pii-mcp" : "/api/tools/pii", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload, apiKeyId: selectedKey.id }), signal });
      const result = await (isMcp ? readPiiMcpResponse(response, signal) : readPiiResponse(response, signal));
      if (!controller.signal.aborted) store.setState({ loading: false, result });
    } catch (error) { if (!controller.signal.aborted) store.setState({ loading: false, failure: error instanceof ModelResponseError ? error.failure : { error: { code: "GenerationFailed" } } }); }
    finally { if (requestRef.current === controller) requestRef.current = null; }
  }
  /** 待受けを中止します。送信済みの処理の取消しは保証しません。 */
  function cancel() { requestRef.current?.abort(); requestRef.current = null; store.setState({ loading: false, cancelled: true }); }

  const origin = gatewayOrigin ?? "https://gateway.example.com";
  const code = payload ? state.language === "json" ? JSON.stringify(isMcp ? buildPiiMcpCall(payload) : payload, null, 2) : isMcp ? getPiiMcpIntegrationExample(state.language, payload, origin) : getPiiIntegrationExample(state.language, payload, origin) : pii.invalid;
  const bytes = getPiiRequestBytes(state.input);
  const runtimes = { typescript: guide.typescriptRuntime, python: guide.pythonRuntime, php: guide.phpRuntime, csharp: guide.csharpRuntime, curl: guide.curlRuntime };

  return <ApiDocumentationLayout locale={locale} value={state.tab} onValueChange={(tab) => store.setState({ tab })} busy={state.loading}
    settings={<form onSubmit={execute} className="space-y-5">
      <div className="space-y-2 rounded-lg border border-border p-3"><div className="flex items-center justify-between gap-3"><label htmlFor={formId("pii-key")} className="text-sm font-medium">{guide.apiKey}</label><Button type="button" variant="ghost" size="sm" disabled={state.loading || choices.status === "loading"} onClick={refreshKeys}>{guide.refreshKeys}</Button></div><Select value={selectedKey?.id ?? ""} disabled={state.loading || choices.status !== "ready" || !choices.keys.length} onValueChange={(apiKeyId) => store.setState({ apiKeyId })}><SelectTrigger id={formId("pii-key")} className="w-full bg-white"><SelectValue placeholder={guide.chooseKey} /></SelectTrigger><SelectContent>{choices.keys.map((key) => <SelectItem key={key.id} value={key.id}>{[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}</SelectItem>)}</SelectContent></Select><p className="text-sm leading-6 text-muted-foreground">{guide.apiKeyHelp}</p>{choices.status === "loading" && <p role="status" className="text-sm">{guide.loadingKeys}</p>}{choices.status === "error" && <p role="alert" className="text-sm text-destructive">{text.errors.ApiKeyAccessUnavailable}</p>}{choices.status === "ready" && !choices.keys.length && <p role="status" className="text-sm">{guide.noKeys} <Link href={`/${locale}/api-keys`} className="text-primary underline">{guide.manageKeys}</Link></p>}</div>
      <div className="space-y-2"><label htmlFor={formId("pii-language")} className="text-sm font-medium">{pii.language}</label><Select value={state.input.language} disabled={state.loading} onValueChange={(value) => { if (value === "ja" || value === "en") updateInput({ language: value }); }}><SelectTrigger id={formId("pii-language")} className="w-full bg-white"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ja">{pii.japanese}</SelectItem><SelectItem value="en">{pii.english}</SelectItem></SelectContent></Select></div>
      <div className="space-y-2"><label htmlFor={formId("pii-text")} className="text-sm font-medium">{pii.text}</label><Textarea id={formId("pii-text")} rows={8} required maxLength={PII_API.maxRequestBytes} disabled={state.loading} value={state.input.text} onChange={(event) => updateInput({ text: event.target.value })} className="bg-white" /><p className="text-sm text-muted-foreground">{pii.bytes}: {new Intl.NumberFormat(locale).format(bytes)} / 32,768 bytes</p></div>
      <p className="text-sm leading-7 text-muted-foreground">{pii.limits}</p><p className="text-sm leading-7 text-muted-foreground">{pii.editable}</p>
      {!payload && <p role="alert" className="text-sm text-destructive">{pii.invalid}</p>}
      <div className="flex flex-wrap gap-2">{state.loading && <Button type="button" variant="outline" onClick={cancel}><Square className="size-4" />{text.cancel}</Button>}<Button type="submit" disabled={state.loading || !payload || !selectedKey || !gatewayOrigin}>{state.loading ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}{state.loading ? pii.running : pii.execute}</Button></div>
    </form>}
    request={<div className="space-y-6"><p className="leading-8">{pii.intro}</p><p className="leading-8 text-muted-foreground">{pii.guide}</p><p className="rounded-lg border border-border px-4 py-3"><span className="mr-3 font-bold text-primary">POST</span><code className="break-all text-sm">{isMcp ? getPiiMcpEndpoint(origin) : getPiiEndpoint(origin)}</code></p><p className="text-sm leading-7 text-muted-foreground">{pii.limits}</p>{isMcp && <p className="text-sm leading-7 text-muted-foreground">{locale === "ja" ? "PHP・C#などでも同じプロトコルを使えます。対応SDKやクライアントで、カスタムapi-keyヘッダー・Streamable HTTP・JSON/SSE応答に対応していることを確認してください。OAuth専用クライアントで自動認証できることは意味しません。" : "PHP, C#, and other languages use the same protocol. Choose a client that supports custom api-key headers, Streamable HTTP, and JSON/SSE responses. OAuth-only clients are not automatically compatible."}</p>}<p className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-7 text-amber-950">{pii.caution}</p>{!gatewayOrigin && <p className="text-sm text-amber-700">{guide.notConfigured}</p>}<div className="flex items-center justify-between gap-3"><p className="font-semibold">{state.language === "json" ? guide.json : guide.code}</p><Select value={state.language} onValueChange={(value) => { if (value === "json" || languages.includes(value as IntegrationLanguage)) store.setState({ language: value as IntegrationLanguage | "json" }); }}><SelectTrigger aria-label={guide.language} className="w-48 bg-white"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="json">{guide.json}</SelectItem>{languages.map((language) => <SelectItem key={language} value={language}>{languageNames[language]}</SelectItem>)}</SelectContent></Select></div>{state.language !== "json" && <div className="text-sm leading-6 text-muted-foreground"><p>{guide.keySetup}</p><p>{runtimes[state.language]}</p>{isMcp && <p>{locale === "ja" ? "TypeScript/Pythonは公式MCP SDK v2の例です。cURLはPython 3を併用してJSON/SSEと初期化を確認します。" : "TypeScript/Python use the official MCP SDK v2. The cURL example uses Python 3 to inspect JSON/SSE and initialization."}</p>}</div>}<CodeBlock key={`${state.language}-${code}`} code={code} title={state.language === "json" ? guide.json : languageNames[state.language]} dictionary={dictionary} />{state.language === "json" && <CodeBlock code={JSON.stringify(isMcp ? getPiiMcpHeaders() : getPiiHeaders(), null, 2)} title={guide.headers} dictionary={dictionary} />}<a href={isMcp ? "https://modelcontextprotocol.io/specification/2025-06-18/basic/transports" : "https://learn.microsoft.com/en-us/azure/ai-services/language-service/personally-identifiable-information/overview"} target="_blank" rel="noreferrer noopener" className="text-sm text-primary underline underline-offset-4">{pii.document}</a></div>}
    schema={<div className="space-y-6"><p className="text-sm leading-7 text-muted-foreground">{pii.schema}</p><ApiSchemaReference locale={locale} title={guide.requestSchema} schema={isMcp ? getPiiMcpRequestSchema() : getPiiRequestSchema()} /><ApiSchemaReference locale={locale} title={guide.responseSchema} schema={isMcp ? getPiiMcpResponseSchema() : getPiiResponseSchema()} /></div>}
    response={<div className="space-y-5" aria-busy={state.loading}><p className="text-sm leading-7 text-muted-foreground">{pii.notice}</p>{state.failure && <div role="alert" className="space-y-2 rounded-xl border border-destructive/20 p-4 text-sm text-destructive"><p>{state.failure.error.code === "InvalidRequest" ? pii.invalid : state.failure.error.code === "GenerationFailed" ? pii.failed : text.errors[state.failure.error.code]}</p>{state.failure.retryAfterSeconds !== undefined && <p>{text.retryAfter.replace("{count}", String(state.failure.retryAfterSeconds))}</p>}{state.failure.requestId && <p>{text.requestId}: {state.failure.requestId}</p>}</div>}{state.result ? <><div className="flex flex-wrap gap-4 text-sm text-muted-foreground"><span>{pii.count}: {new Intl.NumberFormat(locale).format(state.result.result.detectedCount)}</span><span>{pii.model}: {state.result.result.modelVersion}</span><span>{(state.result.durationMs / 1000).toFixed(1)} s</span></div><section className="rounded-xl border border-border p-5"><h3 className="mb-3 font-semibold">{pii.redacted}</h3><p className="whitespace-pre-wrap break-words leading-8">{state.result.result.redactedText || "—"}</p></section><CodeBlock code={JSON.stringify(state.result.mcp?.response ?? state.result.result, null, 2)} title={pii.response} dictionary={dictionary} /><p className="break-all font-mono text-sm text-muted-foreground">{text.requestId}: {state.result.result.requestId}</p></> : !state.failure && <div role="status" className="flex min-h-36 items-center justify-center gap-2 rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground">{state.loading && <LoaderCircle className="size-4 animate-spin" />}{state.loading ? guide.waiting : state.cancelled ? text.cancelNotice : pii.empty}</div>}</div>}
  />;
}

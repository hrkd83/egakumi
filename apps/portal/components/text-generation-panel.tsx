"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { Check, Copy, LoaderCircle, Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import { usePortalStore } from "@/components/portal-store-provider";
import { useModelSelection } from "@/components/use-model-selection";
import { getMinimumTextOutputTokens, MAX_TEXT_MESSAGE_CHARACTERS, textGenerationInputSchema } from "@/lib/model-request";
import { ModelResponseError, readModelResponse } from "@/lib/model-response";
import { loadModelKeyChoices, type ModelKeyChoice } from "@/lib/model-key-choices";
import { chooseTextModelVariant, groupTextModels, resolveTextModelVariant, updateTextModelSelection } from "@/lib/text-model-choices";
import {
  SAMPLE_TEXT_PROMPT, INTEGRATION_LANGUAGES, buildTextGenerationRequest,
  getIntegrationExample, getTextGenerationEndpoint, getTextGenerationHeaders,
  getTextGenerationRequestSchema, getTextGenerationResponseSchema,
  type IntegrationLanguage,
} from "@/lib/model-integration";
import type { TextGenerationModel } from "@/lib/model-types";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** 一覧の取得失敗を『利用可能なキーなし』と取り違えないための表示状態です。 */
type KeyChoiceState = { apiId: string; status: "loading" | "ready" | "error"; keys: ModelKeyChoice[] };

/** 読み取り専用のコード例を表示します。コピー対象にAPIキーの実値は含めません。 */
function CodeBlock({ code, title, copyLabel, dictionary }: { code: string; title: string; copyLabel: string; dictionary: Dictionary }) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");
  /** 選択中のコードだけをコピーし、クリップボードへの書込み結果を案内します。 */
  async function copyCode() {
    try { await navigator.clipboard.writeText(code); setCopyStatus("copied"); }
    catch { setCopyStatus("failed"); }
  }
  return <div className="overflow-hidden rounded-xl border border-border bg-[#f8faf9]">
    <div className="flex items-center justify-between gap-3 border-b border-border bg-white px-4 py-2">
      <span className="text-sm font-semibold text-muted-foreground">{title}</span>
      <Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 text-sm" onClick={copyCode}>{copyStatus === "copied" ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copyStatus === "copied" ? dictionary.modelGateway.copied : copyLabel}</Button>
    </div>
    <pre className="whitespace-pre-wrap break-words p-5 font-mono text-[14px] leading-7" aria-label={title}><code>{code}</code></pre>
    {copyStatus === "failed" && <p role="status" className="px-4 pb-3 text-sm text-destructive">{dictionary.modelGateway.copyFailed}</p>}
  </div>;
}

/** 各コード例の表示名です。プログラミング言語名は翻訳せず統一します。 */
const languageNames: Record<IntegrationLanguage, string> = { typescript: "TypeScript", python: "Python", php: "PHP", csharp: "C#", curl: "cURL" };

/** SSEはJSON配列に見せず、受信順のevent/data行として表示します。 */
function formatReceivedBody(raw: Record<string, unknown>, stream: boolean): string {
  if (!stream) return JSON.stringify(raw, null, 2);
  if (!Array.isArray(raw.events)) return "";
  return raw.events.map((entry: { event?: string; data: unknown }) =>
    `${entry.event ? `event: ${entry.event}\n` : ""}data: ${typeof entry.data === "string" ? entry.data : JSON.stringify(entry.data)}\n\n`,
  ).join("");
}

/** 編集したメッセージでAPIを試し、同じ入力の組み込み例と実行結果を提供します。 */
export function TextGenerationPanel({ models, dictionary, locale, gatewayOrigin }: { models: TextGenerationModel[]; dictionary: Dictionary; locale: Locale; gatewayOrigin: string | null }) {
  const text = dictionary.modelGateway;
  const guide = text.integration;
  const { rememberModel } = useModelSelection("text", models);
  const input = usePortalStore((state) => state.textInput);
  const updateInput = usePortalStore((state) => state.updateTextInput);
  const apiKeyId = usePortalStore((state) => state.modelApiKeyId);
  const selectApiKey = usePortalStore((state) => state.setModelApiKeyId);
  const language = usePortalStore((state) => state.integrationLanguage);
  const setLanguage = usePortalStore((state) => state.setIntegrationLanguage);
  const [activeTab, setActiveTab] = useState<"request" | "schema" | "response">("request");
  const status = usePortalStore((state) => state.generationStatus);
  const result = usePortalStore((state) => state.generationResult);
  const failure = usePortalStore((state) => state.generationFailure);
  const begin = usePortalStore((state) => state.beginGeneration);
  const updateResult = usePortalStore((state) => state.updateGeneration);
  const complete = usePortalStore((state) => state.completeGeneration);
  const fail = usePortalStore((state) => state.failGeneration);
  const cancel = usePortalStore((state) => state.cancelGeneration);
  const requestRef = useRef<AbortController | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const [keyChoices, setKeyChoices] = useState<KeyChoiceState>({ apiId: "", status: "loading", keys: [] });
  const [keyRefresh, setKeyRefresh] = useState(0);
  const modelChoices = groupTextModels(models);
  const model = resolveTextModelVariant(models, input);
  const modelChoice = modelChoices.find((item) => item.id === model?.id);
  const apiId = model?.apiId ?? "";
  const choicesReady = keyChoices.apiId === apiId && keyChoices.status === "ready";
  const selectedKey = choicesReady ? keyChoices.keys.find((key) => key.id === apiKeyId) : undefined;
  const loading = status === "loading";
  const parsed = textGenerationInputSchema.safeParse({ ...input, modelId: model?.id, operationId: model?.operationId, ...(model?.maxOutputTokens === null ? { maxOutputTokens: undefined } : {}) });
  const valid = parsed.success && model !== undefined && (model.maxOutputTokens === null || typeof input.maxOutputTokens === "number" && input.maxOutputTokens >= getMinimumTextOutputTokens(model) && input.maxOutputTokens <= model.maxOutputTokens);
  const origin = gatewayOrigin ?? "https://gateway.example.com";
  const number = new Intl.NumberFormat(locale);

  useEffect(() => () => { if (requestRef.current) { requestRef.current.abort(); cancel(); } }, [cancel]);

  useEffect(() => {
    if (!apiId) return;
    const controller = new AbortController();
    void loadModelKeyChoices(apiId, controller.signal).then((keys) => {
      if (!controller.signal.aborted) setKeyChoices({ apiId, status: "ready", keys });
    }).catch(() => {
      if (!controller.signal.aborted) setKeyChoices({ apiId, status: "error", keys: [] });
    });
    return () => controller.abort();
  }, [apiId, keyRefresh]);

  /** 古い一覧で実行させず、現在の所属と利用可否を改めて取得します。 */
  function refreshKeys() {
    setKeyChoices({ apiId, status: "loading", keys: [] });
    setKeyRefresh((value) => value + 1);
  }

  /** モデル・方式の変更で古い結果を消し、実行先が変わる場合はキーの利用可否も選び直します。 */
  function selectModel(next: TextGenerationModel) {
    if (model?.apiId !== next.apiId) selectApiKey("");
    rememberModel({ id: next.id, operationId: next.operationId });
    updateInput(updateTextModelSelection(input, next));
    setCancelled(false);
  }

  /** 表示と同じメッセージ・設定を送り、接続先とキーはサーバーで解決します。 */
  async function execute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (requestRef.current || !model) return;
    if (!parsed.success || !valid) { fail({ error: { code: "InvalidRequest" } }); return; }
    if (!selectedKey) { fail({ error: { code: "ApiKeyUnavailable" } }); return; }
    const controller = new AbortController();
    requestRef.current = controller;
    setCancelled(false);
    setActiveTab("response");
    begin();
    try {
      const response = await fetch("/api/models/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...parsed.data, apiKeyId: selectedKey.id }), signal: controller.signal });
      const received = await readModelResponse(response, model, { onStreamUpdate: (partial) => { if (!controller.signal.aborted) updateResult(partial); } });
      if (!controller.signal.aborted) complete(received);
    } catch (error) {
      if (!controller.signal.aborted) fail(error instanceof ModelResponseError ? error.failure : { error: { code: "GenerationFailed" } });
    } finally { if (requestRef.current === controller) requestRef.current = null; }
  }

  /** 受信を中止します。すでに送信したモデル処理の取り消しは保証しません。 */
  function cancelRequest() { requestRef.current?.abort(); requestRef.current = null; cancel(); setCancelled(true); }

  if (!model) return null;
  const endpoint = getTextGenerationEndpoint(model, origin);
  const payload = valid && parsed.success ? buildTextGenerationRequest(model, parsed.data) : null;
  const requestCode = payload && parsed.success ? (language === "json" ? JSON.stringify(payload, null, 2) : getIntegrationExample(language, model, parsed.data, origin)) : text.errors.InvalidRequest;
  const runtimes = { typescript: guide.typescriptRuntime, python: guide.pythonRuntime, php: guide.phpRuntime, csharp: guide.csharpRuntime, curl: guide.curlRuntime };
  const requestSchema = getTextGenerationRequestSchema(model);
  const responseSchema = getTextGenerationResponseSchema(model, input.stream);
  const contentType = input.stream ? "text/event-stream" : "application/json";

  return <ApiDocumentationLayout locale={locale} value={activeTab} onValueChange={setActiveTab} busy={loading}
    selection={<>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <label htmlFor="integration-model" className="block text-sm font-medium">{text.model}</label>
          <Select value={model.id} disabled={loading} onValueChange={(modelId) => { const choice = modelChoices.find((item) => item.id === modelId); if (choice) selectModel(chooseTextModelVariant(choice, model.operationId)); }}>
            <SelectTrigger id="integration-model" className="h-10 w-full min-w-0 bg-white text-sm"><SelectValue /></SelectTrigger>
            <SelectContent>{modelChoices.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <label htmlFor="integration-api-method" className="block text-sm font-medium">{guide.apiMethod}</label>
          <Select value={model.operationId} disabled={loading || modelChoice?.variants.length === 1} onValueChange={(operationId) => { const next = modelChoice?.variants.find((item) => item.operationId === operationId); if (next) selectModel(next); }}>
            <SelectTrigger id="integration-api-method" aria-describedby="integration-api-method-help" className="h-10 w-full min-w-0 bg-white text-sm"><SelectValue /></SelectTrigger>
            <SelectContent>{modelChoice?.variants.map((item) => <SelectItem key={item.operationId} value={item.operationId}>{item.operationId === "responses-create" ? "Responses" : "Chat Completions"}</SelectItem>)}</SelectContent>
          </Select>
        </div>
      </div>
      <p id="integration-api-method-help" className="text-sm leading-6 text-muted-foreground">{guide.apiMethodHelp}</p>
    </>}
    settings={
      <form onSubmit={execute} className="space-y-5">
      <div className="space-y-2 rounded-lg border border-border bg-white p-3">
        <div className="flex items-center justify-between gap-3">
          <label htmlFor="integration-api-key" className="text-sm font-medium">{guide.apiKey}</label>
          <Button type="button" variant="ghost" size="sm" disabled={loading || keyChoices.status === "loading"} onClick={refreshKeys} className="h-7 text-sm">{guide.refreshKeys}</Button>
        </div>
        <Select value={selectedKey?.id ?? ""} disabled={loading || !choicesReady || !keyChoices.keys.length} onValueChange={selectApiKey}>
          <SelectTrigger id="integration-api-key" aria-describedby="integration-api-key-help" className="h-10 w-full min-w-0 bg-white text-sm"><SelectValue placeholder={guide.chooseKey} className="min-w-0 flex-1 overflow-hidden text-left" /></SelectTrigger>
          <SelectContent>{keyChoices.keys.map((key) => <SelectItem key={key.id} value={key.id}><span className="block min-w-0 truncate text-left" title={[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}><span>{key.displayName}</span><span className="text-sm text-muted-foreground">{" · "}{[key.companyName, key.groupName].filter(Boolean).join(" · ")}</span></span></SelectItem>)}</SelectContent>
        </Select>
        <p id="integration-api-key-help" className="text-sm leading-6 text-muted-foreground">{guide.apiKeyHelp}</p>
        {(keyChoices.apiId !== apiId || keyChoices.status === "loading") && <p role="status" className="text-sm text-muted-foreground">{guide.loadingKeys}</p>}
        {keyChoices.apiId === apiId && keyChoices.status === "error" && <p role="alert" className="text-sm text-destructive">{text.errors.ApiKeyAccessUnavailable}</p>}
        {choicesReady && !keyChoices.keys.length && <p role="status" className="text-sm leading-6 text-muted-foreground">{guide.noKeys} <Link href={`/${locale}/api-keys`} className="font-medium text-primary underline underline-offset-4">{guide.manageKeys}</Link></p>}
      </div>
      <div className="grid items-start gap-4 sm:grid-cols-[minmax(0,1fr)_140px]">
        <div className="space-y-2">
          <label htmlFor="integration-tokens" className="block text-sm font-medium">{text.outputTokens}</label>
          {model.maxOutputTokens === null ? <p className="text-sm leading-6 text-muted-foreground">{model.outputTokenLimitKind === "dynamic" ? text.tokenLimitAutomatic : text.tokenLimitUnverified}</p> : <><Input id="integration-tokens" type="number" required min={getMinimumTextOutputTokens(model)} max={model.maxOutputTokens} step={1} disabled={loading} value={typeof input.maxOutputTokens === "number" && Number.isFinite(input.maxOutputTokens) ? input.maxOutputTokens : ""} onChange={(event) => updateInput({ maxOutputTokens: event.target.valueAsNumber })} className="h-10 bg-white text-sm" /><p className="text-sm text-muted-foreground">{text.tokenLimit.replace("{minimum}", number.format(getMinimumTextOutputTokens(model))).replace("{count}", number.format(model.maxOutputTokens))}</p></>}
        </div>
        <div className="space-y-2">
          <span className="block text-sm font-medium">{guide.stream}</span>
          <button type="button" role="switch" aria-checked={input.stream} aria-label={guide.stream} aria-describedby="integration-stream-help" disabled={loading} onClick={() => updateInput({ stream: !input.stream })} className="flex h-10 items-center gap-2 rounded-lg border border-border bg-white px-3 text-sm disabled:opacity-50">
            <span className={`relative h-5 w-9 rounded-full transition-colors ${input.stream ? "bg-primary" : "bg-slate-300"}`} aria-hidden="true"><span className={`absolute left-0 top-0.5 size-4 rounded-full bg-white shadow-sm transition-transform ${input.stream ? "translate-x-[18px]" : "translate-x-0.5"}`} /></span><span className="font-mono">{String(input.stream)}</span>
          </button>
        </div>
      </div>
      <p id="integration-stream-help" className="text-sm leading-6 text-muted-foreground">{guide.streamHelp}</p>
      <div className="space-y-2"><label htmlFor="integration-message" className="block text-sm font-medium">{text.prompt}</label><Textarea id="integration-message" required rows={4} maxLength={MAX_TEXT_MESSAGE_CHARACTERS} disabled={loading} value={input.message} onChange={(event) => updateInput({ message: event.target.value })} placeholder={SAMPLE_TEXT_PROMPT} className="bg-white text-sm leading-7" /></div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-lg text-sm leading-6 text-muted-foreground">{guide.fixedNotice}</p>
        <div className="flex items-center gap-2">{loading && <Button type="button" variant="outline" size="sm" onClick={cancelRequest}><Square className="size-3" />{text.cancel}</Button>}<Button type="submit" size="sm" disabled={loading || !valid || !selectedKey} className="gap-2">{loading ? <LoaderCircle className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}{loading ? guide.running : guide.run}</Button></div>
      </div>
      {!valid && <p role="alert" className="text-sm text-destructive">{text.errors.InvalidRequest}</p>}
    </form>
    }
    request={<div className="space-y-6">
      <p className="text-[15px] leading-8 text-muted-foreground">{model.operationId === "responses-create" ? guide.responsesGuide : guide.chatCompletionsGuide}</p>
<div className="rounded-lg border border-border px-4 py-3"><span className="mr-3 text-sm font-bold text-primary">POST</span><code className="break-all text-sm">{endpoint}</code></div>
        {!gatewayOrigin && <p className="text-sm text-amber-700">{guide.notConfigured}</p>}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm font-semibold">{language === "json" ? guide.json : guide.code}</p>
          <Select value={language} onValueChange={(value) => { if (value === "json" || INTEGRATION_LANGUAGES.includes(value as IntegrationLanguage)) setLanguage(value as IntegrationLanguage | "json"); }}><SelectTrigger className="w-48 bg-white text-sm" aria-label={guide.language}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="json">{guide.json}</SelectItem>{INTEGRATION_LANGUAGES.map((item) => <SelectItem key={item} value={item}>{languageNames[item]}</SelectItem>)}</SelectContent></Select>
        </div>
        {language !== "json" && <div className="space-y-1 text-sm leading-6 text-muted-foreground"><p>{guide.keySetup}</p><p>{runtimes[language]}</p>{input.stream && <p>{guide.streamCodeNotice}</p>}</div>}
        <CodeBlock key={`${language}-${model.id}-${input.maxOutputTokens}-${input.stream}`} code={requestCode} title={language === "json" ? guide.json : languageNames[language]} copyLabel={language === "json" ? guide.copyJson : guide.copyCode} dictionary={dictionary} />
        {language === "json" && <CodeBlock code={JSON.stringify(getTextGenerationHeaders(model, input.stream), null, 2)} title={guide.headers} copyLabel={guide.copyJson} dictionary={dictionary} />}
        <p className="text-sm leading-6 text-muted-foreground">{guide.previewNote}</p>
    </div>}
    schema={<div className="space-y-6">
      <p className="text-sm leading-6 text-muted-foreground">{guide.schemaNotice}{input.stream && <> {guide.sseSchemaNotice}</>}</p>
        <ApiSchemaReference locale={locale} schema={requestSchema} title={guide.requestSchema} /><ApiSchemaReference key={`${model.id}-${model.operationId}-${input.stream}`} locale={locale} schema={responseSchema} title={`${guide.responseSchema} · ${contentType}`} />
    </div>}
    response={<div className="space-y-6" aria-busy={loading}>
      <p className="text-sm leading-6 text-muted-foreground">{guide.requestNotice}</p>
        {cancelled && result && <p role="status" className="text-sm leading-6 text-muted-foreground">{text.cancelNotice}</p>}
        {failure && <div role="alert" className="space-y-2 rounded-xl border border-destructive/20 bg-destructive/4 p-4 text-sm leading-6 text-destructive"><p>{text.errors[failure.error.code]}</p>{failure.retryAfterSeconds !== undefined && <p>{text.retryAfter.replace("{count}", number.format(failure.retryAfterSeconds))}</p>}{failure.requestId && <p className="break-all text-sm">{text.requestId}: {failure.requestId}</p>}</div>}
        {result ? <>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground"><span className="font-mono">{contentType}</span><span>{result.model}</span><span>{(result.durationMs / 1000).toFixed(1)} s</span>{result.usage && <><span>{text.inputTokens}: {number.format(result.usage.inputTokens)} {text.tokens}</span><span>{text.outputTokenUsage}: {number.format(result.usage.outputTokens)} {text.tokens}</span></>}</div>
          <CodeBlock code={formatReceivedBody(result.rawResponse, input.stream)} title={input.stream ? guide.rawEvents : guide.rawJson} copyLabel={guide.copyResponse} dictionary={dictionary} />
          {loading && <p role="status" className="flex items-center gap-2 text-sm text-primary"><LoaderCircle className="size-3.5 animate-spin" />{guide.running}</p>}
          <details className="rounded-xl border border-border px-4 py-3"><summary className="text-sm font-semibold">{guide.generatedText}</summary><div className="mt-3 whitespace-pre-wrap text-sm leading-7">{result.text}</div></details>
          {result.requestId && <p className="break-all font-mono text-sm text-muted-foreground">{text.requestId}: {result.requestId}</p>}
          {["length", "incomplete"].includes(result.finishReason ?? "") && <p className="text-sm text-amber-700">{text.truncated}</p>}
        </> : !failure && <div className="flex min-h-36 items-center justify-center gap-2 rounded-xl border border-dashed border-border px-5 text-center text-sm leading-6 text-muted-foreground">{loading && <LoaderCircle className="size-4 animate-spin" />}{loading ? guide.waiting : cancelled ? text.cancelNotice : guide.emptyResponse}</div>}
    </div>}
  />;
}

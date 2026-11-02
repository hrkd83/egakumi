"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { Check, Copy, LoaderCircle, Play, Plus, Square, Trash2 } from "lucide-react";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import { usePortalStore } from "@/components/portal-store-provider";
import { useModelSelection } from "@/components/use-model-selection";
import { MAX_EMBEDDING_TEXT_CHARACTERS, embeddingInputSchema, type EmbeddingInput, type EmbeddingResult } from "@/lib/embedding-request";
import { EMBEDDING_SAMPLE_TEXTS, buildEmbeddingRequest, defaultEmbeddingInput, getEmbeddingDefaultDimensions, getEmbeddingDimensionChoices, getEmbeddingEndpoint, getEmbeddingHeaders, getEmbeddingIntegrationExample, getEmbeddingRequestSchema, getEmbeddingResponseSchema } from "@/lib/embedding-integration";
import { readEmbeddingResponse } from "@/lib/embedding-response";
import { getEmbeddingDictionary } from "@/lib/i18n/embedding";
import { INTEGRATION_LANGUAGES, type IntegrationLanguage } from "@/lib/model-integration";
import { ModelResponseError } from "@/lib/model-response";
import { loadModelKeyChoices, type ModelKeyChoice } from "@/lib/model-key-choices";
import type { TextGenerationFailure } from "@/lib/model-request";
import type { EmbeddingModel } from "@/lib/model-types";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** 埋め込みパネルの表示状態です。購読キーの実値は保持しません。 */
type PanelState = {
  input: EmbeddingInput;
  apiKeyId: string;
  language: IntegrationLanguage | "json";
  tab: "request" | "schema" | "response";
  loading: boolean;
  result: EmbeddingResult | null;
  failure: TextGenerationFailure | null;
  cancelled: boolean;
};

/** コードとJSONを読み取り専用で表示し、クリップボードの成否を伝えます。 */
function EmbeddingCode({ code, title, dictionary }: { code: string; title: string; dictionary: Dictionary }) {
  const [copied, setCopied] = useState<"idle" | "complete" | "failed">("idle");
  /** 表示したコードだけをコピーし、秘密値を補完しません。 */
  async function copy() { try { await navigator.clipboard.writeText(code); setCopied("complete"); } catch { setCopied("failed"); } }
  return <div className="overflow-hidden rounded-xl border border-border bg-[#f8faf9]">
    <div className="flex items-center justify-between gap-3 border-b border-border bg-white px-4 py-2"><span className="text-sm font-semibold text-muted-foreground">{title}</span><Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 text-sm" onClick={copy}>{copied === "complete" ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copied === "complete" ? dictionary.modelGateway.copied : dictionary.modelGateway.integration.copyCode}</Button></div>
    <pre className="whitespace-pre-wrap break-words p-5 font-mono text-[14px] leading-7" aria-label={title}><code>{code}</code></pre>
    {copied === "failed" && <p role="status" className="px-4 pb-3 text-sm text-destructive">{dictionary.modelGateway.copyFailed}</p>}
  </div>;
}

const languageNames: Record<IntegrationLanguage, string> = { typescript: "TypeScript", python: "Python", php: "PHP", csharp: "C#", curl: "cURL" };

/** 編集した複数テキストを、本人が選んだ購読キーで比較する埋め込みガイドです。 */
export function EmbeddingPanel({ models, dictionary, locale, gatewayOrigin }: { models: EmbeddingModel[]; dictionary: Dictionary; locale: Locale; gatewayOrigin: string | null }) {
  const { initialModel, rememberModel } = useModelSelection("embeddings", models);
  const draft = usePortalStore((state) => state.modelDrafts.embeddingTexts), updateDrafts = usePortalStore((state) => state.updateModelDrafts);
  const [store] = useState(() => createStore<PanelState>(() => ({ input: { ...(initialModel ? defaultEmbeddingInput(initialModel) : { modelId: "", dimensions: null, inputType: null, texts: [EMBEDDING_SAMPLE_TEXTS[0]] }), ...(draft !== null ? { texts: [...draft] } : {}) }, apiKeyId: "", language: "json", tab: "request", loading: false, result: null, failure: null, cancelled: false })));
  const state = useStore(store);
  const [choices, setChoices] = useState<{ apiId: string; status: "loading" | "ready" | "error"; keys: ModelKeyChoice[] }>({ apiId: "", status: "loading", keys: [] });
  const [refresh, setRefresh] = useState(0);
  const requestRef = useRef<AbortController | null>(null);
  const model = models.find((item) => item.id === state.input.modelId) ?? models[0];
  const apiId = model?.apiId ?? "";
  const selectedKey = choices.apiId === apiId && choices.status === "ready" ? choices.keys.find((item) => item.id === state.apiKeyId) : undefined;
  const text = dictionary.modelGateway;
  const guide = text.integration;
  const embedding = getEmbeddingDictionary(locale);
  const number = new Intl.NumberFormat(locale);
  const parsed = embeddingInputSchema.safeParse(state.input);
  let payload: Record<string, unknown> | null = null;
  try { if (model && parsed.success) payload = buildEmbeddingRequest(model, parsed.data); } catch { /* 不正なオプションは実行せず、同じ検証結果を画面で案内します。 */ }

  useEffect(() => () => requestRef.current?.abort(), []);
  useEffect(() => {
    if (!apiId) return;
    const controller = new AbortController();
    void loadModelKeyChoices(apiId, controller.signal).then((keys) => { if (!controller.signal.aborted) setChoices({ apiId, status: "ready", keys }); }).catch(() => { if (!controller.signal.aborted) setChoices({ apiId, status: "error", keys: [] }); });
    return () => controller.abort();
  }, [apiId, refresh]);

  /** オプションの選択とリクエスト例を同じ状態から更新します。 */
  function updateInput(patch: Partial<EmbeddingInput>) { if (patch.texts !== undefined) updateDrafts({ embeddingTexts: patch.texts }); store.setState((current) => ({ input: { ...current.input, ...patch }, result: null, failure: null, cancelled: false })); }

  /** キー候補を取得し直し、古いグループ所属や削除されたキーで実行しないようにします。 */
  function refreshKeys() { setChoices({ apiId, status: "loading", keys: [] }); setRefresh((value) => value + 1); }

  /** 編集内容と設定を送り、キーの実値はBFFで解決します。 */
  async function execute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (requestRef.current || !model || !parsed.success || !payload || !selectedKey) return;
    const controller = new AbortController();
    requestRef.current = controller;
    store.setState({ loading: true, result: null, failure: null, cancelled: false, tab: "response" });
    try {
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(100_000)]);
      const response = await fetch("/api/models/embeddings", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...parsed.data, apiKeyId: selectedKey.id }), signal });
      const result = await readEmbeddingResponse(response, signal);
      if (!controller.signal.aborted) store.setState({ loading: false, result });
    } catch (error) { if (!controller.signal.aborted) store.setState({ loading: false, failure: error instanceof ModelResponseError ? error.failure : { error: { code: "GenerationFailed" } } }); }
    finally { if (requestRef.current === controller) requestRef.current = null; }
  }

  /** 受信を中止します。送信済みのモデル処理や課金の取り消しは保証しません。 */
  function cancel() { requestRef.current?.abort(); requestRef.current = null; store.setState({ loading: false, cancelled: true }); }

  if (!model) return null;
  const origin = gatewayOrigin ?? "https://gateway.example.com";
  const code = payload ? state.language === "json" ? JSON.stringify(payload, null, 2) : getEmbeddingIntegrationExample(state.language, model, state.input, origin) : text.errors.InvalidRequest;
  const runtimes = { typescript: guide.typescriptRuntime, python: guide.pythonRuntime, php: guide.phpRuntime, csharp: guide.csharpRuntime, curl: guide.curlRuntime };

  return <ApiDocumentationLayout locale={locale} value={state.tab} onValueChange={(tab) => store.setState({ tab })} busy={state.loading}
    selection={<>
      <div className="max-w-xl"><div className="space-y-2"><label htmlFor="embedding-model" className="block text-sm font-medium">{text.model}</label><Select value={model.id} disabled={state.loading} onValueChange={(value) => { const selected = models.find((item) => item.id === value); if (selected) { rememberModel({ id: selected.id }); store.setState({ input: { ...defaultEmbeddingInput(selected), texts: state.input.texts }, result: null, failure: null }); } }}><SelectTrigger id="embedding-model" className="h-10 w-full bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent>{models.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent></Select></div></div>
    </>}
    settings={
      <form onSubmit={execute} className="space-y-5">
      <div className="space-y-2 rounded-lg border border-border bg-white p-3">
        <div className="flex items-center justify-between gap-3"><label htmlFor="embedding-api-key" className="text-sm font-medium">{guide.apiKey}</label><Button type="button" variant="ghost" size="sm" disabled={state.loading || choices.status === "loading"} onClick={refreshKeys} className="h-7 text-sm">{guide.refreshKeys}</Button></div>
        <Select value={selectedKey?.id ?? ""} disabled={state.loading || choices.apiId !== apiId || choices.status !== "ready" || !choices.keys.length} onValueChange={(apiKeyId) => store.setState({ apiKeyId })}><SelectTrigger id="embedding-api-key" className="h-10 w-full min-w-0 bg-white text-sm"><SelectValue placeholder={guide.chooseKey} /></SelectTrigger><SelectContent>{choices.keys.map((key) => <SelectItem key={key.id} value={key.id}><span className="block truncate">{[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}</span></SelectItem>)}</SelectContent></Select>
        <p className="text-sm leading-6 text-muted-foreground">{guide.apiKeyHelp}</p>
        {(choices.apiId !== apiId || choices.status === "loading") && <p role="status" className="text-sm text-muted-foreground">{guide.loadingKeys}</p>}
        {choices.apiId === apiId && choices.status === "error" && <p role="alert" className="text-sm text-destructive">{text.errors.ApiKeyAccessUnavailable}</p>}
        {choices.apiId === apiId && choices.status === "ready" && !choices.keys.length && <p role="status" className="text-sm leading-6 text-muted-foreground">{guide.noKeys} <Link href={`/${locale}/api-keys`} className="text-primary underline underline-offset-4">{guide.manageKeys}</Link></p>}
      </div>
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <div className="space-y-2"><label htmlFor="embedding-dimensions" className="block text-sm font-medium">{embedding.dimensions}</label><Select value={state.input.dimensions === null ? "default" : String(state.input.dimensions)} disabled={state.loading} onValueChange={(value) => updateInput({ dimensions: value === "default" ? null : Number(value) })}><SelectTrigger id="embedding-dimensions" aria-describedby="embedding-dimensions-help" className="h-10 w-full bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="default">{embedding.defaultDimensions} ({number.format(getEmbeddingDefaultDimensions(model))})</SelectItem>{getEmbeddingDimensionChoices(model).map((value) => <SelectItem key={value} value={String(value)}>{number.format(value)}</SelectItem>)}</SelectContent></Select></div>
        {model.providerId === "cohere" && <div className="space-y-2"><label htmlFor="embedding-input-type" className="block text-sm font-medium">{embedding.inputType}</label><Select value={state.input.inputType ?? ""} disabled={state.loading} onValueChange={(inputType) => updateInput({ inputType })}><SelectTrigger id="embedding-input-type" className="h-10 w-full bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent>{model.inputTypes.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></div>}
      </div>
      <p id="embedding-dimensions-help" className="text-sm leading-6 text-muted-foreground">{embedding.dimensionsHelp}</p>
      {model.providerId === "cohere" && <p className="text-sm leading-6 text-muted-foreground">{embedding.inputTypeHelp}</p>}
      <div className="space-y-3">{state.input.texts.map((value, index) => <div key={index} className="space-y-2"><div className="flex items-center justify-between gap-3"><label htmlFor={`embedding-text-${index}`} className="text-sm font-medium">{embedding.text} {index + 1}</label><Button type="button" variant="ghost" size="sm" disabled={state.loading || state.input.texts.length <= 1} aria-label={`${embedding.removeText} ${index + 1}`} onClick={() => updateInput({ texts: state.input.texts.filter((_, entry) => entry !== index) })}><Trash2 className="size-3.5" />{embedding.removeText}</Button></div><Textarea id={`embedding-text-${index}`} rows={3} maxLength={MAX_EMBEDDING_TEXT_CHARACTERS} required disabled={state.loading} value={value} placeholder={EMBEDDING_SAMPLE_TEXTS[index % EMBEDDING_SAMPLE_TEXTS.length]} onChange={(event) => updateInput({ texts: state.input.texts.map((text, entry) => entry === index ? event.target.value : text) })} className="bg-white text-sm leading-7" /></div>)}<div className="flex items-center justify-between gap-3"><p className="text-sm text-muted-foreground">{embedding.textsHelp.replace("{count}", number.format(model.maxInputs))}</p><Button type="button" variant="outline" size="sm" disabled={state.loading || state.input.texts.length >= model.maxInputs} onClick={() => updateInput({ texts: [...state.input.texts, ""] })}><Plus className="size-3.5" />{embedding.addText}</Button></div></div>
      <div className="flex flex-wrap items-center justify-between gap-3"><p className="max-w-lg text-sm leading-6 text-muted-foreground">{embedding.sampleNotice}</p><div className="flex items-center gap-2">{state.loading && <Button type="button" variant="outline" size="sm" onClick={cancel}><Square className="size-3.5" />{text.cancel}</Button>}<Button type="submit" size="sm" disabled={state.loading || !payload || !selectedKey || !gatewayOrigin}>{state.loading ? <LoaderCircle className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}{state.loading ? embedding.running : embedding.execute}</Button></div></div>
      {!payload && <p role="alert" className="text-sm text-destructive">{text.errors.InvalidRequest}</p>}
    </form>
    }
    request={<div className="space-y-6">
      <p className="text-[15px] leading-8 text-muted-foreground">{embedding.introduction}</p><p className="text-[15px] leading-8 text-muted-foreground">{embedding.integrationGuide}</p>
<div className="rounded-lg border border-border px-4 py-3"><span className="mr-3 text-sm font-bold text-primary">POST</span><code className="break-all text-sm">{getEmbeddingEndpoint(model, origin)}</code></div>
        {!gatewayOrigin && <p className="text-sm text-amber-700">{guide.notConfigured}</p>}
        <div className="flex items-center justify-between gap-3"><p className="text-sm font-semibold">{state.language === "json" ? guide.json : guide.code}</p><Select value={state.language} onValueChange={(language) => { if (language === "json" || INTEGRATION_LANGUAGES.includes(language as IntegrationLanguage)) store.setState({ language: language as IntegrationLanguage | "json" }); }}><SelectTrigger aria-label={guide.language} className="w-48 bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="json">{guide.json}</SelectItem>{INTEGRATION_LANGUAGES.map((language) => <SelectItem key={language} value={language}>{languageNames[language]}</SelectItem>)}</SelectContent></Select></div>
        {state.language !== "json" && <div className="text-sm leading-6 text-muted-foreground"><p>{guide.keySetup}</p><p>{runtimes[state.language]}</p></div>}
        <EmbeddingCode key={`${state.language}-${code}`} code={code} title={state.language === "json" ? guide.json : languageNames[state.language]} dictionary={dictionary} />
        {state.language === "json" && <EmbeddingCode code={JSON.stringify(getEmbeddingHeaders(), null, 2)} title={guide.headers} dictionary={dictionary} />}
        <p className="text-sm leading-6 text-muted-foreground">{guide.previewNote}</p>
<div className="flex flex-wrap gap-3 text-sm text-muted-foreground"><span>{embedding.sources}:</span><a href={model.providerId === "cohere" ? "https://docs.cohere.com/v2/reference/embed" : "https://platform.openai.com/docs/guides/embeddings"} target="_blank" rel="noreferrer noopener" className="text-primary underline underline-offset-4">{model.providerId === "cohere" ? embedding.cohereDocs : embedding.openaiDocs}</a></div>
    </div>}
    schema={<div className="space-y-6">
      <p className="text-sm leading-6 text-muted-foreground">{embedding.schemaNotice}</p><ApiSchemaReference locale={locale} schema={getEmbeddingRequestSchema(model)} title={guide.requestSchema} /><ApiSchemaReference locale={locale} schema={getEmbeddingResponseSchema(model)} title={guide.responseSchema} />
    </div>}
    response={<div className="space-y-6" aria-busy={state.loading}>
      <p className="text-sm leading-6 text-muted-foreground">{guide.requestNotice}</p>
        {state.failure && <div role="alert" className="space-y-2 rounded-xl border border-destructive/20 bg-destructive/4 p-4 text-sm leading-6 text-destructive"><p>{text.errors[state.failure.error.code]}</p>{state.failure.retryAfterSeconds !== undefined && <p>{text.retryAfter.replace("{count}", number.format(state.failure.retryAfterSeconds))}</p>}{state.failure.requestId && <p className="break-all">{text.requestId}: {state.failure.requestId}</p>}</div>}
        {state.result ? <><div className="flex flex-wrap gap-x-4 gap-y-2 text-sm text-muted-foreground"><span>{state.result.model}</span><span>{embedding.vectorCount}: {number.format(state.result.vectorCount)}</span><span>{embedding.dimensions}: {number.format(state.result.dimensions)}</span><span>{(state.result.durationMs / 1_000).toFixed(1)} s</span>{state.result.usage && <><span>{embedding.inputTokens}: {number.format(state.result.usage.inputTokens)}</span>{state.result.usage.totalTokens !== undefined && <span>{embedding.totalTokens}: {number.format(state.result.usage.totalTokens)}</span>}</>}</div><EmbeddingCode code={JSON.stringify(state.result.rawResponse, null, 2)} title={guide.rawJson} dictionary={dictionary} /><details className="rounded-xl border border-border px-4 py-3"><summary className="text-sm font-semibold">{embedding.vectorPreview}</summary><p className="my-3 text-sm leading-6 text-muted-foreground">{embedding.vectorNotice}</p><pre className="whitespace-pre-wrap break-words font-mono text-[14px] leading-7">{JSON.stringify(state.result.vectors.map((vector, index) => ({ index, dimensions: vector.length, values: vector.slice(0, 8) })), null, 2)}</pre></details>{!state.result.usage && <p className="text-sm text-muted-foreground">{embedding.noUsage}</p>}{state.result.requestId && <p className="break-all font-mono text-sm text-muted-foreground">{text.requestId}: {state.result.requestId}</p>}</> : !state.failure && <div role="status" className="flex min-h-36 items-center justify-center gap-2 rounded-xl border border-dashed border-border px-5 text-center text-sm leading-6 text-muted-foreground">{state.loading && <LoaderCircle className="size-4 animate-spin" />}{state.loading ? guide.waiting : state.cancelled ? text.cancelNotice : guide.emptyResponse}</div>}
    </div>}
  />;
}

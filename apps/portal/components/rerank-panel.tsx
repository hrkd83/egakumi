"use client";

import { useFieldIds } from "@/lib/use-field-ids";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { Check, Copy, LoaderCircle, Play, Plus, Square, Trash2 } from "lucide-react";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { usePortalStore } from "@/components/portal-store-provider";
import { useModelSelection } from "@/components/use-model-selection";
import { restoreModelSettings } from "@/lib/model-settings";
import { deactivatePanelRequest } from "@/lib/panel-request-lifecycle";
import { getRerankDictionary } from "@/lib/i18n/rerank";
import { INTEGRATION_LANGUAGES, type IntegrationLanguage } from "@/lib/model-integration";
import { loadModelKeyChoices, type ModelKeyChoice } from "@/lib/model-key-choices";
import { ModelResponseError } from "@/lib/model-response";
import type { TextGenerationFailure } from "@/lib/model-request";
import { buildRerankRequest, defaultRerankInput, getRerankEndpoint, getRerankHeaders, getRerankIntegrationExample, getRerankRequestSchema, getRerankResponseSchema } from "@/lib/rerank-integration";
import { MAX_RERANK_TEXT_CHARACTERS, rerankInputSchema, type RerankInput, type RerankResult } from "@/lib/rerank-request";
import { readRerankResponse } from "@/lib/rerank-response";
import type { RerankModel } from "@/lib/rerank-types";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** 試行中の入力と表示状態です。秘密の購読キーは保存しません。 */
type PanelState = {
  input: RerankInput; apiKeyId: string; language: IntegrationLanguage | "json";
  tab: "request" | "schema" | "response"; loading: boolean;
  result: RerankResult | null; failure: TextGenerationFailure | null; cancelled: boolean;
};
const languageNames: Record<IntegrationLanguage, string> = { typescript: "TypeScript", python: "Python", php: "PHP", csharp: "C#", curl: "cURL" };

/** コードを省略せず表示し、コピー操作の成否を伝えます。 */
function CodeBlock({ code, title, dictionary }: { code: string; title: string; dictionary: Dictionary }) {
  const [copied, setCopied] = useState<"idle" | "complete" | "failed">("idle");
  /** 画面で見えるコードだけをコピーし、秘密値を補完しません。 */
  async function copy() { try { await navigator.clipboard.writeText(code); setCopied("complete"); } catch { setCopied("failed"); } }
  return <div className="overflow-hidden rounded-xl border border-border bg-muted/30"><div className="flex items-center justify-between gap-3 border-b border-border bg-white px-4 py-2"><span className="text-sm font-semibold">{title}</span><Button type="button" variant="ghost" size="sm" onClick={copy}>{copied === "complete" ? <Check className="size-4" /> : <Copy className="size-4" />}{copied === "complete" ? dictionary.modelGateway.copied : dictionary.modelGateway.integration.copyCode}</Button></div><pre className="whitespace-pre-wrap break-words p-5 font-mono text-[14px] leading-7"><code>{code}</code></pre>{copied === "failed" && <p role="status" className="px-4 pb-3 text-sm text-destructive">{dictionary.modelGateway.copyFailed}</p>}</div>;
}

/** リランクの組み込みガイド、API仕様、本人の購読キーによる試用を提供します。 */
export function RerankPanel({ models, dictionary, locale, gatewayOrigin }: { models: RerankModel[]; dictionary: Dictionary; locale: Locale; gatewayOrigin: string | null }) {
  const fieldId = useFieldIds();
  const { scope, initialModel, rememberModel } = useModelSelection("rerank", models);
  const savedSettings = usePortalStore((state) => state.modelSettings.rerank[scope]), rememberSettings = usePortalStore((state) => state.setModelSettings);
  const [store] = useState(() => createStore<PanelState>(() => ({ input: restoreModelSettings("rerank", initialModel ? defaultRerankInput(initialModel) : { modelId: "", query: "", documents: [""], topN: 1 }, savedSettings, (input) => { if (initialModel) buildRerankRequest(initialModel, input); }), apiKeyId: "", language: "json", tab: "request", loading: false, result: null, failure: null, cancelled: false })));
  const state = useStore(store), model = models.find((entry) => entry.id === state.input.modelId) ?? models[0];
  const text = dictionary.modelGateway, guide = text.integration, rerank = getRerankDictionary(locale), number = new Intl.NumberFormat(locale);
  const [choices, setChoices] = useState<{ status: "loading" | "ready" | "error"; keys: ModelKeyChoice[] }>({ status: "loading", keys: [] });
  const [refresh, setRefresh] = useState(0), requestRef = useRef<AbortController | null>(null);
  const apiId = model?.apiId ?? "", selectedKey = choices.status === "ready" ? choices.keys.find((entry) => entry.id === state.apiKeyId) : undefined;
  const parsed = rerankInputSchema.safeParse(state.input);
  let payload: Record<string, unknown> | null = null;
  try { if (model && parsed.success) { const value = buildRerankRequest(model, parsed.data); if (new TextEncoder().encode(JSON.stringify(value)).length <= model.maxRequestBytes) payload = value; } } catch { /* 同じ検証結果で、コード例と実行ボタンを無効にします。 */ }

  useEffect(() => () => deactivatePanelRequest(requestRef, (pending) => store.setState({ loading: false, ...(pending ? { cancelled: true } : {}) })), [store]);
  useEffect(() => {
    if (!apiId) return;
    const controller = new AbortController();
    void loadModelKeyChoices(apiId, controller.signal).then((keys) => { if (!controller.signal.aborted) setChoices({ status: "ready", keys }); }).catch(() => { if (!controller.signal.aborted) setChoices({ status: "error", keys: [] }); });
    return () => controller.abort();
  }, [apiId, refresh]);

  /** 文書を減らした場合だけ返却件数も候補数以下へ調整し、編集中の内容を保ちます。 */
  function updateInput(patch: Partial<RerankInput>) {
    const input = { ...store.getState().input, ...patch }, selected = models.find((entry) => entry.id === input.modelId);
    if (selected) input.topN = Math.min(input.topN, selected.maxTopN, input.documents.length);
    rememberSettings("rerank", scope, input); store.setState({ input, result: null, failure: null, cancelled: false });
  }
  /** 現在の所属で参照できるキー候補を読み直します。 */
  function refreshKeys() { setChoices({ status: "loading", keys: [] }); setRefresh((value) => value + 1); }
  /** 入力を一度だけBFFへ送り、元文書との対応を検証した結果を表示します。 */
  async function execute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (requestRef.current || !payload || !parsed.success || !selectedKey || !model) return;
    const controller = new AbortController(); requestRef.current = controller;
    store.setState({ loading: true, result: null, failure: null, cancelled: false, tab: "response" });
    try {
      const signal = controller.signal;
      const response = await fetch("/api/models/rerank", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...parsed.data, apiKeyId: selectedKey.id }), signal });
      const result = await readRerankResponse(response, signal);
      if (!controller.signal.aborted) store.setState({ loading: false, result });
    } catch (error) { if (!controller.signal.aborted) store.setState({ loading: false, failure: error instanceof ModelResponseError ? error.failure : { error: { code: "GenerationFailed" } } }); }
    finally { if (requestRef.current === controller) requestRef.current = null; }
  }
  /** 受信を中止します。送信済みのAI処理や課金の取消しは保証しません。 */
  function cancel() { requestRef.current?.abort(); requestRef.current = null; store.setState({ loading: false, cancelled: true }); }

  if (!model) return null;
  const origin = gatewayOrigin ?? "https://gateway.example.com";
  const code = payload ? state.language === "json" ? JSON.stringify(payload, null, 2) : getRerankIntegrationExample(state.language, model, state.input, origin) : rerank.invalidInput;
  const limitLabel = rerank.limits.replace("{documents}", number.format(model.maxDocuments)).replace("{top}", number.format(model.maxTopN)).replace("{bytes}", number.format(model.maxRequestBytes));
  const runtimes = { typescript: guide.typescriptRuntime, python: guide.pythonRuntime, php: guide.phpRuntime, csharp: guide.csharpRuntime, curl: guide.curlRuntime };

  return <ApiDocumentationLayout locale={locale} value={state.tab} onValueChange={(tab) => store.setState({ tab })} busy={state.loading}
    selection={<div className="max-w-xl space-y-2"><label htmlFor={fieldId("rerank-model")} className="block text-sm font-medium">{text.model}</label><Select value={model.id} disabled={state.loading} onValueChange={(value) => { const selected = models.find((entry) => entry.id === value); if (selected) { rememberModel({ id: selected.id }); updateInput({ modelId: selected.id, topN: Math.min(selected.maxTopN, state.input.documents.length) }); } }}><SelectTrigger id={fieldId("rerank-model")} className="w-full bg-white"><SelectValue /></SelectTrigger><SelectContent>{models.map((entry) => <SelectItem key={entry.id} value={entry.id}>{entry.label}</SelectItem>)}</SelectContent></Select></div>}
    settings={<form onSubmit={execute} className="space-y-5">
      <div className="space-y-2 rounded-lg border border-border p-3"><div className="flex items-center justify-between gap-3"><label htmlFor={fieldId("rerank-key")} className="text-sm font-medium">{guide.apiKey}</label><Button type="button" variant="ghost" size="sm" disabled={state.loading || choices.status === "loading"} onClick={refreshKeys}>{guide.refreshKeys}</Button></div><Select value={selectedKey?.id ?? ""} disabled={state.loading || choices.status !== "ready" || !choices.keys.length} onValueChange={(apiKeyId) => store.setState({ apiKeyId })}><SelectTrigger id={fieldId("rerank-key")} className="w-full bg-white"><SelectValue placeholder={guide.chooseKey} /></SelectTrigger><SelectContent>{choices.keys.map((key) => <SelectItem key={key.id} value={key.id}>{[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}</SelectItem>)}</SelectContent></Select><p className="text-sm leading-6 text-muted-foreground">{guide.apiKeyHelp}</p>{choices.status === "loading" && <p role="status" className="text-sm">{guide.loadingKeys}</p>}{choices.status === "error" && <p role="alert" className="text-sm text-destructive">{text.errors.ApiKeyAccessUnavailable}</p>}{choices.status === "ready" && !choices.keys.length && <p role="status" className="text-sm">{guide.noKeys} <Link href={`/${locale}/api-keys`} className="text-primary underline">{guide.manageKeys}</Link></p>}</div>
      <div className="space-y-2"><label htmlFor={fieldId("rerank-query")} className="text-sm font-medium">{rerank.query}</label><Textarea id={fieldId("rerank-query")} rows={3} required maxLength={MAX_RERANK_TEXT_CHARACTERS} disabled={state.loading} value={state.input.query} onChange={(event) => updateInput({ query: event.target.value })} className="bg-white" /></div>
      <div className="space-y-4">{state.input.documents.map((document, index) => <div key={index} className="space-y-2"><div className="flex items-center justify-between gap-2"><label htmlFor={fieldId(`rerank-document-${index}`)} className="text-sm font-medium">{rerank.document} {index + 1}</label><Button type="button" size="sm" variant="ghost" disabled={state.loading || state.input.documents.length <= 1} aria-label={`${rerank.removeDocument} ${index + 1}`} onClick={() => updateInput({ documents: state.input.documents.filter((_, position) => position !== index) })}><Trash2 className="size-4" />{rerank.removeDocument}</Button></div><Textarea id={fieldId(`rerank-document-${index}`)} rows={3} required maxLength={MAX_RERANK_TEXT_CHARACTERS} disabled={state.loading} value={document} onChange={(event) => updateInput({ documents: state.input.documents.map((value, position) => position === index ? event.target.value : value) })} className="bg-white" /></div>)}<Button type="button" variant="outline" size="sm" disabled={state.loading || state.input.documents.length >= model.maxDocuments} onClick={() => updateInput({ documents: [...state.input.documents, ""] })}><Plus className="size-4" />{rerank.addDocument}</Button></div>
      <div className="space-y-2"><label htmlFor={fieldId("rerank-top-n")} className="block text-sm font-medium">{rerank.topN}</label><Select value={String(state.input.topN)} disabled={state.loading} onValueChange={(value) => updateInput({ topN: Number(value) })}><SelectTrigger id={fieldId("rerank-top-n")} className="w-full bg-white"><SelectValue /></SelectTrigger><SelectContent>{Array.from({ length: Math.min(model.maxTopN, state.input.documents.length) - model.minTopN + 1 }, (_, index) => index + model.minTopN).map((value) => <SelectItem key={value} value={String(value)}>{value}</SelectItem>)}</SelectContent></Select></div>
      <p className="text-sm leading-6 text-muted-foreground">{limitLabel}</p><p className="text-sm leading-6 text-muted-foreground">{rerank.editable}</p>
      {!payload && <p role="alert" className="text-sm text-destructive">{rerank.invalidInput}</p>}
      <div className="flex flex-wrap gap-2">{state.loading && <Button type="button" variant="outline" onClick={cancel}><Square className="size-4" />{text.cancel}</Button>}<Button type="submit" disabled={state.loading || !payload || !selectedKey || !gatewayOrigin}>{state.loading ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}{state.loading ? rerank.running : rerank.execute}</Button></div>
    </form>}
    request={<div className="space-y-6"><p className="leading-8">{rerank.introduction}</p><p className="leading-8 text-muted-foreground">{rerank.guide}</p><p className="rounded-lg border border-border px-4 py-3"><span className="mr-3 font-bold text-primary">POST</span><code className="break-all text-sm">{getRerankEndpoint(model, origin)}</code></p><p className="text-sm text-muted-foreground">{limitLabel}</p>{!gatewayOrigin && <p className="text-sm text-amber-700">{guide.notConfigured}</p>}<div className="flex items-center justify-between gap-3"><p className="font-semibold">{state.language === "json" ? guide.json : guide.code}</p><Select value={state.language} onValueChange={(value) => { if (value === "json" || INTEGRATION_LANGUAGES.includes(value as IntegrationLanguage)) store.setState({ language: value as IntegrationLanguage | "json" }); }}><SelectTrigger aria-label={guide.language} className="w-48 bg-white"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="json">{guide.json}</SelectItem>{INTEGRATION_LANGUAGES.map((language) => <SelectItem key={language} value={language}>{languageNames[language]}</SelectItem>)}</SelectContent></Select></div>{state.language !== "json" && <div className="text-sm leading-6 text-muted-foreground"><p>{guide.keySetup}</p><p>{runtimes[state.language]}</p></div>}<CodeBlock key={`${state.language}-${code}`} code={code} title={state.language === "json" ? guide.json : languageNames[state.language]} dictionary={dictionary} />{state.language === "json" && <CodeBlock code={JSON.stringify(getRerankHeaders(), null, 2)} title={guide.headers} dictionary={dictionary} />}<p className="text-sm text-muted-foreground">{guide.previewNote}</p><a href="https://docs.cohere.com/v2/reference/rerank" target="_blank" rel="noreferrer noopener" className="text-sm text-primary underline underline-offset-4">{rerank.providerDocs}</a></div>}
    schema={<div className="space-y-6"><p className="text-sm leading-7 text-muted-foreground">{rerank.schemaNotice}</p><ApiSchemaReference locale={locale} title={guide.requestSchema} schema={getRerankRequestSchema(model)} /><ApiSchemaReference locale={locale} title={guide.responseSchema} schema={getRerankResponseSchema()} /></div>}
    response={<div className="space-y-5" aria-busy={state.loading}><p className="text-sm leading-6 text-muted-foreground">{rerank.requestNotice}</p>{state.failure && <div role="alert" className="space-y-2 rounded-xl border border-destructive/20 p-4 text-sm text-destructive"><p>{state.failure.error.code === "InvalidRequest" ? rerank.invalidInput : text.errors[state.failure.error.code]}</p>{state.failure.retryAfterSeconds !== undefined && <p>{text.retryAfter.replace("{count}", String(state.failure.retryAfterSeconds))}</p>}{state.failure.requestId && <p>{text.requestId}: {state.failure.requestId}</p>}</div>}{state.result ? <><div className="flex flex-wrap gap-4 text-sm text-muted-foreground"><span>{state.result.model}</span><span>{(state.result.durationMs / 1_000).toFixed(1)} s</span>{state.result.searchUnits !== undefined && <span>{rerank.searchUnits}: {number.format(state.result.searchUnits)}</span>}</div><p className="text-sm leading-6 text-muted-foreground">{rerank.resultHelp}</p><ol className="space-y-3">{state.result.ranking.map((entry, rank) => <li key={entry.index} className="rounded-xl border border-border p-4"><div className="mb-3 flex flex-wrap gap-3 text-sm font-semibold"><span>{rerank.rank}: {rank + 1}</span><span>{rerank.original}: {entry.index + 1}</span><span>{rerank.score}: {new Intl.NumberFormat(locale, { maximumFractionDigits: 6 }).format(entry.relevanceScore)}</span></div><p className="whitespace-pre-wrap break-words leading-7">{entry.document}</p></li>)}</ol><CodeBlock code={JSON.stringify(state.result.rawResponse, null, 2)} title={guide.rawJson} dictionary={dictionary} />{state.result.searchUnits === undefined && <p className="text-sm text-muted-foreground">{rerank.noUsage}</p>}{state.result.requestId && <p className="break-all font-mono text-sm text-muted-foreground">{text.requestId}: {state.result.requestId}</p>}</> : !state.failure && <div role="status" className="flex min-h-36 items-center justify-center gap-2 rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground">{state.loading && <LoaderCircle className="size-4 animate-spin" />}{state.loading ? guide.waiting : state.cancelled ? text.cancelNotice : rerank.emptyResponse}</div>}</div>}
  />;
}

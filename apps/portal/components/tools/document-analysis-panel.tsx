"use client";

import { useFieldIds } from "@/lib/use-field-ids";
import { deactivatePanelRequest } from "@/lib/panel-request-lifecycle";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";
import { Check, Copy, LoaderCircle, Play, Square } from "lucide-react";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DOCUMENT_ANALYSIS_API_ID, DOCUMENT_MEDIA_TYPES, DOCUMENT_MODELS, MAX_DOCUMENT_BYTES, DocumentAnalysisResponseError, getDocumentAnalysisEndpoint, inspectDocument, readDocumentAnalysisResponse, type DocumentAnalysisFailure, type DocumentAnalysisModel, type DocumentAnalysisResult } from "@/lib/document-analysis-contracts";
import { getDocumentAnalysisExample, getDocumentAnalysisRequestSchema, getDocumentAnalysisResponseSchema } from "@/lib/document-analysis-integration";
import { getDocumentAnalysisDictionary } from "@/lib/i18n/document-analysis";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";
import { INTEGRATION_LANGUAGES, type IntegrationLanguage } from "@/lib/model-integration";
import { loadModelKeyChoices, type ModelKeyChoice } from "@/lib/model-key-choices";

/** ページを開いている間だけ保持する選択値と実行状態です。ファイルや秘密を永続化しません。 */
type DocumentState = { model: DocumentAnalysisModel; apiKeyId: string; file: File | null; language: IntegrationLanguage; tab: "request" | "schema" | "response"; loading: boolean; cancelled: boolean; result: DocumentAnalysisResult | null; failure: DocumentAnalysisFailure | null };
const languageLabels: Record<IntegrationLanguage, string> = { typescript: "TypeScript", python: "Python", php: "PHP", csharp: "C#", curl: "cURL" };

/** 文字列としてコードを表示し、HTMLやMarkdownとして実行しません。 */
function Code({ title, code, dictionary }: { title: string; code: string; dictionary: Dictionary }) {
  const [copied, setCopied] = useState(false); const [failed, setFailed] = useState(false);
  return <div className="overflow-hidden rounded-xl border border-border bg-muted/25">
    <div className="flex items-center justify-between gap-3 border-b border-border bg-white px-4 py-2"><h3 className="text-sm font-semibold">{title}</h3><Button type="button" variant="ghost" size="sm" onClick={async () => { try { await navigator.clipboard.writeText(code); setCopied(true); setFailed(false); } catch { setFailed(true); } }}>{copied ? <Check className="size-4" /> : <Copy className="size-4" />}{copied ? dictionary.modelGateway.copied : dictionary.modelGateway.integration.copyCode}</Button></div>
    <pre className="whitespace-pre-wrap break-words p-5 font-mono text-[14px] leading-7"><code>{code}</code></pre>
    {failed && <p role="status" className="px-4 pb-3 text-sm text-destructive">{dictionary.modelGateway.copyFailed}</p>}
  </div>;
}

/** ガイド・仕様・実ファイルでの解析を同じ契約で確認する文書解析ページです。 */
export function DocumentAnalysisPanel({ locale, dictionary, gatewayOrigin }: { locale: Locale; dictionary: Dictionary; gatewayOrigin: string | null }) {
  const formId = useFieldIds();
  const [store] = useState(() => createStore<DocumentState>(() => ({ model: "prebuilt-read", apiKeyId: "", file: null, language: "curl", tab: "request", loading: false, cancelled: false, result: null, failure: null })));
  const state = useStore(store); const text = getDocumentAnalysisDictionary(locale); const common = dictionary.modelGateway; const guide = common.integration;
  const [choices, setChoices] = useState<{ status: "loading" | "ready" | "error"; keys: ModelKeyChoice[] }>({ status: "loading", keys: [] });
  const [refresh, setRefresh] = useState(0); const request = useRef<AbortController | null>(null);
  const selectedKey = choices.status === "ready" ? choices.keys.find((key) => key.id === state.apiKeyId) : undefined;
  const origin = gatewayOrigin ?? "https://gateway.example.com";
  useEffect(() => () => deactivatePanelRequest(request, (pending) => store.setState({ loading: false, ...(pending ? { cancelled: true } : {}) })), [store]);
  useEffect(() => {
    const controller = new AbortController();
    void loadModelKeyChoices(DOCUMENT_ANALYSIS_API_ID, controller.signal).then((keys) => { if (!controller.signal.aborted) setChoices({ status: "ready", keys }); }).catch(() => { if (!controller.signal.aborted) setChoices({ status: "error", keys: [] }); });
    return () => controller.abort();
  }, [refresh]);

  /** 条件を変えたときに以前の解析を消し、別文書の結果との混同を防ぎます。 */
  function update(patch: Partial<DocumentState>) { store.setState({ result: null, failure: null, cancelled: false, ...patch }); }

  /** ファイルを送る直前に形式を確認し、選択キーの台帳IDだけをBFFへ渡します。 */
  async function execute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (request.current || !state.file || !selectedKey || !gatewayOrigin) return;
    const controller = new AbortController(); request.current = controller;
    store.setState({ loading: true, cancelled: false, failure: null, result: null });
    try {
      const bytes = new Uint8Array(await state.file.arrayBuffer());
      inspectDocument(bytes, state.file.type);
      controller.signal.throwIfAborted();
      const form = new FormData(); form.append("input", JSON.stringify({ model: state.model, apiKeyId: selectedKey.id })); form.append("file", state.file);
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(105_000)]);
      const response = await fetch("/api/tools/documents", { method: "POST", credentials: "same-origin", body: form, signal });
      const result = await readDocumentAnalysisResponse(response, state.model, signal);
      if (!controller.signal.aborted) store.setState({ loading: false, result });
    } catch (error) {
      if (!controller.signal.aborted) store.setState({ loading: false, failure: error instanceof DocumentAnalysisResponseError ? error.failure : { error: { code: error instanceof TypeError || error instanceof RangeError ? "UnsupportedDocument" : error instanceof DOMException && error.name === "TimeoutError" ? "DocumentAnalysisTimeout" : "DocumentAnalysisFailed" } } });
    } finally { if (request.current === controller) request.current = null; }
  }

  const settings = <form onSubmit={execute} className="space-y-5">
    <div className="space-y-2"><div className="flex items-center justify-between gap-3"><label htmlFor={formId("document-api-key")} className="text-sm font-medium">{text.apiKey}</label><Button type="button" size="sm" variant="ghost" disabled={state.loading || choices.status === "loading"} onClick={() => { setChoices({ status: "loading", keys: [] }); setRefresh((value) => value + 1); }}>{guide.refreshKeys}</Button></div>
      <Select value={selectedKey?.id ?? ""} disabled={state.loading || choices.status !== "ready" || !choices.keys.length} onValueChange={(apiKeyId) => update({ apiKeyId })}><SelectTrigger id={formId("document-api-key")} className="w-full min-w-0"><SelectValue placeholder={guide.chooseKey} /></SelectTrigger><SelectContent>{choices.keys.map((key) => <SelectItem key={key.id} value={key.id}><span className="block truncate">{[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}</span></SelectItem>)}</SelectContent></Select>
      <p className="text-sm leading-6 text-muted-foreground">{guide.apiKeyHelp}</p>
      {choices.status === "loading" && <p role="status" className="text-sm text-muted-foreground">{guide.loadingKeys}</p>}
      {choices.status === "error" && <p role="alert" className="text-sm text-destructive">{text.errors.ApiKeyAccessUnavailable}</p>}
      {choices.status === "ready" && !choices.keys.length && <p className="text-sm leading-6">{guide.noKeys} <Link href={`/${locale}/api-keys`} className="text-primary underline underline-offset-4">{guide.manageKeys}</Link></p>}
    </div>
    <div className="space-y-3"><label htmlFor={formId("document-file")} className="block text-sm font-medium">{text.file}</label><Input id={formId("document-file")} type="file" accept={DOCUMENT_MEDIA_TYPES.join(",")} disabled={state.loading} aria-describedby={formId("document-file-help")} onChange={(event) => {
      const file = event.target.files?.[0] ?? null;
      if (file && (file.size === 0 || file.size > MAX_DOCUMENT_BYTES || !DOCUMENT_MEDIA_TYPES.some((type) => type === file.type))) update({ file: null, failure: { error: { code: file.size > MAX_DOCUMENT_BYTES ? "DocumentTooLarge" : "UnsupportedDocument" } } });
      else update({ file });
    }} /><p id={formId("document-file-help")} className="text-sm leading-7 text-muted-foreground">{text.fileHelp}</p>{state.file && <p className="break-words text-sm">{state.file.name} · {(state.file.size / 1024).toFixed(1)} KiB</p>}</div>
    <p className="text-sm leading-7 text-muted-foreground">{text.privacy}</p>
    <div className="flex flex-wrap gap-3">{state.loading && <Button type="button" variant="outline" onClick={() => { request.current?.abort(); request.current = null; store.setState({ loading: false, cancelled: true }); }}><Square className="size-4" />{common.cancel}</Button>}<Button type="submit" disabled={state.loading || !state.file || !selectedKey || !gatewayOrigin}>{state.loading ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}{state.loading ? text.running : text.execute}</Button></div>
  </form>;

  const result = state.result;
  const response = <div className="space-y-5" aria-busy={state.loading}>
    {state.failure && <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/5 p-4 text-sm leading-7 text-destructive"><p>{text.errors[state.failure.error.code]}</p>{state.failure.requestId && <p className="break-all font-mono">{common.requestId}: {state.failure.requestId}</p>}{state.failure.retryAfterSeconds && <p>{common.retryAfter.replace("{count}", String(state.failure.retryAfterSeconds))}</p>}</div>}
    {state.loading && <p role="status" className="text-sm leading-7 text-muted-foreground">{text.waiting}</p>}
    {state.cancelled && <p role="status" className="text-sm leading-7 text-muted-foreground">{text.cancelled}</p>}
    {!result && !state.loading && !state.failure && !state.cancelled && <p className="text-sm leading-7 text-muted-foreground">{text.noResult}</p>}
    {result && <><div className="flex flex-wrap gap-4 text-sm text-muted-foreground"><span>{result.model}</span><span>{(result.durationMs / 1000).toFixed(1)} s</span><span>{text.pages}: {result.pageNumbers.length}</span><span>{text.tables}: {result.tables.length}</span></div><p className="text-sm text-muted-foreground">{text.pageNumbers}: {result.pageNumbers.join(", ")}</p>
      <Code title={text.text} code={result.content || text.emptyText} dictionary={dictionary} />
      {state.model === "prebuilt-layout" && <section className="space-y-4"><h3 className="font-semibold">{text.tables}</h3>{!result.tables.length && <p className="text-sm text-muted-foreground">{text.noTables}</p>}{result.tables.map((table, index) => <div key={index} className="overflow-hidden rounded-xl border border-border"><h4 className="border-b border-border bg-muted/25 px-4 py-3 text-sm font-semibold">{text.table} {index + 1} · {table.rowCount} × {table.columnCount}</h4><table className="w-full table-fixed text-left text-sm"><caption className="sr-only">{text.cells}</caption><thead><tr className="border-b border-border"><th scope="col" className="w-16 p-3">{text.row}</th><th scope="col" className="w-16 p-3">{text.column}</th><th scope="col" className="p-3">{text.cell}</th></tr></thead><tbody>{table.cells.map((cell, cellIndex) => <tr key={cellIndex} className="border-b border-border last:border-0"><td className="p-3 align-top">{cell.rowIndex + 1}</td><td className="p-3 align-top">{cell.columnIndex + 1}</td><td className="break-words whitespace-pre-wrap p-3">{cell.content}</td></tr>)}</tbody></table></div>)}</section>}
      <details className="rounded-xl border border-border"><summary className="cursor-pointer px-5 py-4 text-sm font-semibold">{text.raw}</summary><Code title={text.raw} code={JSON.stringify(result.rawResponse, null, 2)} dictionary={dictionary} /></details>
      {result.requestId && <p className="break-all font-mono text-sm text-muted-foreground">{common.requestId}: {result.requestId}</p>}
    </>}
  </div>;

  return <ApiDocumentationLayout locale={locale} value={state.tab} onValueChange={(tab) => store.setState({ tab })} busy={state.loading}
    selection={<div className="max-w-xl space-y-2"><label htmlFor={formId("document-model")} className="block text-sm font-medium">{text.model}</label><Select value={state.model} disabled={state.loading} onValueChange={(model) => { if (DOCUMENT_MODELS.includes(model as DocumentAnalysisModel)) update({ model: model as DocumentAnalysisModel }); }}><SelectTrigger id={formId("document-model")} className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="prebuilt-read">{text.read}</SelectItem><SelectItem value="prebuilt-layout">{text.layout}</SelectItem></SelectContent></Select><p className="text-sm leading-7 text-muted-foreground">{state.model === "prebuilt-read" ? text.readHelp : text.layoutHelp}</p></div>}
    settings={settings} response={response}
    request={<div className="space-y-6"><p className="text-[15px] leading-8 text-muted-foreground">{text.intro}</p><div className="rounded-xl border border-border px-5 py-4"><span className="mr-3 font-bold text-primary">POST</span><code className="break-all text-sm">{getDocumentAnalysisEndpoint(origin, state.model)}</code></div><p className="text-sm leading-7 text-muted-foreground">{text.async}</p><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-sm font-semibold">{guide.code}</h2><Select value={state.language} onValueChange={(language) => { if (INTEGRATION_LANGUAGES.includes(language as IntegrationLanguage)) store.setState({ language: language as IntegrationLanguage }); }}><SelectTrigger aria-label={guide.language} className="w-48"><SelectValue /></SelectTrigger><SelectContent>{INTEGRATION_LANGUAGES.map((language) => <SelectItem key={language} value={language}>{languageLabels[language]}</SelectItem>)}</SelectContent></Select></div><p className="text-sm leading-7 text-muted-foreground">{guide.keySetup}</p><Code title={languageLabels[state.language]} code={getDocumentAnalysisExample(state.language, origin, state.model)} dictionary={dictionary} /><p className="text-sm leading-7 text-muted-foreground">{text.limits}</p><p className="text-sm leading-7 text-muted-foreground">{text.timeoutNote}</p><a href="https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/analyze-document?view=rest-aiservices-v4.0%20(2024-11-30)" target="_blank" rel="noopener noreferrer" className="text-sm font-medium text-primary underline underline-offset-4">{text.docs}</a>{!gatewayOrigin && <p className="text-sm text-amber-700">{guide.notConfigured}</p>}</div>}
    schema={<div className="space-y-6"><p className="text-sm leading-7 text-muted-foreground">{text.limits}</p><ApiSchemaReference locale={locale} title={guide.requestSchema} schema={getDocumentAnalysisRequestSchema(locale)} /><ApiSchemaReference locale={locale} title={guide.responseSchema} schema={getDocumentAnalysisResponseSchema(locale)} /></div>}
  />;
}

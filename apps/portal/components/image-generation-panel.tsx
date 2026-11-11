"use client";

import { useFieldIds } from "@/lib/use-field-ids";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Image from "next/image";
import Link from "next/link";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";
import { Check, Copy, LoaderCircle, Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import { usePortalStore } from "@/components/portal-store-provider";
import { useModelSelection } from "@/components/use-model-selection";
import { restoreModelSettings } from "@/lib/model-settings";
import { getImageGenerationDictionary } from "@/lib/i18n/image-generation";
import { loadModelKeyChoices, type ModelKeyChoice } from "@/lib/model-key-choices";
import { ModelResponseError } from "@/lib/model-response";
import { MAX_IMAGE_PROMPT_CHARACTERS, imageGenerationInputSchema, type ImageGenerationInput, type ImageGenerationResult } from "@/lib/image-request";
import { imageExternalUrl, imagePreviewSource, imageResponsePreview, readImageResponse } from "@/lib/image-response";
import { SAMPLE_IMAGE_PROMPT, buildImageGenerationRequest, defaultImageInput, getImageGenerationEndpoint, getImageGenerationHeaders, getImageIntegrationExample, getImageRequestSchema, getImageResponseSchema } from "@/lib/image-integration";
import { INTEGRATION_LANGUAGES, type IntegrationLanguage } from "@/lib/model-integration";
import type { ImageGenerationModel } from "@/lib/model-types";
import type { TextGenerationFailure } from "@/lib/model-request";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** 表示状態をパネルごとのZustandに閉じ、画像と他パネルの実行結果を混在させません。 */
type ImagePanelState = { input: ImageGenerationInput; apiKeyId: string; language: IntegrationLanguage | "json"; tab: "request" | "schema" | "response"; loading: boolean; result: ImageGenerationResult | null; failure: TextGenerationFailure | null; cancelled: boolean };
const languageNames = { typescript: "TypeScript", python: "Python", php: "PHP", csharp: "C#", curl: "cURL" };

/** 表示を短縮した場合も、コピーには省略前の完全なJSONを使います。 */
function ImageCodeBlock({ title, code, copyCode = code, copyLabel, copiedLabel, failedLabel }: { title: string; code: string; copyCode?: string; copyLabel: string; copiedLabel: string; failedLabel: string }) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  /** コピー操作の成功と失敗を画面で案内します。 */
  async function copy() { try { await navigator.clipboard.writeText(copyCode); setStatus("copied"); } catch { setStatus("failed"); } }
  return <div className="overflow-hidden rounded-xl border border-border bg-slate-50"><div className="flex items-center justify-between gap-3 border-b border-border bg-white px-4 py-2"><span className="text-sm font-semibold text-muted-foreground">{title}</span><Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 text-sm" onClick={copy}>{status === "copied" ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{status === "copied" ? copiedLabel : copyLabel}</Button></div><pre className="whitespace-pre-wrap break-words p-5 font-mono text-[14px] leading-7" aria-label={title}><code>{code}</code></pre>{status === "failed" && <p role="status" className="px-4 pb-3 text-sm text-destructive">{failedLabel}</p>}</div>;
}

/** 編集したプロンプトで画像を生成し、組み込み例と応答を確認します。 */
export function ImageGenerationPanel({ models, dictionary, locale, gatewayOrigin }: { models: ImageGenerationModel[]; dictionary: Dictionary; locale: Locale; gatewayOrigin: string | null }) {
  const fieldId = useFieldIds();
  const { scope, initialModel, rememberModel } = useModelSelection("image", models);
  const savedSettings = usePortalStore((state) => state.modelSettings.image[scope]), rememberSettings = usePortalStore((state) => state.setModelSettings);
  const text = dictionary.modelGateway;
  const guide = text.integration;
  const imageText = getImageGenerationDictionary(locale);
  const draft = usePortalStore((state) => state.modelDrafts.imagePrompt), updateDrafts = usePortalStore((state) => state.updateModelDrafts);
  const [store] = useState(() => createStore<ImagePanelState>(() => ({ input: { ...restoreModelSettings("image", initialModel ? defaultImageInput(initialModel) : { modelId: "", width: 1024, height: 1024, size: "", quality: "", outputFormat: "png" as const, prompt: SAMPLE_IMAGE_PROMPT }, savedSettings, (input) => { if (initialModel) buildImageGenerationRequest(initialModel, input); }), ...(draft !== null ? { prompt: draft } : {}) }, apiKeyId: "", language: "json", tab: "request", loading: false, result: null, failure: null, cancelled: false })));
  const state = useStore(store);
  const controller = useRef<AbortController | null>(null);
  const [keyState, setKeyState] = useState<{ apiId: string; status: "loading" | "ready" | "error"; keys: ModelKeyChoice[] }>({ apiId: "", status: "loading", keys: [] });
  const [refresh, setRefresh] = useState(0);
  const model = models.find((entry) => entry.id === state.input.modelId) ?? models[0];
  const apiId = model?.apiId ?? "";
  const keysReady = keyState.apiId === apiId && keyState.status === "ready";
  const selectedKey = keysReady ? keyState.keys.find((key) => key.id === state.apiKeyId) : undefined;
  const parsed = imageGenerationInputSchema.safeParse(state.input);
  let payload: Record<string, unknown> | null = null;
  try { if (model && parsed.success) payload = buildImageGenerationRequest(model, parsed.data); } catch { /* 入力途中の値は送信せず、フォームのエラーとして表示します。 */ }
  const origin = gatewayOrigin ?? "https://gateway.example.com";
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (!apiId) return;
    const abort = new AbortController();
    void loadModelKeyChoices(apiId, abort.signal).then((keys) => { if (!abort.signal.aborted) setKeyState({ apiId, status: "ready", keys }); }).catch(() => { if (!abort.signal.aborted) setKeyState({ apiId, status: "error", keys: [] }); });
    return () => abort.abort();
  }, [apiId, refresh]);
  if (!model) return null;

  /** 所属権限が変わった古いキー一覧を使わず、候補を読み直します。 */
  function refreshKeys() { setKeyState({ apiId, status: "loading", keys: [] }); setRefresh((value) => value + 1); }
  /** パラメーターを更新すると、同じ設定からコード例とJSONを作り直します。 */
  function updateInput(value: Partial<ImageGenerationInput>) { if (value.prompt !== undefined) updateDrafts({ imagePrompt: value.prompt }); const input = { ...store.getState().input, ...value }; rememberSettings("image", scope, input); store.setState({ input, result: null, failure: null, cancelled: false }); }
  /** 表示したプロンプトと設定を送り、接続先とキーはサーバーで解決します。 */
  async function execute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (controller.current || !payload || !selectedKey || !parsed.success) return;
    const abort = new AbortController(); controller.current = abort;
    store.setState({ loading: true, tab: "response", result: null, failure: null, cancelled: false });
    try {
      const response = await fetch("/api/models/images", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...parsed.data, apiKeyId: selectedKey.id }), signal: abort.signal });
      const result = await readImageResponse(response, abort.signal);
      if (!abort.signal.aborted) store.setState({ result });
    } catch (error) { if (!abort.signal.aborted) store.setState({ failure: error instanceof ModelResponseError ? error.failure : { error: { code: "GenerationFailed" } } }); }
    finally { if (controller.current === abort) { controller.current = null; store.setState({ loading: false }); } }
  }
  /** 画面の待機を止めます。生成済み処理への自動再試行は行いません。 */
  function cancel() { controller.current?.abort(); controller.current = null; store.setState({ loading: false, cancelled: true }); }
  const blockProps = { copyLabel: guide.copyJson, copiedLabel: text.copied, failedLabel: text.copyFailed };
  const requestCode = payload ? state.language === "json" ? JSON.stringify(payload, null, 2) : getImageIntegrationExample(state.language, model, state.input, origin) : text.errors.InvalidRequest;
  const raw = state.result ? imageResponsePreview(state.result.rawResponse) : null;
  const range = (min: number, max: number) => imageText.range.replace("{min}", String(min)).replace("{max}", String(max));
  return <ApiDocumentationLayout locale={locale} value={state.tab} onValueChange={(tab) => store.setState({ tab })} busy={state.loading}
    selection={<>
      <div className="max-w-xl"><div className="space-y-2"><label htmlFor={fieldId("image-model")} className="text-sm font-medium">{text.model}</label><Select value={model.id} disabled={state.loading} onValueChange={(id) => { const next = models.find((entry) => entry.id === id); if (next) { rememberModel({ id: next.id }); updateInput({ ...defaultImageInput(next), prompt: state.input.prompt }); } }}><SelectTrigger id={fieldId("image-model")} className="h-10 w-full bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent>{models.map((entry) => <SelectItem key={entry.id} value={entry.id}>{entry.label}</SelectItem>)}</SelectContent></Select></div></div>
    </>}
    settings={
      <form onSubmit={execute} className="space-y-5">
      <div className="space-y-2 rounded-lg border border-border bg-white p-3">
        <div className="flex items-center justify-between gap-3"><label htmlFor={fieldId("image-api-key")} className="text-sm font-medium">{guide.apiKey}</label><Button type="button" variant="ghost" size="sm" disabled={state.loading || keyState.status === "loading"} onClick={refreshKeys} className="h-7 text-sm">{guide.refreshKeys}</Button></div>
        <Select value={selectedKey?.id ?? ""} disabled={state.loading || !keysReady || !keyState.keys.length} onValueChange={(apiKeyId) => store.setState({ apiKeyId })}><SelectTrigger id={fieldId("image-api-key")} aria-describedby={fieldId("image-key-help")} className="h-10 w-full min-w-0 bg-white text-sm"><SelectValue placeholder={guide.chooseKey} /></SelectTrigger><SelectContent>{keyState.keys.map((key) => <SelectItem key={key.id} value={key.id}><span className="block min-w-0 truncate" title={[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}>{[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}</span></SelectItem>)}</SelectContent></Select>
        <p id={fieldId("image-key-help")} className="text-sm leading-6 text-muted-foreground">{guide.apiKeyHelp}</p>
        {(keyState.apiId !== apiId || keyState.status === "loading") && <p role="status" className="text-sm text-muted-foreground">{guide.loadingKeys}</p>}
        {keyState.apiId === apiId && keyState.status === "error" && <p role="alert" className="text-sm text-destructive">{text.errors.ApiKeyAccessUnavailable}</p>}
        {keysReady && !keyState.keys.length && <p className="text-sm leading-6 text-muted-foreground">{guide.noKeys} <Link href={`/${locale}/api-keys`} className="text-primary underline underline-offset-4">{guide.manageKeys}</Link></p>}
      </div>
      <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><label htmlFor={fieldId("image-format")} className="text-sm font-medium">{imageText.format}</label><Select value={state.input.outputFormat} disabled={state.loading} onValueChange={(value) => { if (value === "png" || value === "jpeg") updateInput({ outputFormat: value }); }}><SelectTrigger id={fieldId("image-format")} className="h-10 w-full bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent>{model.formats.map((format) => <SelectItem key={format} value={format}>{format.toUpperCase()}</SelectItem>)}</SelectContent></Select></div></div>
      <div className="grid gap-4 sm:grid-cols-2">{model.operationId === "image-generate" ? <>{([ ["size", imageText.size, model.sizes], ["quality", imageText.quality, model.qualities] ] as const).map(([field, label, choices]) => <div key={field} className="space-y-2"><label htmlFor={fieldId(`image-${field}`)} className="text-sm font-medium">{label}</label><Input id={fieldId(`image-${field}`)} list={fieldId(`image-${field}-choices`)} value={state.input[field]} disabled={state.loading} onChange={(event) => updateInput({ [field]: event.target.value })} className="h-10 bg-white text-sm" /><datalist id={fieldId(`image-${field}-choices`)}>{choices.map((value) => <option key={value} value={value} />)}</datalist></div>)}</> : <>{([ ["width", imageText.width, model.minWidth, model.maxWidth], ["height", imageText.height, model.minHeight, model.maxHeight] ] as const).map(([field, label, min, max]) => <div key={field} className="space-y-2"><label htmlFor={fieldId(`image-${field}`)} className="text-sm font-medium">{label}</label><Input id={fieldId(`image-${field}`)} type="number" min={min} max={max === Number.MAX_SAFE_INTEGER ? undefined : max} step={1} required disabled={state.loading} value={Number.isFinite(state.input[field]) ? state.input[field] : ""} onChange={(event) => updateInput({ [field]: event.target.valueAsNumber })} aria-describedby={fieldId(`image-${field}-range`)} className="h-10 bg-white text-sm" /><p id={fieldId(`image-${field}-range`)} className="text-sm text-muted-foreground">{max === Number.MAX_SAFE_INTEGER ? imageText.providerDimensions : range(min, max)}</p></div>)}</>}</div>
      <div className="space-y-2"><label htmlFor={fieldId("image-count")} className="text-sm font-medium">{imageText.count}</label><Input id={fieldId("image-count")} type="number" min={model.minImages ?? 1} max={model.maxImages ?? 1} step={1} value={state.input.count ?? 1} disabled={state.loading || model.maxImages === 1} onChange={(event) => updateInput({ count: event.target.valueAsNumber })} /></div>
      <div className="space-y-2"><label htmlFor={fieldId("image-prompt")} className="block text-sm font-medium">{imageText.prompt}</label><Textarea id={fieldId("image-prompt")} rows={4} maxLength={MAX_IMAGE_PROMPT_CHARACTERS} required disabled={state.loading} value={state.input.prompt} placeholder={SAMPLE_IMAGE_PROMPT} onChange={(event) => updateInput({ prompt: event.target.value })} className="bg-white text-sm leading-7" /></div>
      <p className="text-sm leading-6 text-muted-foreground">{imageText.fixedNotice}</p><p className="text-sm leading-6 text-muted-foreground">{imageText.wait}</p>
      <div className="flex justify-end gap-2">{state.loading && <Button type="button" size="sm" variant="outline" onClick={cancel}><Square className="size-3" />{text.cancel}</Button>}<Button type="submit" size="sm" disabled={state.loading || !payload || !selectedKey || !gatewayOrigin}>{state.loading ? <LoaderCircle className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}{state.loading ? imageText.running : imageText.run}</Button></div>
      {!payload && <p role="alert" className="text-sm text-destructive">{text.errors.InvalidRequest}</p>}
    </form>
    }
    request={<div className="space-y-6">
      <p className="text-[15px] leading-8 text-muted-foreground">{imageText.integrationGuide}</p>
<div className="rounded-lg border border-border px-4 py-3"><span className="mr-3 text-sm font-bold text-primary">POST</span><code className="break-all text-sm">{getImageGenerationEndpoint(model, origin)}</code></div>{!gatewayOrigin && <p className="text-sm text-amber-700">{guide.notConfigured}</p>}<div className="flex justify-end"><Select value={state.language} onValueChange={(value) => { if (value === "json" || INTEGRATION_LANGUAGES.includes(value as IntegrationLanguage)) store.setState({ language: value as ImagePanelState["language"] }); }}><SelectTrigger className="w-48 bg-white text-sm" aria-label={imageText.language}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="json">{imageText.json}</SelectItem>{INTEGRATION_LANGUAGES.map((language) => <SelectItem key={language} value={language}>{languageNames[language]}</SelectItem>)}</SelectContent></Select></div>{state.language !== "json" && <p className="text-sm leading-6 text-muted-foreground">{imageText.backend}</p>}<ImageCodeBlock key={requestCode} code={requestCode} title={state.language === "json" ? imageText.json : languageNames[state.language]} {...blockProps} />{state.language === "json" && <ImageCodeBlock code={JSON.stringify(getImageGenerationHeaders(model), null, 2)} title={imageText.headers} {...blockProps} />}
    </div>}
    schema={<div className="space-y-6">
      <p className="text-sm leading-6 text-muted-foreground">{imageText.schema}</p><ApiSchemaReference locale={locale} schema={getImageRequestSchema(model)} title={imageText.requestSchema} /><ApiSchemaReference locale={locale} schema={getImageResponseSchema()} title={imageText.responseSchema} /><p className="text-sm leading-6 text-muted-foreground">{imageText.source}: <a href="https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/dall-e" target="_blank" rel="noopener noreferrer" className="text-primary underline">{imageText.openaiDocs}</a>{" / "}<a href="https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-flux" target="_blank" rel="noopener noreferrer" className="text-primary underline">{imageText.fluxDocs}</a></p>
    </div>}
    response={<div className="space-y-6" aria-busy={state.loading}>
      {state.failure && <div role="alert" className="space-y-2 rounded-xl border border-destructive/20 bg-destructive/4 p-4 text-sm leading-6 text-destructive"><p>{text.errors[state.failure.error.code]}</p>{state.failure.retryAfterSeconds && <p>{text.retryAfter.replace("{count}", String(state.failure.retryAfterSeconds))}</p>}{state.failure.requestId && <p className="break-all">{text.requestId}: {state.failure.requestId}</p>}</div>}{state.cancelled && <p role="status" className="text-sm text-muted-foreground">{text.cancelNotice}</p>}{state.loading && <p role="status" className="flex items-center gap-2 text-sm text-primary"><LoaderCircle className="size-4 animate-spin" />{imageText.running}</p>}{state.result && raw ? <><div className="flex flex-wrap gap-x-4 gap-y-2 text-sm text-muted-foreground"><span>{state.result.model}</span><span>{(state.result.durationMs / 1000).toFixed(1)} s</span><span>application/json</span></div>{state.result.images.map((image) => { const source = imagePreviewSource(state.result!, image); const externalUrl = imageExternalUrl(state.result!, image); return source ? <div key={image.index} className="overflow-hidden rounded-xl border border-border bg-white p-3"><Image src={source} alt={imageText.imageAlt} width={state.input.width} height={state.input.height} unoptimized className="mx-auto h-auto max-h-[32rem] w-auto max-w-full rounded-lg object-contain" /></div> : <p key={image.index} className="rounded-lg border border-border p-4 text-sm leading-6 text-muted-foreground">{imageText.previewUnavailable}{externalUrl && <a href={externalUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="mt-2 block text-primary underline underline-offset-4">{imageText.openImageUrl}</a>}</p>; })}{raw.truncated && <p className="text-sm leading-6 text-muted-foreground">{imageText.truncated}</p>}<ImageCodeBlock code={raw.text} copyCode={JSON.stringify(state.result.rawResponse, null, 2)} title={imageText.rawResponse} {...blockProps} copyLabel={guide.copyResponse} />{state.result.requestId && <p className="break-all font-mono text-sm text-muted-foreground">{text.requestId}: {state.result.requestId}</p>}</> : !state.loading && !state.failure && <p className="rounded-xl border border-dashed border-border p-6 text-sm leading-6 text-muted-foreground">{imageText.responseEmpty}</p>}
    </div>}
  />;
}

"use client";

import { useFieldIds } from "@/lib/use-field-ids";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";
import { Check, Copy, LoaderCircle, Play, Square } from "lucide-react";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import { useModelSelection } from "@/components/use-model-selection";
import { deactivatePanelRequest } from "@/lib/panel-request-lifecycle";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { defaultDecisionInput, buildDecisionRequest, getDecisionEndpoint, getDecisionHeaders, getDecisionIntegrationExample, getDecisionRequestSchema, getDecisionResponseSchema, readDecisionResponse } from "@/lib/decision-integration";
import { decisionInputSchema } from "@/lib/decision-request";
import { getDecisionDictionary } from "@/lib/i18n/decision";
import { INTEGRATION_LANGUAGES, type IntegrationLanguage } from "@/lib/model-integration";
import { loadModelKeyChoices, type ModelKeyChoice } from "@/lib/model-key-choices";
import { ModelResponseError } from "@/lib/model-response";
import type { DecisionInput, DecisionModel, DecisionResult } from "@/lib/decision-types";
import type { TextGenerationFailure } from "@/lib/model-request";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** 判断の入力と表示状態です。購読キーは台帳IDだけを保持します。 */
type PanelState = { modelId: string; stateJson: string; questionsJson: string; apiKeyId: string; language: IntegrationLanguage | "json"; tab: "request" | "schema" | "response"; loading: boolean; cancelled: boolean; result: DecisionResult | null; failure: TextGenerationFailure | null };
const languages: Record<IntegrationLanguage, string> = { typescript: "TypeScript", python: "Python", php: "PHP", csharp: "C#", curl: "cURL" };

/** 入力中の文字列を保ったまま、実行可能なJSONだけを取り出します。 */
function parsedInput(state: PanelState): DecisionInput | null {
  try { return decisionInputSchema.parse({ modelId: state.modelId, state: JSON.parse(state.stateJson), questions: JSON.parse(state.questionsJson) }); } catch { return null; }
}

/** 確率のキーだけでなく、利用者が入力した選択肢や段階の説明も表示します。 */
function criterionName(input: DecisionInput | null, questionName: string, key: string): string {
  const question = input?.questions[questionName];
  const description = question?.type === "score" ? question.criteria[Number(key)] : question?.type === "choice" ? question.criteria[key] : undefined;
  return description ? `${key} — ${description}` : key;
}

/** 長いコードもページの幅で読み、表示中の内容だけをコピーします。 */
function Code({ title, code, dictionary }: { title: string; code: string; dictionary: Dictionary }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  return <div className="overflow-hidden rounded-xl border border-border bg-muted/30"><div className="flex items-center justify-between gap-3 border-b border-border bg-white px-4 py-2"><span className="text-sm font-semibold">{title}</span><Button type="button" size="sm" variant="ghost" onClick={async () => { try { await navigator.clipboard.writeText(code); setCopied(true); } catch { setFailed(true); } }}>{copied ? <Check className="size-4" /> : <Copy className="size-4" />}{copied ? dictionary.modelGateway.copied : dictionary.modelGateway.integration.copyCode}</Button></div><pre className="whitespace-pre-wrap break-words p-5 font-mono text-[14px] leading-7"><code>{code}</code></pre>{failed && <p role="status" className="px-4 pb-3 text-sm text-destructive">{dictionary.modelGateway.copyFailed}</p>}</div>;
}

/** 状況と型付き質問を学び、同じ入力を実際の意思決定APIで確かめるページです。 */
export function DecisionPanel({ models, dictionary, locale, gatewayOrigin }: { models: DecisionModel[]; dictionary: Dictionary; locale: Locale; gatewayOrigin: string | null }) {
  const fieldId = useFieldIds();
  const { initialModel, rememberModel } = useModelSelection("decision", models);
  const [store] = useState(() => {
    const initial = initialModel && defaultDecisionInput(initialModel, locale);
    return createStore<PanelState>(() => ({ modelId: initialModel?.id ?? "", stateJson: JSON.stringify(initial?.state ?? {}, null, 2), questionsJson: JSON.stringify(initial?.questions ?? {}, null, 2), apiKeyId: "", language: "json", tab: "request", loading: false, cancelled: false, result: null, failure: null }));
  });
  const state = useStore(store);
  const [choices, setChoices] = useState<{ status: "loading" | "ready" | "error"; keys: ModelKeyChoice[] }>({ status: "loading", keys: [] });
  const [refresh, setRefresh] = useState(0);
  const requestRef = useRef<AbortController | null>(null);
  const model = models.find((item) => item.id === state.modelId) ?? models[0];
  const selectedKey = choices.status === "ready" ? choices.keys.find((key) => key.id === state.apiKeyId) : undefined;
  const input = parsedInput(state);
  const text = dictionary.modelGateway;
  const guide = text.integration;
  const decision = getDecisionDictionary(locale);
  const number = new Intl.NumberFormat(locale);
  let payload: Record<string, unknown> | null = null;
  try { if (model && input) payload = buildDecisionRequest(model, input); } catch { /* 不完全な編集内容は送信せず、そのまま修正できるよう残します。 */ }
  useEffect(() => () => deactivatePanelRequest(requestRef, (pending) => store.setState({ loading: false, ...(pending ? { cancelled: true } : {}) })), [store]);
  useEffect(() => {
    if (!model?.apiId) return;
    const controller = new AbortController();
    void loadModelKeyChoices(model.apiId, controller.signal).then((keys) => { if (!controller.signal.aborted) setChoices({ status: "ready", keys }); }).catch(() => { if (!controller.signal.aborted) setChoices({ status: "error", keys: [] }); });
    return () => controller.abort();
  }, [model?.apiId, refresh]);
  if (!model) return null;
  const origin = gatewayOrigin ?? "https://gateway.example.com";
  const code = payload && input ? state.language === "json" ? JSON.stringify(payload, null, 2) : getDecisionIntegrationExample(state.language, model, input, origin) : decision.invalid;

  /** 入力が変わったら前回結果を消し、別の入力の結果と混同しないようにします。 */
  function update(patch: Partial<PanelState>) { store.setState({ ...patch, result: null, failure: null, cancelled: false }); }

  /** 選択した台帳IDと検証済み入力を送り、購読キーはバックエンドで解決します。 */
  async function execute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (requestRef.current || !input || !payload || !selectedKey) return;
    const controller = new AbortController(); requestRef.current = controller;
    store.setState({ loading: true, result: null, failure: null, cancelled: false, tab: "response" });
    try {
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(100_000)]);
      const response = await fetch("/api/models/decision", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...input, apiKeyId: selectedKey.id }), signal });
      const result = await readDecisionResponse(response, signal);
      if (!controller.signal.aborted) store.setState({ loading: false, result });
    } catch (error) { if (!controller.signal.aborted) store.setState({ loading: false, failure: error instanceof ModelResponseError ? error.failure : { error: { code: "GenerationFailed" } } }); }
    finally { if (requestRef.current === controller) requestRef.current = null; }
  }

  return <ApiDocumentationLayout locale={locale} value={state.tab} onValueChange={(tab) => store.setState({ tab })} busy={state.loading}
    selection={<div className="max-w-xl space-y-2"><label htmlFor={fieldId("decision-model")} className="block text-sm font-medium">{text.model}</label><Select value={model.id} disabled={state.loading} onValueChange={(id) => { const next = models.find((item) => item.id === id); if (next) { rememberModel({ id }); update({ modelId: id }); } }}><SelectTrigger id={fieldId("decision-model")} className="w-full"><SelectValue /></SelectTrigger><SelectContent>{models.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent></Select></div>}
    request={<div className="space-y-6"><p className="text-[15px] leading-8 text-muted-foreground">{decision.introduction}</p><div className="grid gap-4 lg:grid-cols-3">{(["noul", "choice", "score"] as const).map((type) => <section key={type} className="rounded-xl border border-border bg-white p-5"><h2 className="font-semibold">{decision[type]}</h2><p className="mt-3 text-sm leading-7 text-muted-foreground">{decision[`${type}Help`]}</p></section>)}</div><p className="text-sm leading-7 text-muted-foreground">{decision.sample}</p><div className="rounded-lg border border-border px-4 py-3"><span className="mr-3 text-sm font-bold text-primary">POST</span><code className="break-all text-sm">{getDecisionEndpoint(model, origin)}</code></div><div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm font-semibold">{state.language === "json" ? guide.json : guide.code}</p><Select value={state.language} onValueChange={(language) => { if (language === "json" || INTEGRATION_LANGUAGES.includes(language as IntegrationLanguage)) store.setState({ language: language as IntegrationLanguage | "json" }); }}><SelectTrigger aria-label={guide.language} className="w-48"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="json">{guide.json}</SelectItem>{INTEGRATION_LANGUAGES.map((language) => <SelectItem key={language} value={language}>{languages[language]}</SelectItem>)}</SelectContent></Select></div>{state.language !== "json" && <p className="text-sm leading-7 text-muted-foreground">{guide.keySetup}</p>}<Code title={state.language === "json" ? guide.json : languages[state.language]} code={code} dictionary={dictionary} />{state.language === "json" && <Code title={guide.headers} code={JSON.stringify(getDecisionHeaders(), null, 2)} dictionary={dictionary} />}<p className="text-sm leading-7 text-muted-foreground">{decision.limits.replace("{bytes}", number.format(model.maxRequestBytes))}</p><p className="text-sm leading-7 text-muted-foreground">{decision.caution}</p><p className="text-sm leading-7 text-muted-foreground">{decision.preview} <a href="https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision" target="_blank" rel="noreferrer noopener" className="text-primary underline underline-offset-4">{decision.docs}</a></p>{!gatewayOrigin && <p className="text-sm text-amber-700">{guide.notConfigured}</p>}</div>}
    schema={<div className="space-y-6"><p className="text-sm leading-7 text-muted-foreground">{decision.schema}</p><ApiSchemaReference locale={locale} title={guide.requestSchema} schema={getDecisionRequestSchema(model, locale)} /><ApiSchemaReference locale={locale} title={guide.responseSchema} schema={getDecisionResponseSchema(locale)} /></div>}
    settings={<form onSubmit={execute} className="space-y-5"><div className="space-y-2"><div className="flex items-center justify-between gap-2"><label htmlFor={fieldId("decision-key")} className="text-sm font-medium">{guide.apiKey}</label><Button type="button" variant="ghost" size="sm" disabled={state.loading || choices.status === "loading"} onClick={() => { setChoices({ status: "loading", keys: [] }); setRefresh((value) => value + 1); }}>{guide.refreshKeys}</Button></div><Select value={selectedKey?.id ?? ""} disabled={state.loading || choices.status !== "ready" || !choices.keys.length} onValueChange={(apiKeyId) => store.setState({ apiKeyId })}><SelectTrigger id={fieldId("decision-key")} className="w-full min-w-0"><SelectValue placeholder={guide.chooseKey} /></SelectTrigger><SelectContent>{choices.keys.map((key) => <SelectItem key={key.id} value={key.id}><span className="block truncate">{[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}</span></SelectItem>)}</SelectContent></Select><p className="text-sm leading-6 text-muted-foreground">{guide.apiKeyHelp}</p>{choices.status === "loading" && <p role="status" className="text-sm">{guide.loadingKeys}</p>}{choices.status === "error" && <p role="alert" className="text-sm text-destructive">{text.errors.ApiKeyAccessUnavailable}</p>}{choices.status === "ready" && !choices.keys.length && <p className="text-sm">{guide.noKeys} <Link href={`/${locale}/api-keys`} className="text-primary underline">{guide.manageKeys}</Link></p>}</div><div className="space-y-2"><label htmlFor={fieldId("decision-state")} className="block text-sm font-medium">{decision.state}</label><Textarea id={fieldId("decision-state")} rows={7} value={state.stateJson} maxLength={model.maxRequestBytes} disabled={state.loading} onChange={(event) => update({ stateJson: event.target.value })} className="font-mono text-sm leading-7" /><p className="text-sm leading-6 text-muted-foreground">{decision.stateHelp}</p></div><div className="space-y-2"><label htmlFor={fieldId("decision-questions")} className="block text-sm font-medium">{decision.questions}</label><Textarea id={fieldId("decision-questions")} rows={15} value={state.questionsJson} maxLength={model.maxRequestBytes} disabled={state.loading} onChange={(event) => update({ questionsJson: event.target.value })} className="font-mono text-sm leading-7" /><p className="text-sm leading-6 text-muted-foreground">{decision.questionsHelp}</p></div><p className="text-sm leading-6 text-muted-foreground">{decision.limits.replace("{bytes}", number.format(model.maxRequestBytes))}</p>{!payload && <p role="alert" className="text-sm text-destructive">{decision.invalid}</p>}<div className="flex flex-wrap gap-2"><Button type="button" variant="outline" disabled={state.loading} onClick={() => { const sample = defaultDecisionInput(model, locale); update({ stateJson: JSON.stringify(sample.state, null, 2), questionsJson: JSON.stringify(sample.questions, null, 2) }); }}>{decision.reset}</Button>{state.loading && <Button type="button" variant="outline" onClick={() => { requestRef.current?.abort(); requestRef.current = null; store.setState({ loading: false, cancelled: true }); }}><Square className="size-4" />{text.cancel}</Button>}<Button type="submit" disabled={state.loading || !payload || !selectedKey || !gatewayOrigin}>{state.loading ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}{state.loading ? decision.running : decision.execute}</Button></div></form>}
    response={<div className="space-y-5" aria-busy={state.loading}>{state.failure && <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/5 p-4 text-sm leading-7 text-destructive"><p>{text.errors[state.failure.error.code]}</p>{state.failure.requestId && <p className="break-all">{text.requestId}: {state.failure.requestId}</p>}{state.failure.retryAfterSeconds && <p>{text.retryAfter.replace("{count}", number.format(state.failure.retryAfterSeconds))}</p>}</div>}{state.loading && <p role="status" className="text-sm text-muted-foreground">{decision.waiting}</p>}{state.cancelled && <p role="status" className="text-sm text-muted-foreground">{decision.cancelled}</p>}{!state.result && !state.loading && !state.failure && !state.cancelled && <p className="text-sm text-muted-foreground">{decision.noResult}</p>}{state.result && <><div className="flex flex-wrap gap-x-4 gap-y-2 text-sm text-muted-foreground"><span>{state.result.model}</span><span>{(state.result.durationMs / 1000).toFixed(1)} s</span>{state.result.usage?.inputTokens !== undefined && <span>{decision.inputTokens}: {number.format(state.result.usage.inputTokens)}</span>}{state.result.usage?.outputTokens !== undefined && <span>{decision.outputTokens}: {number.format(state.result.usage.outputTokens)}</span>}</div><h3 className="font-semibold">{decision.answer}</h3>{state.result.answers.map((answer) => <section key={answer.name} className="space-y-3 rounded-xl border border-border p-4"><div className="flex flex-wrap items-center justify-between gap-3"><h4 className="font-semibold">{answer.name} <span className="ml-2 text-sm font-normal text-muted-foreground">{answer.type}</span></h4><strong className="font-mono text-base">{answer.type === "noul" ? `${(Number(answer.value) * 100).toFixed(1)}%` : typeof answer.value === "number" ? number.format(answer.value) : answer.value}</strong></div><p className="text-sm text-muted-foreground">{answer.type === "noul" ? decision.probability : answer.type === "score" ? decision.weightedScore : decision.choice}</p>{answer.probabilities && <dl className="space-y-2">{Object.entries(answer.probabilities).map(([name, probability]) => <div key={name} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 text-sm"><dt className="break-words">{criterionName(input, answer.name, name)}</dt><dd>{(probability * 100).toFixed(1)}%</dd><div className="col-span-2 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${probability * 100}%` }} /></div></div>)}</dl>}{answer.confidence !== undefined && <p className="text-sm text-muted-foreground">{decision.confidence}: {(answer.confidence * 100).toFixed(1)}%</p>}</section>)}<Code title={guide.rawJson} code={JSON.stringify(state.result.rawResponse, null, 2)} dictionary={dictionary} />{state.result.requestId && <p className="break-all font-mono text-sm text-muted-foreground">{text.requestId}: {state.result.requestId}</p>}</>}</div>}
  />;
}

"use client";

import { useFieldIds } from "@/lib/use-field-ids";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { Check, Copy, LoaderCircle, Mic, MicOff, Square, Volume2 } from "lucide-react";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import { usePortalStore } from "@/components/portal-store-provider";
import { useModelSelection } from "@/components/use-model-selection";
import { restoreModelSettings } from "@/lib/model-settings";
import { getRealtimeAudioDictionary } from "@/lib/i18n/realtime-audio";
import { INTEGRATION_LANGUAGES, type IntegrationLanguage } from "@/lib/model-integration";
import { loadModelKeyChoices, type ModelKeyChoice } from "@/lib/model-key-choices";
import { chooseRealtimeVariant, groupRealtimeModels, updateRealtimeModelSelection } from "@/lib/realtime-model-choices";
import { REALTIME_SERIES } from "@/lib/realtime-series";
import { getRealtimeVoiceOptions } from "@/lib/realtime-voices";
import type { TextGenerationFailure } from "@/lib/model-request";
import { ModelResponseError } from "@/lib/model-response";
import type { RealtimeModel } from "@/lib/model-types";
import { startRealtimeConversation, RealtimeBrowserError, type RealtimeBrowserFailure, type RealtimeConversation } from "@/lib/realtime-browser";
import { appendRealtimeTranscript, joinRealtimeTranscriptionParagraph, stopRealtimeBrowser, type RealtimeCompletion, type RealtimeTurn } from "@/lib/realtime-client";
import { defaultRealtimeInput, getRealtimeEndpoint, getRealtimeIntegrationCode, getRealtimeProtocolGuide, getRealtimeRequestExample, getRealtimeRequestSchema, getRealtimeResponseSchema, validateRealtimeInput } from "@/lib/realtime-integration";
import { REALTIME_MAX_DURATION_MS, realtimeInputSchema, type RealtimeInput, type RealtimeStreamEvent } from "@/lib/realtime-request";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** 表示設定とメモリー内の会話履歴です。認証キー・SDP・音声録音は保持しません。 */
type ConversationState = { input: RealtimeInput; apiKeyId: string; language: IntegrationLanguage | "body"; tab: "request" | "schema" | "response"; connecting: boolean; connected: boolean; stopping: boolean; muted: boolean; playbackBlocked: boolean; turns: RealtimeTurn[]; timeline: { eventType: string; elapsedMs: number }[]; eventCount: number; elapsedSeconds: number; maximumDurationMs: number; usage: Record<string, number>; complete: Omit<RealtimeCompletion, "handle"> | null; failure: TextGenerationFailure | null; localFailure: RealtimeBrowserFailure | null; turnFailed: boolean; retrying: boolean; retryFailed: boolean; canRetryStop: boolean };
const languageNames: Record<IntegrationLanguage, string> = { typescript: "TypeScript", python: "Python", php: "PHP", csharp: "C#", curl: "cURL" };
const familyNames = Object.fromEntries(REALTIME_SERIES.map((series) => [series.family, series.label]));

/** 表示した実装例だけをコピーし、キーの実値は補完しません。 */
function RealtimeCode({ code, title, dictionary }: { code: string; title: string; dictionary: Dictionary }) {
  const [copied, setCopied] = useState(false);
  /** クリップボードの失敗を内部例外文なしで扱います。 */
  async function copy() { try { await navigator.clipboard.writeText(code); setCopied(true); } catch { setCopied(false); } }
  return <div className="overflow-hidden rounded-xl border border-border bg-slate-50"><div className="flex items-center justify-between gap-3 border-b border-border bg-white px-4 py-2"><span className="text-sm font-semibold text-muted-foreground">{title}</span><Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 text-sm" onClick={copy}>{copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copied ? dictionary.modelGateway.copied : dictionary.modelGateway.integration.copyCode}</Button></div><pre className="overflow-x-auto p-5 font-mono text-sm leading-7" aria-label={title}><code>{code}</code></pre></div>;
}

/** マイクを使った複数ターンの会話と、接続方法・イベントを確認する組み込みサンプルです。 */
export function RealtimeAudioPanel({ models, dictionary, locale, gatewayOrigin }: { models: RealtimeModel[]; dictionary: Dictionary; locale: Locale; gatewayOrigin: string | null }) {
  const fieldId = useFieldIds();
  const series = REALTIME_SERIES.find((entry) => entry.family === models[0]?.family);
  const text = dictionary.modelGateway, guide = text.integration, rt = getRealtimeAudioDictionary(locale, series?.family);
  const { scope, initialModel, rememberModel } = useModelSelection("realtime", models, series?.id);
  const savedSettings = usePortalStore((state) => state.modelSettings.realtime[scope]), rememberSettings = usePortalStore((state) => state.setModelSettings);
  const [store] = useState(() => createStore<ConversationState>(() => ({ input: restoreModelSettings("realtime", initialModel ? defaultRealtimeInput(initialModel) : { modelId: "" }, savedSettings, (input) => { if (initialModel) validateRealtimeInput(initialModel, input); }), apiKeyId: "", language: "body", tab: "request", connecting: false, connected: false, stopping: false, muted: false, playbackBlocked: false, turns: [], timeline: [], eventCount: 0, elapsedSeconds: 0, maximumDurationMs: REALTIME_MAX_DURATION_MS, usage: {}, complete: null, failure: null, localFailure: null, turnFailed: false, retrying: false, retryFailed: false, canRetryStop: false })));
  const state = useStore(store);
  const requestRef = useRef<AbortController | null>(null), conversationRef = useRef<RealtimeConversation | null>(null), stopHandleRef = useRef<string | undefined>(undefined);
  const [keyState, setKeyState] = useState<{ apiId: string; status: "loading" | "ready" | "error"; keys: ModelKeyChoice[] }>({ apiId: "", status: "loading", keys: [] });
  const [refresh, setRefresh] = useState(0);
  const model = models.find((entry) => entry.id === state.input.modelId) ?? models[0], apiId = model?.apiId ?? "";
  const modelChoices = groupRealtimeModels(models), modelChoice = modelChoices.find((choice) => choice.variants.some((entry) => entry.id === model?.id));
  const voiceOptions = model ? getRealtimeVoiceOptions(model) : [];
  const maximumOutputTokens = model?.numericMaxOutputTokens ?? model?.maxOutputTokens ?? 1;
  const keysReady = keyState.apiId === apiId && keyState.status === "ready", selectedKey = keysReady ? keyState.keys.find((entry) => entry.id === state.apiKeyId) : undefined;
  const parsed = realtimeInputSchema.safeParse(state.input), origin = gatewayOrigin ?? "https://gateway.example.com", number = new Intl.NumberFormat(locale);
  let valid = false;
  try { if (model && parsed.success) { validateRealtimeInput(model, parsed.data); valid = true; } } catch { /* モデル固有の指定範囲に合わない値は送信しません。 */ }
  const busy = state.connecting || state.connected || state.stopping, locked = busy || state.canRetryStop;

  useEffect(() => {
    /** 表示を離れた場合もマイクを止め、制御接続の終了を要求します。 */
    function leave() { requestRef.current?.abort(); void conversationRef.current?.stop(); const handle = stopHandleRef.current; if (handle) void stopRealtimeBrowser(handle).catch(() => undefined); }
    window.addEventListener("pagehide", leave);
    return () => { window.removeEventListener("pagehide", leave); leave(); };
  }, []);
  useEffect(() => {
    if (!apiId) return;
    const controller = new AbortController();
    void loadModelKeyChoices(apiId, controller.signal).then((keys) => { if (!controller.signal.aborted) setKeyState({ apiId, status: "ready", keys }); }).catch(() => { if (!controller.signal.aborted) setKeyState({ apiId, status: "error", keys: [] }); });
    return () => controller.abort();
  }, [apiId, refresh]);
  useEffect(() => { if (!state.connected) return; const timer = setInterval(() => store.setState((current) => ({ elapsedSeconds: current.elapsedSeconds + 1 })), 1_000); return () => clearInterval(timer); }, [state.connected, store]);

  /** 新しい設定に前の会話結果を混ぜません。接続中は設定変更を許可しません。 */
  function clearResult() { store.setState({ turns: [], timeline: [], eventCount: 0, elapsedSeconds: 0, usage: {}, complete: null, failure: null, localFailure: null, turnFailed: false, retryFailed: false, playbackBlocked: false }); }
  /** 編集した安全な設定だけをシリーズ別に記憶し、会話や購読は共有しません。 */
  function updateInput(patch: Partial<RealtimeInput>) { if (locked) return; const input = { ...store.getState().input, ...patch }; clearResult(); rememberSettings("realtime", scope, input); store.setState({ input }); }
  /** モデルと方式を別々に選んでも、開始時には一つの実API定義へ対応付けます。 */
  function selectModel(next: RealtimeModel) {
    if (locked) return;
    rememberModel({ id: next.id });
    const current = store.getState(), previous = models.find((entry) => entry.id === current.input.modelId);
    const selected = updateRealtimeModelSelection(current, previous, next);
    clearResult(); rememberSettings("realtime", scope, selected.input); store.setState(selected);
  }
  /** 会話の途中経過を採用し、セッションの終了通知だけで停止状態へ移ります。 */
  function receive(event: RealtimeStreamEvent) {
    if (event.type === "status") { store.setState({ elapsedSeconds: Math.floor(event.elapsedMs / 1000), ...(event.maximumDurationMs ? { maximumDurationMs: event.maximumDurationMs } : {}), ...(event.stage === "connected" ? { connected: true, connecting: false } : event.stage === "stopping" ? { connected: false, connecting: false, stopping: true } : {}) }); }
    else if (event.type === "transcript") store.setState((current) => ({ turns: appendRealtimeTranscript(current.turns, event) }));
    else if (event.type === "usage") store.setState({ usage: event.values });
    else if (event.type === "event") store.setState((current) => ({ eventCount: current.eventCount + 1, timeline: [...current.timeline.slice(-199), event], ...(event.eventType === "response.failed" ? { turnFailed: true } : event.eventType === "response.created" ? { turnFailed: false } : {}) }));
    else if (event.type === "error") { if (event.handle) stopHandleRef.current = event.handle; store.setState({ failure: { error: event.error, ...(event.requestId ? { requestId: event.requestId } : {}) } }); }
    else if (event.type === "complete") { const { handle, ...visible } = event; stopHandleRef.current = event.stopStatus === "confirmed" ? undefined : handle ?? stopHandleRef.current; store.setState({ connected: false, connecting: false, stopping: false, complete: visible, canRetryStop: Boolean(stopHandleRef.current) }); }
  }
  /** 明示した開始操作でだけマイクを取得し、利用者が停止するまで会話を続けます。 */
  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (requestRef.current || stopHandleRef.current || !model || !parsed.success || !valid || !selectedKey || !gatewayOrigin) return;
    clearResult(); const controller = new AbortController(); requestRef.current = controller;
    store.setState({ connecting: true, muted: false, tab: "response" });
    // Activityで非表示でも、自分の実行の終了通知は外部ストアへ反映します。
    const current = () => requestRef.current === controller;
    let stoppingObserved = false, startWaitFinished = false;
    try {
      const conversation = await startRealtimeConversation({ model, input: parsed.data, apiKeyId: selectedKey.id, signal: controller.signal, onEvent: (value) => {
        if (!current()) return;
        if (value.type === "status" && value.stage === "stopping") stoppingObserved = true;
        receive(value);
        if (value.type === "complete" && startWaitFinished) { requestRef.current = null; conversationRef.current = null; }
      }, onPlaybackBlocked: (playbackBlocked) => { if (current()) store.setState({ playbackBlocked }); } });
      if (!current() || controller.signal.aborted) { await conversation.stop(); return; }
      conversationRef.current = conversation; await conversation.completion;
    } catch (error) { if (current() && !controller.signal.aborted) { if (error instanceof RealtimeBrowserError) store.setState({ localFailure: error.reason, failure: null }); else store.setState({ failure: error instanceof ModelResponseError ? error.failure : { error: { code: "GenerationFailed" } } }); } }
    finally {
      // readyの取消がcompleteより先に返る間は、所有参照を残して停止結果を受け取ります。
      startWaitFinished = true;
      if (current() && (!stoppingObserved || store.getState().complete)) { requestRef.current = null; conversationRef.current = null; store.setState({ connecting: false, connected: false, stopping: false }); }
    }
  }
  /** 取消は端末のマイク停止とサーバーの終了確認を同じ操作にまとめます。 */
  function stop() { store.setState({ stopping: true }); if (conversationRef.current) void conversationRef.current.stop(); else requestRef.current?.abort(); }
  /** 所属とキーの一覧を読み直し、古い実行結果を消します。 */
  function refreshKeys() { clearResult(); setKeyState({ apiId, status: "loading", keys: [] }); setRefresh((value) => value + 1); }
  /** ミュート中も接続を維持し、利用者の次の発話へ戻れるようにします。 */
  function mute() { const muted = !store.getState().muted; conversationRef.current?.setMuted(muted); store.setState({ muted }); }
  /** 未確認の停止だけを再確認し、会話を自動再開しません。 */
  async function retryStop() { const handle = stopHandleRef.current; if (!handle) return; store.setState({ retrying: true, retryFailed: false }); try { if (await stopRealtimeBrowser(handle) === "confirmed") { stopHandleRef.current = undefined; const complete = store.getState().complete; store.setState({ canRetryStop: false, ...(complete ? { complete: { ...complete, stopStatus: "confirmed" } } : {}) }); } else store.setState({ retryFailed: true }); } catch { store.setState({ retryFailed: true }); } finally { store.setState({ retrying: false }); } }

  if (!model) return null;
  const documentation = {
    live: { href: "https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/gpt-live", label: rt.liveDocs },
    transcription: { href: "https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio-websockets#transcribe-audio-in-real-time", label: rt.transcriptionDocs },
    realtime: model.transport === "webrtc"
      ? { href: "https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/realtime-audio-webrtc", label: rt.rtcDocs }
      : { href: "https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/realtime-audio-websockets", label: rt.wsDocs },
    "voice-live": model.transport === "webrtc"
      ? { href: "https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-webrtc", label: rt.voiceDocs }
      : { href: "https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to", label: rt.voiceWsDocs },
  }[model.family];
  let code = text.errors.InvalidRequest;
  try { if (parsed.success && valid) code = state.language === "body" ? getRealtimeRequestExample(model, parsed.data) : getRealtimeIntegrationCode(model, parsed.data, origin, state.language, locale); } catch { /* 許可外設定を送らず、固定エラーで案内します。 */ }
  const stopLabels = { confirmed: rt.confirmed, "transport-closed": rt.transportClosed, unconfirmed: rt.unconfirmed };
  const visibleTurns = state.turns.filter((turn) => turn.text.trim().length > 0);
  const transcriptionParagraph = model.family === "transcription" ? joinRealtimeTranscriptionParagraph(state.turns) : "";
  const selection = <div className="space-y-5"><p className="text-sm leading-7 text-muted-foreground">{rt.introduction}</p><div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2"><label htmlFor={fieldId("realtime-model")} className="text-sm font-medium">{rt.model}</label><Select value={modelChoice?.id ?? ""} disabled={locked} onValueChange={(id) => { const choice = modelChoices.find((entry) => entry.id === id); if (choice) selectModel(chooseRealtimeVariant(choice, model.transport)); }}><SelectTrigger id={fieldId("realtime-model")} className="h-10 w-full bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent>{modelChoices.map((entry) => <SelectItem key={entry.id} value={entry.id}>{familyNames[entry.family]} · {entry.modelId}</SelectItem>)}</SelectContent></Select></div>
        <div className="space-y-2"><label htmlFor={fieldId("realtime-transport")} className="text-sm font-medium">{rt.transport}</label><Select value={model.transport} disabled={locked || !modelChoice || modelChoice.variants.length < 2} onValueChange={(transport) => { const next = modelChoice?.variants.find((entry) => entry.transport === transport); if (next) selectModel(next); }}><SelectTrigger id={fieldId("realtime-transport")} className="h-10 w-full bg-white text-sm" aria-describedby={fieldId("realtime-transport-help")}><SelectValue /></SelectTrigger><SelectContent>{modelChoice?.variants.map((entry) => <SelectItem key={entry.id} value={entry.transport}>{entry.transport === "webrtc" ? rt.webrtc : rt.websocket}</SelectItem>)}</SelectContent></Select><p id={fieldId("realtime-transport-help")} className="text-sm leading-5 text-muted-foreground">{rt.transportHelp}</p></div>
      </div></div>;
  const settings = <form id={fieldId("realtime-settings")} onSubmit={start} className="min-w-0 space-y-5">
      <div className="space-y-2 rounded-lg border border-border bg-white p-3"><div className="flex items-center justify-between gap-3"><label htmlFor={fieldId("realtime-api-key")} className="text-sm font-medium">{guide.apiKey}</label><Button type="button" variant="ghost" size="sm" disabled={locked || keyState.status === "loading"} onClick={refreshKeys} className="h-7 text-sm">{guide.refreshKeys}</Button></div><Select value={selectedKey?.id ?? ""} disabled={locked || !keysReady || !keyState.keys.length} onValueChange={(apiKeyId) => { clearResult(); store.setState({ apiKeyId }); }}><SelectTrigger id={fieldId("realtime-api-key")} className="h-10 w-full min-w-0 bg-white text-sm"><SelectValue placeholder={guide.chooseKey} /></SelectTrigger><SelectContent>{keyState.keys.map((key) => <SelectItem key={key.id} value={key.id}><span className="block min-w-0 truncate" title={[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}>{[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}</span></SelectItem>)}</SelectContent></Select><p className="text-sm leading-5 text-muted-foreground">{guide.apiKeyHelp}</p>{(keyState.apiId !== apiId || keyState.status === "loading") && <p role="status" className="text-sm text-muted-foreground">{guide.loadingKeys}</p>}{keyState.apiId === apiId && keyState.status === "error" && <p role="alert" className="text-sm text-destructive">{text.errors.ApiKeyAccessUnavailable}</p>}{keysReady && !keyState.keys.length && <p role="status" className="text-sm leading-6 text-muted-foreground">{guide.noKeys} <Link href={`/${locale}/api-keys`} className="text-primary underline underline-offset-4">{guide.manageKeys}</Link></p>}</div>

      {voiceOptions.length > 0 && <div className="space-y-2"><label htmlFor={fieldId("realtime-voice")} className="text-sm font-medium">{rt.voice}</label><Select value={state.input.voice ?? model.voice} disabled={locked || voiceOptions.length < 2} onValueChange={(voice) => { if (voiceOptions.some((option) => option.id === voice)) { updateInput({ voice }); } }}><SelectTrigger id={fieldId("realtime-voice")} className="h-10 w-full bg-white text-sm" aria-describedby={fieldId("realtime-voice-help")}><SelectValue /></SelectTrigger><SelectContent>{voiceOptions.map((voice) => <SelectItem key={voice.id} value={voice.id}>{voice.label[locale]}</SelectItem>)}</SelectContent></Select><p id={fieldId("realtime-voice-help")} className="text-sm leading-6 text-muted-foreground">{voiceOptions.length > 1 ? rt.voiceHelp : rt.fixedVoice.replace("{voice}", voiceOptions[0].label[locale])}</p></div>}
      {model.maxOutputTokens === null ? <p className="text-sm leading-6 text-muted-foreground">{model.family === "transcription" ? rt.transcriptionTokens : rt.liveTokens}</p> : <div className="space-y-2"><label htmlFor={fieldId("realtime-output-tokens")} className="text-sm font-medium">{rt.maxOutputTokens}</label><Input id={fieldId("realtime-output-tokens")} type="number" min={1} max={maximumOutputTokens} step={1} value={typeof state.input.maxOutputTokens === "number" && Number.isFinite(state.input.maxOutputTokens) ? state.input.maxOutputTokens : ""} aria-describedby={fieldId("realtime-token-range")} disabled={locked || state.input.maxOutputTokens === "model-max"} onChange={(event) => { updateInput({ maxOutputTokens: event.target.valueAsNumber }); }} className="h-10 max-w-60 bg-white text-sm" /><p id={fieldId("realtime-token-range")} className="text-sm text-muted-foreground">{rt.tokenRange.replace("{maximum}", number.format(maximumOutputTokens))}</p><p className="text-sm leading-5 text-muted-foreground">{rt.tokenHelp}</p>{model.supportsMaximumOutput && <div className="space-y-1"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={state.input.maxOutputTokens === "model-max"} disabled={locked} onChange={(event) => { updateInput({ maxOutputTokens: event.target.checked ? "model-max" : maximumOutputTokens }); }} />{rt.modelMaximum.replace("{count}", number.format(model.maxOutputTokens))}</label><p className="text-sm leading-5 text-muted-foreground">{rt.modelMaximumHelp}</p></div>}</div>}
      <p className="text-sm leading-6 text-muted-foreground">{rt.permission}</p><p className="text-sm leading-6 text-muted-foreground">{model.transport === "webrtc" ? rt.mediaNotice : rt.wsNotice}</p><p className="text-sm leading-6 text-muted-foreground">{rt.limit.replace("{minutes}", number.format(Math.floor(state.maximumDurationMs / 60_000)))}</p>
      {!valid && <p role="alert" className="text-sm text-destructive">{text.errors.InvalidRequest}</p>}
    </form>;
  const conversationActions = <div role="group" aria-label={rt.conversation} className="flex min-w-0 max-w-full flex-wrap items-center gap-2"><Button type="submit" form={fieldId("realtime-settings")} size="sm" className="h-auto min-h-8 min-w-0 max-w-full whitespace-normal text-left" disabled={locked || !valid || !selectedKey || !gatewayOrigin}>{state.connecting ? <LoaderCircle className="size-3.5 animate-spin" /> : <Mic className="size-3.5" />}{state.connecting ? rt.starting : rt.start}</Button><Button type="button" size="sm" variant="outline" className="h-auto min-h-8 min-w-0 max-w-full whitespace-normal text-left" disabled={!busy || state.stopping} onClick={stop}>{state.stopping ? <LoaderCircle className="size-3.5 animate-spin" /> : <Square className="size-3.5" />}{state.stopping ? rt.stopping : rt.stop}</Button></div>;
  const requestContent = <div className="space-y-6"><section className="space-y-3"><h3 className="text-lg font-semibold">{rt.guideTitle}</h3><ol className="list-decimal space-y-2 pl-5 text-sm leading-7 text-muted-foreground"><li>{rt.backend}</li><li>{model.transport === "webrtc" ? rt.mediaNotice : rt.wsNotice}</li><li>{rt.guideConversation}</li></ol><p className="text-sm leading-7 text-muted-foreground">{rt.guideTry}</p></section><div className="rounded-lg border border-border px-4 py-3"><span className="mr-3 text-sm font-bold text-primary">{model.transport === "websocket" || model.family === "voice-live" ? "WSS" : "POST"}</span><code className="break-all text-sm">{getRealtimeEndpoint(model, origin)}</code></div>{!gatewayOrigin && <p className="text-sm text-amber-700">{guide.notConfigured}</p>}<div className="flex justify-end"><Select value={state.language} onValueChange={(language) => { if (language === "body" || INTEGRATION_LANGUAGES.includes(language as IntegrationLanguage)) store.setState({ language: language as ConversationState["language"] }); }}><SelectTrigger aria-label={rt.language} className="w-48 bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="body">{rt.body}</SelectItem>{INTEGRATION_LANGUAGES.map((language) => <SelectItem key={language} value={language}>{languageNames[language]}</SelectItem>)}</SelectContent></Select></div><p className="text-sm leading-6 text-muted-foreground">{rt.backend}</p><RealtimeCode key={code} code={code} title={state.language === "body" ? rt.body : languageNames[state.language]} dictionary={dictionary} /><div className="rounded-xl border border-border bg-white p-4"><h4 className="mb-2 text-sm font-semibold">{rt.protocol}</h4><p className="whitespace-pre-wrap text-sm leading-7 text-muted-foreground">{getRealtimeProtocolGuide(model, locale)}</p></div><div className="flex flex-wrap gap-3 text-sm text-muted-foreground"><span>{rt.source}:</span><a href={documentation.href} target="_blank" rel="noreferrer noopener" className="text-primary underline underline-offset-4">{documentation.label}</a></div></div>;
  const schemaContent = <div className="space-y-6"><p className="text-sm leading-6 text-muted-foreground">{rt.schema}</p><ApiSchemaReference locale={locale} schema={getRealtimeRequestSchema(model)} title={rt.requestSchema} /><ApiSchemaReference locale={locale} schema={getRealtimeResponseSchema(model)} title={rt.responseSchema} /></div>;
  const responseContent = <div className="min-w-0 space-y-5">{(state.connecting || state.connected || state.stopping) && <div className="space-y-3 rounded-xl border border-primary/20 bg-primary/4 p-4"><p role="status" className="flex items-center gap-2 text-sm font-medium text-primary">{state.connected ? <Mic className="size-4" /> : <LoaderCircle className="size-4 animate-spin" />}{state.stopping ? rt.stopping : state.connected ? state.muted ? rt.muted : rt.connected : rt.starting}</p><div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground"><span>{rt.elapsed}: {number.format(state.elapsedSeconds)} s</span><span>{rt.events}: {number.format(state.eventCount)}</span>{state.connected && <Button type="button" size="sm" variant="outline" onClick={mute}>{state.muted ? <MicOff className="size-3.5" /> : <Mic className="size-3.5" />}{state.muted ? rt.unmute : rt.mute}</Button>}{state.connected && !["live", "transcription"].includes(model.family) && <Button type="button" size="sm" variant="outline" onClick={() => conversationRef.current?.cancelResponse()}><Square className="size-3.5" />{rt.interrupt}</Button>}</div></div>}
        {state.playbackBlocked && <div role="status" className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3"><p className="text-sm leading-6 text-amber-900">{rt.playbackBlocked}</p><Button type="button" size="sm" variant="outline" onClick={() => void conversationRef.current?.play()}><Volume2 className="size-3.5" />{rt.play}</Button></div>}
        {state.localFailure && <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/4 p-4 text-sm leading-6 text-destructive">{rt[state.localFailure]}</p>}{state.failure && !state.localFailure && <div role="alert" className="space-y-2 rounded-xl border border-destructive/20 bg-destructive/4 p-4 text-sm leading-6 text-destructive"><p>{state.failure.error.code === "InvalidRequest" ? rt.InvalidRequest : text.errors[state.failure.error.code]}</p>{state.failure.requestId && <p className="break-all">{text.requestId}: {state.failure.requestId}</p>}</div>}{state.turnFailed && <p role="status" className="text-sm leading-6 text-amber-800">{rt.turnFailed}</p>}
        <div className="min-w-0 space-y-4 rounded-xl border border-border bg-white p-4"><h4 className="text-sm font-semibold">{rt.conversation}</h4>{visibleTurns.length ? model.family === "transcription" ? <div className="max-h-[32rem] min-w-0 overflow-auto"><p className="whitespace-pre-wrap text-sm leading-7 [overflow-wrap:anywhere]">{transcriptionParagraph}{visibleTurns.some((turn) => !turn.final) && <span className="ml-2 text-muted-foreground">{state.connected || state.connecting || state.stopping ? rt.partial : rt.partialEnded}</span>}</p></div> : <div className="max-h-[32rem] min-w-0 space-y-3 overflow-auto">{visibleTurns.map((turn) => <div key={turn.id} className={`min-w-0 rounded-lg p-3 ${turn.role === "input" ? "ml-6 bg-primary/6" : "mr-6 bg-muted/50"}`}><div className="mb-1 flex flex-wrap items-center gap-2 text-sm font-semibold text-muted-foreground"><span>{turn.role === "input" ? rt.user : rt.assistant}</span>{!turn.final && <span className="font-normal">{state.connected || state.connecting || state.stopping ? rt.partial : rt.partialEnded}</span>}</div><p className="whitespace-pre-wrap text-sm leading-7 [overflow-wrap:anywhere]">{turn.text}</p></div>)}</div> : <p className="text-sm leading-7 text-muted-foreground">{rt.noConversation}</p>}<p className="text-sm leading-6 text-muted-foreground">{rt.transcriptNotice}</p></div>
        {state.complete && <div className={`space-y-2 rounded-xl border p-4 ${state.complete.stopStatus === "unconfirmed" ? "border-amber-200 bg-amber-50" : "border-border bg-muted/30"}`}><h4 className="text-sm font-semibold">{rt.ended} · {stopLabels[state.complete.stopStatus]}</h4><p className="text-sm leading-6 text-muted-foreground">{rt.stopHelp}</p></div>}{state.canRetryStop && <div className="space-y-2"><Button type="button" variant="outline" size="sm" disabled={state.retrying} onClick={() => void retryStop()}>{state.retrying && <LoaderCircle className="size-3.5 animate-spin" />}{rt.retryStop}</Button>{state.retryFailed && <p role="alert" className="text-sm text-amber-800">{rt.retryFailed}</p>}</div>}
        {Object.keys(state.usage).length > 0 && <div className="min-w-0 rounded-xl border border-border bg-white p-4"><h4 className="mb-2 text-sm font-semibold">{rt.usage}</h4><p className="mb-3 text-sm leading-6 text-muted-foreground">{rt.usageHelp}</p><dl className="grid min-w-0 gap-y-2 font-mono text-sm leading-6">{Object.entries(state.usage).map(([key, value]) => <div key={key} className="flex min-w-0 items-start justify-between gap-4"><dt className="min-w-0 flex-1 break-all">{key}</dt><dd className="max-w-[40%] shrink-0 break-all text-right tabular-nums">{number.format(value)}</dd></div>)}</dl></div>}
        {state.timeline.length > 0 && <details className="rounded-xl border border-border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">{rt.timeline} · {number.format(state.eventCount)}</summary><p className="my-3 text-sm leading-6 text-muted-foreground">{rt.timelineHelp}</p><div className="max-h-64 overflow-auto font-mono text-sm">{state.timeline.map((entry, index) => <div key={index} className="flex justify-between gap-3 border-b border-border/60 py-1.5"><span className="break-all">{entry.eventType}</span><span className="shrink-0 text-muted-foreground">{(entry.elapsedMs / 1000).toFixed(1)} s</span></div>)}</div></details>}
        <p className="text-sm leading-6 text-muted-foreground">{rt.privacy}</p></div>;
  return <ApiDocumentationLayout locale={locale} value={state.tab} onValueChange={(tab) => store.setState({ tab })} busy={busy || state.retrying} selection={selection} settings={settings} request={requestContent} schema={schemaContent} response={responseContent} responseActions={conversationActions} />;
}

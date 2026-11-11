"use client";

import { inspectSpeechUpload, MAX_TRANSCRIPTION_FILE_BYTES, TRANSCRIPTION_FILE_ACCEPT } from "@/lib/speech-upload";

import { useFieldIds } from "@/lib/use-field-ids";

import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { Check, Copy, Download, LoaderCircle, Play, Square } from "lucide-react";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import { usePortalStore } from "@/components/portal-store-provider";
import { useModelSelection } from "@/components/use-model-selection";
import { restoreModelSettings } from "@/lib/model-settings";
import { getSpeechDictionary } from "@/lib/i18n/speech";
import { loadModelKeyChoices, type ModelKeyChoice } from "@/lib/model-key-choices";
import { INTEGRATION_LANGUAGES, type IntegrationLanguage } from "@/lib/model-integration";
import { ModelResponseError } from "@/lib/model-response";
import { MAX_SPEECH_UPLOAD_BYTES, MAX_SPEECH_TEXT_CHARACTERS, speechInputSchema, type SpeechInput, type SpeechResult } from "@/lib/speech-request";
import { SAMPLE_SPEECH_TEXT, SPEECH_SAMPLE_PATH, SPEECH_VOICES, SPEECH_RATES, buildSpeechRequest, defaultSpeechInput, getSpeechEndpoint, getSpeechHeaders, getSpeechIntegrationExample, getSpeechRequestSchema, getSpeechResponseSchema } from "@/lib/speech-integration";
import { readSpeechResponse, speechAudioBytes } from "@/lib/speech-response";
import { buildSpeechExecutionRequest } from "@/lib/speech-client";
import { AZURE_SPEECH_TTS_VOICES } from "@/lib/speech-voices";
import type { SpeechModel } from "@/lib/model-types";
import type { TextGenerationFailure } from "@/lib/model-request";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** 画面内の選択値と実行結果です。購読キーの秘密値は保持しません。 */
type SpeechPanelState = {
  input: SpeechInput;
  apiKeyId: string;
  language: IntegrationLanguage | "body";
  tab: "request" | "schema" | "response";
  loading: boolean;
  result: SpeechResult | null;
  audioUrl: string | null;
  failure: TextGenerationFailure | null;
  cancelled: boolean;
  inputFile: File | null;
  inputAudioUrl: string | null;
  fileChecking: boolean;
  fileError: boolean;
};

const languageNames: Record<IntegrationLanguage, string> = { typescript: "TypeScript", python: "Python", php: "PHP", csharp: "C#", curl: "cURL" };

/** 読み取り専用のコードを表示し、コピーの成功と失敗を伝えます。 */
function SpeechCode({ code, title, dictionary }: { code: string; title: string; dictionary: Dictionary }) {
  const [copied, setCopied] = useState<"idle" | "complete" | "failed">("idle");
  /** 表示したコードのみをコピーし、環境変数に秘密値を補完しません。 */
  async function copy() { try { await navigator.clipboard.writeText(code); setCopied("complete"); } catch { setCopied("failed"); } }
  return <div className="overflow-hidden rounded-xl border border-border bg-slate-50">
    <div className="flex items-center justify-between gap-3 border-b border-border bg-white px-4 py-2"><span className="text-sm font-semibold text-muted-foreground">{title}</span><Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 text-sm" onClick={copy}>{copied === "complete" ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copied === "complete" ? dictionary.modelGateway.copied : dictionary.modelGateway.integration.copyCode}</Button></div>
    <pre className="overflow-x-auto p-5 font-mono text-sm leading-7" aria-label={title}><code>{code}</code></pre>
    {copied === "failed" && <p role="status" className="px-4 pb-3 text-sm text-destructive">{dictionary.modelGateway.copyFailed}</p>}
  </div>;
}

/** 検証済みのWAVだけをBlobへ変換し、外部URLへの自動アクセスを避けます。 */
function createAudioUrl(result: Extract<SpeechResult, { kind: "tts" }>): string {
  const bytes = new Uint8Array(speechAudioBytes(result.audioBase64));
  if (bytes.length !== result.byteLength) throw new Error("Invalid WAV length");
  return URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
}

/** 所有する音声だけを停止し、DOMとBlob URLの両方を解放します。 */
function releaseSpeechAudio(audio: HTMLAudioElement | null, url: string) {
  if (audio?.getAttribute("src") === url) { audio.pause(); audio.removeAttribute("src"); audio.load(); }
  URL.revokeObjectURL(url);
}

/** 入力文と選択した音声ファイルで、音声APIを比較する組み込みガイドです。 */
export function SpeechPanel({ models, dictionary, locale, gatewayOrigin }: { models: SpeechModel[]; dictionary: Dictionary; locale: Locale; gatewayOrigin: string | null }) {
  const fieldId = useFieldIds();
  const { scope, initialModel, rememberModel } = useModelSelection("speech", models);
  const savedSettings = usePortalStore((state) => state.modelSettings.speech[scope]), rememberSettings = usePortalStore((state) => state.setModelSettings);
  const text = dictionary.modelGateway;
  const guide = text.integration;
  const speech = getSpeechDictionary(locale);
  const drafts = usePortalStore((state) => state.modelDrafts), updateDrafts = usePortalStore((state) => state.updateModelDrafts);
  const [store] = useState(() => createStore<SpeechPanelState>(() => ({ input: { ...restoreModelSettings("speech", initialModel ? defaultSpeechInput(initialModel) : { modelId: "", voice: "ja-JP-NanamiNeural" as const, rate: "0%" as const, text: SAMPLE_SPEECH_TEXT }, savedSettings, (input) => { if (initialModel) buildSpeechRequest(initialModel, input); }), ...(drafts.speechText !== null ? { text: drafts.speechText } : {}) }, apiKeyId: "", language: "body", tab: "request", loading: false, result: null, audioUrl: null, failure: null, cancelled: false, inputFile: drafts.speechFile, inputAudioUrl: null, fileChecking: false, fileError: false })));
  const state = useStore(store);
  const requestRef = useRef<AbortController | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const outputAudioRef = useRef<HTMLAudioElement | null>(null), inputAudioRef = useRef<HTMLAudioElement | null>(null);
  const inputAudioUrlRef = useRef<string | null>(null), fileSelectionRef = useRef(0);
  const [keyState, setKeyState] = useState<{ apiId: string; status: "loading" | "ready" | "error"; keys: ModelKeyChoice[] }>({ apiId: "", status: "loading", keys: [] });
  const [refresh, setRefresh] = useState(0);
  const model = models.find((entry) => entry.id === state.input.modelId) ?? models[0];
  const apiId = model?.apiId ?? "";
  const keysReady = keyState.apiId === apiId && keyState.status === "ready";
  const selectedKey = keysReady ? keyState.keys.find((key) => key.id === state.apiKeyId) : undefined;
  const parsed = speechInputSchema.safeParse(state.input);
  const origin = gatewayOrigin ?? "https://gateway.example.com";
  const number = new Intl.NumberFormat(locale);
  const maximumUploadBytes = Math.min(model?.providerId === "openai" ? MAX_TRANSCRIPTION_FILE_BYTES : MAX_SPEECH_UPLOAD_BYTES, model?.maxRequestBytes ?? MAX_SPEECH_UPLOAD_BYTES);
  const fileTooLarge = Boolean(state.inputFile && state.inputFile.size > maximumUploadBytes);
  const inputReady = model?.kind === "tts" || Boolean(state.inputFile && !state.fileChecking && !state.fileError && !fileTooLarge);
  let payload: ReturnType<typeof buildSpeechRequest> | null = null;
  try { if (model && parsed.success) payload = buildSpeechRequest(model, parsed.data, state.inputFile?.name); } catch { /* 許可外の設定では送信せず、固定エラーを表示します。 */ }

  useLayoutEffect(() => () => {
    const pending = requestRef.current; requestRef.current = null; pending?.abort(); fileSelectionRef.current++;
    store.setState({ loading: false, fileChecking: false, ...(pending ? { cancelled: true } : {}) });
  }, [state.tab, store]);
  useLayoutEffect(() => {
    const url = state.audioUrl, audio = outputAudioRef.current;
    if (!url || state.tab !== "response") return;
    return () => {
      if (audioUrlRef.current !== url) return;
      audioUrlRef.current = null; releaseSpeechAudio(audio, url); store.setState({ audioUrl: null });
    };
  }, [state.tab, state.audioUrl, store]);
  useLayoutEffect(() => {
    const url = state.inputAudioUrl, audio = inputAudioRef.current;
    if (!url || state.tab !== "response") return;
    return () => {
      if (inputAudioUrlRef.current !== url) return;
      inputAudioUrlRef.current = null; releaseSpeechAudio(audio, url); store.setState({ inputAudioUrl: null });
    };
  }, [state.tab, model?.kind, state.inputAudioUrl, store]);
  useEffect(() => {
    if (state.tab !== "response") return;
    const current = store.getState();
    if (current.result?.kind === "tts" && !audioUrlRef.current) {
      const url = createAudioUrl(current.result); audioUrlRef.current = url; store.setState({ audioUrl: url });
    }
    if (model?.kind === "stt" && current.inputFile && !inputAudioUrlRef.current) {
      const url = URL.createObjectURL(current.inputFile); inputAudioUrlRef.current = url; store.setState({ inputAudioUrl: url });
    }
  }, [state.tab, model?.kind, state.result, state.audioUrl, state.inputFile, state.inputAudioUrl, store]);
  useEffect(() => {
    if (!apiId) return;
    const controller = new AbortController();
    void loadModelKeyChoices(apiId, controller.signal).then((keys) => { if (!controller.signal.aborted) setKeyState({ apiId, status: "ready", keys }); }).catch(() => { if (!controller.signal.aborted) setKeyState({ apiId, status: "error", keys: [] }); });
    return () => controller.abort();
  }, [apiId, refresh]);

  /** 以前の音声URLを解放し、設定と実行結果が食い違う表示を残しません。 */
  function clearResult() {
    const url = audioUrlRef.current; audioUrlRef.current = null;
    if (url) releaseSpeechAudio(outputAudioRef.current, url);
    store.setState({ result: null, audioUrl: null, failure: null, cancelled: false });
  }
  /** 選択内容を同じ状態へ保存し、組み込み例と実送信を揃えます。 */
  function updateInput(patch: Partial<SpeechInput>) { if (patch.text !== undefined) updateDrafts({ speechText: patch.text }); const input = { ...store.getState().input, ...patch }; clearResult(); rememberSettings("speech", scope, input); store.setState({ input }); }
  /** 現在の所属で使えるキーを読み直し、古い結果を消します。 */
  function refreshKeys() { clearResult(); setKeyState({ apiId, status: "loading", keys: [] }); setRefresh((value) => value + 1); }

  /** ファイル名だけで判断せず、有限サイズのWAV本文を検査してプレビューに使います。 */
  async function selectAudio(file: File | undefined) {
    clearResult(); const selection = ++fileSelectionRef.current;
    updateDrafts({ speechFile: null });
    const previousUrl = inputAudioUrlRef.current; inputAudioUrlRef.current = null;
    if (previousUrl) releaseSpeechAudio(inputAudioRef.current, previousUrl);
    store.setState({ inputFile: null, inputAudioUrl: null, fileError: false, fileChecking: Boolean(file) });
    if (!file) return;
    try {
      if (!file.size || file.size > maximumUploadBytes) throw new Error("Invalid audio size");
      if (!model) throw new Error("Missing model");
      inspectSpeechUpload(new Uint8Array(await file.arrayBuffer()), model);
      if (selection !== fileSelectionRef.current) return;
      const url = URL.createObjectURL(file); inputAudioUrlRef.current = url; updateDrafts({ speechFile: file }); store.setState({ inputFile: file, inputAudioUrl: url, fileChecking: false });
    } catch { if (selection === fileSelectionRef.current) store.setState({ fileChecking: false, fileError: true }); }
  }

  /** 入力文または選択ファイルをBFF経由で送り、古い要求の結果で上書きしません。 */
  async function execute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (requestRef.current || !model || !payload || !parsed.success || !selectedKey || !gatewayOrigin || !inputReady) return;
    clearResult();
    const controller = new AbortController();
    requestRef.current = controller;
    store.setState({ loading: true, tab: "response" });
    try {
      const signal = controller.signal;
      const response = await fetch("/api/models/speech", { ...buildSpeechExecutionRequest(model, parsed.data, selectedKey.id, state.inputFile), signal });
      const result = await readSpeechResponse(response, signal);
      if (controller.signal.aborted || requestRef.current !== controller) return;
      if (result.kind !== model.kind) throw new Error("Unexpected speech operation");
      const audioUrl = result.kind === "tts" ? createAudioUrl(result) : null;
      audioUrlRef.current = audioUrl;
      store.setState({ result, audioUrl });
    } catch (error) {
      if (!controller.signal.aborted && requestRef.current === controller) store.setState({ failure: error instanceof ModelResponseError ? error.failure : { error: { code: error instanceof DOMException && error.name === "TimeoutError" ? "GatewayTimeout" : "GenerationFailed" } } });
    } finally { if (requestRef.current === controller) { requestRef.current = null; store.setState({ loading: false }); } }
  }
  /** 表示中の要求を中止し、結果が遅れて届いても採用しません。 */
  function cancel() {
    requestRef.current?.abort(); requestRef.current = null; clearResult();
    const url = inputAudioUrlRef.current; inputAudioUrlRef.current = null;
    if (url) releaseSpeechAudio(inputAudioRef.current, url);
    store.setState({ loading: false, cancelled: true, inputAudioUrl: null });
  }

  if (!model) return null;
  const requestCode = payload ? state.language === "body" ? typeof payload === "string" ? payload : JSON.stringify(payload, null, 2) : getSpeechIntegrationExample(state.language, model, state.input, origin, state.inputFile?.name) : text.errors.InvalidRequest;
  const bodyTitle = model.kind === "tts" ? speech.ssml : speech.binary;
  const selectedVoice = AZURE_SPEECH_TTS_VOICES.find((voice) => voice.id === state.input.voice);
  const rateLabels = { "-20%": speech.slower, "0%": speech.normal, "+20%": speech.faster };
  const runningText = model.kind === "tts" ? speech.runningTts : speech.runningStt;
  const result = state.result;

  const selection = <div className="space-y-5">
    <p className="text-sm leading-7 text-muted-foreground">{speech.introduction}</p>
    <div className="max-w-xl"><div className="space-y-2"><label htmlFor={fieldId("speech-operation")} className="text-sm font-medium">{speech.operation}</label><Select value={model.id} disabled={state.loading || state.fileChecking} onValueChange={(id) => { const next = models.find((entry) => entry.id === id); if (next) { rememberModel({ id: next.id }); updateInput({ ...defaultSpeechInput(next), text: state.input.text }); } }}><SelectTrigger id={fieldId("speech-operation")} className="h-10 w-full bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent>{models.map((entry) => <SelectItem key={entry.id} value={entry.id}>{entry.kind === "tts" ? speech.tts : speech.stt}{entry.modelId ? ` · ${entry.modelId}` : ""}</SelectItem>)}</SelectContent></Select></div></div>
  </div>;
  const settings = <form onSubmit={execute} className="space-y-5">
      <div className="space-y-2 rounded-lg border border-border bg-white p-3">
        <div className="flex items-center justify-between gap-3"><label htmlFor={fieldId("speech-api-key")} className="text-sm font-medium">{guide.apiKey}</label><Button type="button" variant="ghost" size="sm" disabled={state.loading || keyState.status === "loading"} onClick={refreshKeys} className="h-7 text-sm">{guide.refreshKeys}</Button></div>
        <Select value={selectedKey?.id ?? ""} disabled={state.loading || !keysReady || !keyState.keys.length} onValueChange={(apiKeyId) => { clearResult(); store.setState({ apiKeyId }); }}><SelectTrigger id={fieldId("speech-api-key")} aria-describedby={fieldId("speech-key-help")} className="h-10 w-full min-w-0 bg-white text-sm"><SelectValue placeholder={guide.chooseKey} /></SelectTrigger><SelectContent>{keyState.keys.map((key) => <SelectItem key={key.id} value={key.id}><span className="block min-w-0 truncate" title={[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}>{[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}</span></SelectItem>)}</SelectContent></Select>
        <p id={fieldId("speech-key-help")} className="text-sm leading-5 text-muted-foreground">{guide.apiKeyHelp}</p>
        {(keyState.apiId !== apiId || keyState.status === "loading") && <p role="status" className="text-sm text-muted-foreground">{guide.loadingKeys}</p>}
        {keyState.apiId === apiId && keyState.status === "error" && <p role="alert" className="text-sm text-destructive">{text.errors.ApiKeyAccessUnavailable}</p>}
        {keysReady && !keyState.keys.length && <p role="status" className="text-sm leading-6 text-muted-foreground">{guide.noKeys} <Link href={`/${locale}/api-keys`} className="text-primary underline underline-offset-4">{guide.manageKeys}</Link></p>}
      </div>

      {model.kind === "tts" ? <>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2"><label htmlFor={fieldId("speech-voice")} className="text-sm font-medium">{speech.voice}</label><Select value={state.input.voice} disabled={state.loading} onValueChange={(voice) => { if (SPEECH_VOICES.includes(voice as SpeechInput["voice"])) updateInput({ voice: voice as SpeechInput["voice"] }); }}><SelectTrigger id={fieldId("speech-voice")} aria-describedby={fieldId("speech-voice-help")} className="h-10 w-full bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent>{AZURE_SPEECH_TTS_VOICES.map((voice) => <SelectItem key={voice.id} value={voice.id}>{voice.label[locale]}</SelectItem>)}</SelectContent></Select></div>
          <div className="space-y-2"><label htmlFor={fieldId("speech-rate")} className="text-sm font-medium">{speech.rate}</label><Select value={state.input.rate} disabled={state.loading} onValueChange={(rate) => { if (SPEECH_RATES.includes(rate as SpeechInput["rate"])) updateInput({ rate: rate as SpeechInput["rate"] }); }}><SelectTrigger id={fieldId("speech-rate")} className="h-10 w-full bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent>{SPEECH_RATES.map((rate) => <SelectItem key={rate} value={rate}>{rateLabels[rate]}</SelectItem>)}</SelectContent></Select></div>
        </div>
        <p id={fieldId("speech-voice-help")} className="text-sm leading-6 text-muted-foreground">{speech.voiceHelp} <a href="https://learn.microsoft.com/azure/ai-services/speech-service/language-support?tabs=tts#text-to-speech" target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4">{speech.voiceListDocs}</a></p>
        <p className="text-sm leading-6 text-muted-foreground">{speech.formatNotice}</p>
        <div className="space-y-2"><label htmlFor={fieldId("speech-text")} className="block text-sm font-medium">{speech.text}</label><Textarea id={fieldId("speech-text")} rows={4} required disabled={state.loading} maxLength={MAX_SPEECH_TEXT_CHARACTERS} value={state.input.text} placeholder={SAMPLE_SPEECH_TEXT} onChange={(event) => updateInput({ text: event.target.value })} className="bg-white text-sm leading-7" /></div>
        <p className="text-sm leading-6 text-muted-foreground">{speech.fixedNotice}</p>
      </> : <>
        <div className="rounded-lg border border-border bg-white px-3 py-3"><p className="mb-1 text-sm font-semibold text-muted-foreground">{speech.inputLanguage}</p><p className="text-sm">{speech.japanese}</p><p className="mt-2 text-sm leading-6 text-muted-foreground">{model.providerId === "openai" ? speech.openaiLanguageNotice : speech.languageNotice}</p></div>
        <div className="space-y-3 rounded-lg border border-border bg-white p-3"><label htmlFor={fieldId("speech-file")} className="block text-sm font-medium">{speech.file}</label><Input id={fieldId("speech-file")} type="file" accept={model.providerId === "openai" ? TRANSCRIPTION_FILE_ACCEPT : ".wav,audio/wav,audio/x-wav"} disabled={state.loading || state.fileChecking} onChange={(event) => void selectAudio(event.target.files?.[0])} className="bg-white" /><p className="text-sm leading-6 text-muted-foreground">{(model.providerId === "openai" ? speech.transcriptionFileHelp : speech.fileHelp).replace("{size}", number.format(maximumUploadBytes / 1_048_576))}</p>{state.fileChecking && <p role="status" className="text-sm text-muted-foreground">{speech.fileChecking}</p>}{(state.fileError || fileTooLarge) && <p role="alert" className="text-sm text-destructive">{speech.fileInvalid}</p>}{state.inputFile && <p className="break-all text-sm text-muted-foreground">{state.inputFile.name} · {number.format(state.inputFile.size)} B</p>}{state.inputAudioUrl && <audio ref={inputAudioRef} controls preload="none" src={state.inputAudioUrl} aria-label={speech.file} className="w-full" />}<a href={SPEECH_SAMPLE_PATH} download="speech-ja-jp.wav" className="inline-flex items-center gap-1.5 text-sm font-medium text-primary underline underline-offset-4"><Download className="size-3.5" />{speech.sampleDownload}</a><p className="text-sm leading-6 text-muted-foreground">{speech.sampleNotice}</p></div>
      </>}
      <p className="text-sm leading-6 text-muted-foreground">{speech.wait}</p>
      <div className="flex justify-end gap-2">{state.loading && <Button type="button" size="sm" variant="outline" onClick={cancel}><Square className="size-3.5" />{text.cancel}</Button>}<Button type="submit" size="sm" disabled={state.loading || !payload || !selectedKey || !gatewayOrigin || !inputReady}>{state.loading ? <LoaderCircle className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}{state.loading ? runningText : model.kind === "tts" ? speech.runTts : speech.runStt}</Button></div>
      {!payload && <p role="alert" className="text-sm text-destructive">{text.errors.InvalidRequest}</p>}
    </form>;
  const requestContent = <div className="space-y-6">
    <section className="space-y-3"><h3 className="text-lg font-semibold">{speech.guideTitle}</h3><ol className="list-decimal space-y-2 pl-5 text-sm leading-7 text-muted-foreground"><li>{speech.guideSelect}</li><li>{speech.backend}</li><li>{model.kind === "tts" ? speech.guideTts : speech.guideStt}</li></ol><p className="text-sm leading-7 text-muted-foreground">{speech.guideTry}</p></section>
    <div className="rounded-lg border border-border px-4 py-3"><span className="mr-3 text-sm font-bold text-primary">POST</span><code className="break-all text-sm">{getSpeechEndpoint(model, origin)}</code></div>
        {!gatewayOrigin && <p className="text-sm text-amber-700">{guide.notConfigured}</p>}
        <div className="flex justify-end"><Select value={state.language} onValueChange={(language) => { if (language === "body" || INTEGRATION_LANGUAGES.includes(language as IntegrationLanguage)) store.setState({ language: language as SpeechPanelState["language"] }); }}><SelectTrigger aria-label={speech.integrationLanguage} className="w-48 bg-white text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="body">{bodyTitle}</SelectItem>{INTEGRATION_LANGUAGES.map((language) => <SelectItem key={language} value={language}>{languageNames[language]}</SelectItem>)}</SelectContent></Select></div>
        {state.language !== "body" && <p className="text-sm leading-6 text-muted-foreground">{speech.backend}</p>}
        {state.language === "body" && model.kind === "stt" && <p className="text-sm leading-6 text-muted-foreground">{speech.binaryNotice}</p>}
        {model.providerId === "openai" && <p className="text-sm leading-6 text-muted-foreground">{speech.multipartNotice}</p>}
        <SpeechCode key={requestCode} code={requestCode} title={state.language === "body" ? bodyTitle : languageNames[state.language]} dictionary={dictionary} />
        {state.language === "body" && <SpeechCode code={JSON.stringify(getSpeechHeaders(model), null, 2)} title={speech.headers} dictionary={dictionary} />}
    <div className="flex flex-wrap gap-3 text-sm text-muted-foreground"><span>{speech.sources}:</span>{model.providerId === "openai" ? <a href="https://learn.microsoft.com/en-us/azure/ai-services/openai/whisper-quickstart" target="_blank" rel="noreferrer noopener" className="text-primary underline underline-offset-4">{speech.openaiDocs}</a> : <><a href="https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech" target="_blank" rel="noreferrer noopener" className="text-primary underline underline-offset-4">{speech.ttsDocs}</a><a href="https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text-short" target="_blank" rel="noreferrer noopener" className="text-primary underline underline-offset-4">{speech.sttDocs}</a></>}</div>
  </div>;
  const schemaContent = <div className="space-y-6"><p className="text-sm leading-6 text-muted-foreground">{speech.schema}</p><ApiSchemaReference locale={locale} schema={getSpeechRequestSchema(model)} title={speech.requestSchema} /><ApiSchemaReference locale={locale} schema={getSpeechResponseSchema(model)} title={speech.responseSchema} /></div>;
  const responseContent = <div className="space-y-5" aria-busy={state.loading}><p className="text-sm leading-6 text-muted-foreground">{guide.requestNotice}</p>
        {state.failure && <div role="alert" className="space-y-2 rounded-xl border border-destructive/20 bg-destructive/4 p-4 text-sm leading-6 text-destructive"><p>{text.errors[state.failure.error.code]}</p>{state.failure.retryAfterSeconds !== undefined && <p>{text.retryAfter.replace("{count}", number.format(state.failure.retryAfterSeconds))}</p>}{state.failure.requestId && <p className="break-all">{text.requestId}: {state.failure.requestId}</p>}</div>}
        {state.loading && <p role="status" className="flex items-center gap-2 text-sm text-primary"><LoaderCircle className="size-4 animate-spin" />{runningText}</p>}
        {result ? <>
          <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm text-muted-foreground"><span>{result.model}</span><span>{speech.duration}: {(result.durationMs / 1000).toFixed(1)} s</span>{result.kind === "tts" && <><span>{speech.voice}: {selectedVoice?.label[locale]}</span><span>{speech.audioDuration}: {(result.audioDurationMs / 1000).toFixed(1)} s</span><span>{speech.bytes}: {number.format(result.byteLength)} B</span></>}</div>
          {result.kind === "tts" ? <>
            <div className="space-y-3 rounded-xl border border-border bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-3"><h4 className="text-sm font-semibold">{speech.generatedAudio}</h4>{state.audioUrl && <a href={state.audioUrl} download="egakumi-speech-sample.wav" className="inline-flex items-center gap-1.5 text-sm font-medium text-primary underline underline-offset-4"><Download className="size-3.5" />{speech.download}</a>}</div>{state.audioUrl && <audio ref={outputAudioRef} controls preload="none" src={state.audioUrl} aria-label={speech.generatedAudio} aria-describedby={fieldId("speech-result-transcript")} className="w-full" />}<p id={fieldId("speech-result-transcript")} className="text-sm leading-7">{state.input.text}</p><p className="text-sm leading-6 text-muted-foreground">{speech.noAutoplay}</p></div>
            <p className="font-mono text-sm text-muted-foreground">{speech.binaryResponse}</p>
            <SpeechCode code={JSON.stringify(result.rawResponse, null, 2)} title={speech.audioMetadata} dictionary={dictionary} />
          </> : <>
            <div className="rounded-xl border border-border bg-white p-4"><h4 className="mb-2 text-sm font-semibold text-muted-foreground">{speech.transcript}</h4><p className="whitespace-pre-wrap text-sm leading-8">{result.transcript || speech.noTranscript}</p></div>
            <SpeechCode code={JSON.stringify(result.rawResponse, null, 2)} title={speech.rawResponse} dictionary={dictionary} />
          </>}
          {result.requestId && <p className="break-all font-mono text-sm text-muted-foreground">{text.requestId}: {result.requestId}</p>}
        </> : !state.loading && !state.failure && <p role="status" className="rounded-xl border border-dashed border-border p-6 text-sm leading-6 text-muted-foreground">{state.cancelled ? text.cancelNotice : speech.responseEmpty}</p>}</div>;
  return <ApiDocumentationLayout locale={locale} value={state.tab} onValueChange={(tab) => store.setState({ tab })} busy={state.loading} selection={selection} settings={settings} request={requestContent} schema={schemaContent} response={responseContent} />;
}

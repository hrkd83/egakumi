"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { ArrowUpRight, Check, CheckCircle2, Code2, Copy, LoaderCircle, Play, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import type { GraphToolDictionary } from "@/lib/i18n/graph-tool";
import type { Locale } from "@/lib/i18n/config";
import { GRAPH_TOOL_PATH, GRAPH_TOOL_PROFILE_FIELDS, graphToolProfileValue, type GraphToolKey } from "@/lib/graph-tool-contracts";
import { checkGraphToolConnection, executeGraphTool, GraphToolResponseError, loadGraphToolKeys } from "./graph-tool-client";
import { canResumeGraphTool, createGraphToolStore, type GraphToolViewState } from "./graph-tool-store";
import { createGraphToolPoller } from "./graph-tool-poller";
import { getGraphToolExample, graphToolAuthenticationContract, graphToolRequestContract, graphToolSuccessContract } from "./graph-tool-examples";

/** 秘密値を含まない実装例・公開応答を、コピー可能なコード枠で表示します。 */
function CodeBlock({ title, code, text }: { title: string; code: string; text: GraphToolDictionary }) {
  const [copy, setCopy] = useState<"idle" | "complete" | "failed">("idle");
  /** 表示中のコードだけをコピーし、失敗も画面上で案内します。 */
  async function copyCode() {
    try { await navigator.clipboard.writeText(code); setCopy("complete"); }
    catch { setCopy("failed"); }
  }
  return <div className="overflow-hidden rounded-xl border border-border bg-[#f8faf9]">
    <div className="flex items-center justify-between gap-3 border-b border-border bg-white px-4 py-2"><h4 className="text-xs font-semibold">{title}</h4><Button type="button" variant="ghost" size="sm" className="h-7 text-xs" onClick={copyCode}>{copy === "complete" ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copy === "complete" ? text.copied : text.copy}</Button></div>
    <pre className="whitespace-pre-wrap break-words p-5 font-mono text-[14px] leading-7" aria-label={title}><code>{code}</code></pre>
    {copy === "failed" && <p role="status" className="px-4 pb-3 text-xs text-destructive">{text.copyFailed}</p>}
  </div>;
}

/** 各アプリで一般的な名前に合わせた、組み込み例の表示名です。 */
const languages = { curl: "cURL", nextjs: "Next.js / TypeScript", fastapi: "FastAPI / Python", php: "PHP", dify: "Dify" };

/**
 * Graphを最初に呼び出し、必要な本人認証とプロフィール応答までを確認します。
 * @remarks 秘密情報はBFFが保持します。画面を閉じると通信を中止し、ポーリングでセッション期限を延長しません。
 */
export function GraphToolPanel({ text, locale, gatewayOrigin }: { text: GraphToolDictionary; locale: Locale; gatewayOrigin: string | null }) {
  const [store] = useState(createGraphToolStore);
  const state = useStore(store);
  const [view, setView] = useState<"request" | "schema" | "response">("request");
  const [keys, setKeys] = useState<{ status: "loading" | "ready" | "error"; items: GraphToolKey[] }>({ status: "loading", items: [] });
  const [keyRefresh, setKeyRefresh] = useState(0);
  const request = useRef<AbortController | null>(null);
  const pollNow = useRef<(() => void) | null>(null);
  const selectedKey = keys.status === "ready" ? keys.items.find((key) => key.id === state.apiKeyId) : undefined;
  const running = state.phase === "running";

  useEffect(() => () => request.current?.abort(), []);

  useEffect(() => {
    if (view !== "response") return;
    const controller = new AbortController();
    void loadGraphToolKeys(controller.signal).then((items) => {
      if (controller.signal.aborted) return;
      setKeys({ status: "ready", items });
      const previous = store.getState().apiKeyId;
      if (previous && !items.some((key) => key.id === previous)) store.getState().selectKey("");
    }).catch(() => { if (!controller.signal.aborted) setKeys({ status: "error", items: [] }); });
    return () => controller.abort();
  }, [keyRefresh, store, view]);

  /** APIへの通常要求は1回だけ送り、認証完了後の読み取りだけを一度再開します。 */
  const execute = useCallback(async (resuming = false) => {
    if (request.current) return;
    const apiKeyId = store.getState().apiKeyId;
    if (!apiKeyId) return;
    const controller = new AbortController();
    request.current = controller;
    const revision = store.getState().begin();
    try {
      const result = await executeGraphTool(apiKeyId, gatewayOrigin, controller.signal);
      if (controller.signal.aborted) return;
      if (resuming && result.status === "need_login") throw new GraphToolResponseError({ error: { code: "GraphReauthenticationRequired" } });
      store.getState().receive(revision, result, Date.now());
    } catch (error) {
      if (!controller.signal.aborted) store.getState().fail(revision, error instanceof GraphToolResponseError ? error.failure : { error: { code: "GatewayUnavailable" } });
    } finally { if (request.current === controller) request.current = null; }
  }, [gatewayOrigin, store]);

  useEffect(() => {
    if (state.phase !== "waiting" || state.deadline === null) return;
    const timer = setTimeout(() => store.getState().expire(state.revision), Math.max(0, state.deadline - Date.now()));
    return () => clearTimeout(timer);
  }, [state.phase, state.deadline, state.revision, store]);

  useEffect(() => {
    if (state.phase !== "waiting" || state.deadline === null || state.pollFailed) return;
    const poller = createGraphToolPoller({
      deadline: state.deadline,
      poll: (signal) => checkGraphToolConnection(state.apiKeyId, signal),
      visible: () => document.visibilityState === "visible",
      onComplete: () => { if (canResumeGraphTool(store.getState(), state.revision, state.apiKeyId)) return execute(true); },
      onExpired: () => store.getState().expire(state.revision),
      onFailure: (error) => {
        if (error instanceof GraphToolResponseError && error.failure.error.code === "ConnectionIdentityMismatch") store.getState().fail(state.revision, error.failure);
        else store.getState().markPollFailed(state.revision, true);
      },
    });
    pollNow.current = poller.requestNow;
    document.addEventListener("visibilitychange", poller.requestNow);
    poller.requestNow();
    return () => { poller.stop(); if (pollNow.current === poller.requestNow) pollNow.current = null; document.removeEventListener("visibilitychange", poller.requestNow); };
  }, [state.phase, state.deadline, state.apiKeyId, state.revision, state.pollFailed, execute, store]);

  /** キー変更時は進行中の旧要求を取り消し、以前の認証URL・応答を破棄します。 */
  function selectKey(id: string) {
    request.current?.abort(); request.current = null;
    store.getState().selectKey(id);
  }

  /** 権限を再確認する間は古い候補で実行しないよう、一覧を読み込み状態にします。 */
  function refreshKeys() { setKeys({ status: "loading", items: [] }); setKeyRefresh((value) => value + 1); }

  const result = state.result;
  const settings = <div className="space-y-5">
    <section className="space-y-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3"><label htmlFor="graph-api-key" className="text-xs font-medium">{text.key}</label><Button variant="ghost" size="sm" disabled={running || state.phase === "waiting" || keys.status === "loading"} className="h-7 text-xs" onClick={refreshKeys}>{text.refreshKeys}</Button></div>
        <Select value={selectedKey?.id ?? ""} disabled={running || keys.status !== "ready" || !keys.items.length} onValueChange={selectKey}>
          <SelectTrigger id="graph-api-key" className="h-10 w-full min-w-0 bg-white text-xs" aria-describedby="graph-api-key-help"><SelectValue placeholder={text.chooseKey} className="min-w-0 flex-1 overflow-hidden text-left" /></SelectTrigger>
          <SelectContent>{keys.items.map((key) => <SelectItem key={key.id} value={key.id}><span className="block min-w-0 truncate text-left" title={[key.displayName, key.companyName, key.groupName].filter(Boolean).join(" · ")}>{key.displayName}<span className="text-muted-foreground"> · {[key.companyName, key.groupName].filter(Boolean).join(" · ")}</span></span></SelectItem>)}</SelectContent>
        </Select>
        <p id="graph-api-key-help" className="text-xs leading-6 text-muted-foreground">{text.keyHelp}</p>
        {keys.status === "loading" && <p role="status" className="text-xs text-muted-foreground">{text.loadingKeys}</p>}
        {keys.status === "error" && <p role="alert" className="text-xs text-destructive">{text.errors.ApiKeyAccessUnavailable}</p>}
        {keys.status === "ready" && !keys.items.length && <p role="status" className="text-xs leading-6 text-muted-foreground">{text.noKeys} <Link href={`/${locale}/api-keys`} className="font-medium text-primary underline underline-offset-4">{text.manageKeys}</Link></p>}
      </div>
      <div className="space-y-2 rounded-lg border border-border bg-white p-3"><h3 className="flex items-center gap-2 text-xs font-semibold"><Code2 className="size-4 text-primary" />{text.fixedRequest}</h3><p className="break-all font-mono text-xs leading-6"><span className="mr-2 font-semibold text-primary">GET</span>{gatewayOrigin ?? "https://gateway.example.com"}{GRAPH_TOOL_PATH}</p><p className="text-xs leading-6 text-muted-foreground">{text.fixedNote}</p></div>
      <div className="flex flex-wrap items-center justify-between gap-3"><p className="max-w-xl text-xs leading-6 text-muted-foreground">{text.loginNote}</p><Button type="button" size="sm" disabled={!selectedKey || running || state.phase === "waiting"} onClick={() => { void execute(); }}>{running ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}{running ? text.running : text.execute}</Button></div>
    </section>

    {state.phase === "waiting" && state.loginUrl && <section className="space-y-4 rounded-xl border border-blue-200 bg-blue-50/60 p-5" aria-live="polite">
      <p className="flex gap-2 text-sm font-medium text-blue-950"><ShieldCheck className="mt-0.5 size-4 shrink-0" />{text.waiting}</p>
      <div className="flex flex-wrap items-center gap-3"><Button asChild size="sm"><a href={state.loginUrl} target="_blank" rel="noopener noreferrer">{text.login}<ArrowUpRight className="size-4" /></a></Button><Button variant="outline" size="sm" type="button" onClick={() => { if (state.pollFailed) store.getState().markPollFailed(state.revision, false); else pollNow.current?.(); }}>{text.check}</Button></div>
      {state.pollFailed && <p role="status" className="text-xs leading-6 text-amber-800">{text.timedOut}</p>}
    </section>}
    {state.phase === "expired" && <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-7 text-amber-900">{text.expired}</p>}
    {state.failure && <div role="alert" className="space-y-2 rounded-xl border border-destructive/20 bg-destructive/4 p-4 text-sm leading-7 text-destructive"><p>{text.errors[state.failure.error.code]}</p>{state.failure.requestId && <p className="break-all font-mono text-xs">{text.requestId}: {state.failure.requestId}</p>}</div>}

  </div>;
  const response = <div className="space-y-4" aria-busy={running}>
        {result ? <><h3 className="flex items-center gap-2 text-sm font-semibold text-primary"><CheckCircle2 className="size-4" />{text.completed}</h3>
          <dl className="grid gap-x-6 gap-y-5 rounded-xl border border-border bg-white p-5 sm:grid-cols-2">{GRAPH_TOOL_PROFILE_FIELDS.map((field) => <div key={field} className="min-w-0"><dt className="text-xs text-muted-foreground">{text.profileFields[field]}<code className="mt-1 block text-xs">{field}</code></dt><dd className={`mt-2 break-words text-sm font-medium ${field === "id" ? "font-mono" : ""}`}>{graphToolProfileValue(result.profile, field, text)}</dd></div>)}</dl>
          <p className="text-xs leading-6 text-muted-foreground">{text.attributesNote}</p>
          <p className="text-xs text-muted-foreground">HTTP 200 · {text.duration}: {(result.durationMs / 1_000).toFixed(2)} s</p>
          <CodeBlock code={JSON.stringify(result.profile, null, 2)} title="application/json" text={text} />
          {result.requestId && <p className="break-all font-mono text-xs text-muted-foreground">{text.requestId}: {result.requestId}</p>}
          {result.sessionUpdatePending && <p role="status" className="rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-900">{text.sessionUpdatePending}</p>}
        </> : <p className="flex min-h-36 items-center justify-center gap-2 rounded-xl border border-dashed border-border p-5 text-center text-sm leading-7 text-muted-foreground">{running && <LoaderCircle className="size-4 shrink-0 animate-spin" />}{running ? text.running : text.noResponse}</p>}
      </div>;
  const guide = <div className="space-y-6">
        <section className="space-y-3"><h2 className="text-base font-semibold">{text.fixedRequest}</h2><div className="rounded-xl border border-border bg-muted/20 px-5 py-4 font-mono text-sm leading-7"><span className="mr-3 font-semibold text-primary">GET</span><code className="break-all">{gatewayOrigin ?? "https://gateway.example.com"}{GRAPH_TOOL_PATH}</code></div><p className="text-sm leading-7 text-muted-foreground">{text.fixedNote}</p><CodeBlock title={text.upstreamSelect} code={`GET https://graph.microsoft.com/v1.0${graphToolRequestContract.upstream}`} text={text} /><p className="text-sm leading-7 text-muted-foreground">{text.attributesNote}</p><p className="text-sm leading-7 text-muted-foreground">{text.permissionsNote} <a href="https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0" target="_blank" rel="noopener noreferrer" className="font-medium text-primary underline underline-offset-4">{text.permissionsReference}</a></p></section>
        <p className="text-sm leading-7">{text.guideIntro}</p><ol className="list-decimal space-y-2 pl-5 text-sm leading-7 text-muted-foreground">{text.steps.map((step) => <li key={step}>{step}</li>)}</ol>
        <div className="space-y-2 rounded-xl border border-blue-100 bg-blue-50/50 p-4 text-xs leading-7 text-blue-950"><p>{text.lifetime}</p><p>{text.rotation}</p></div>
        <div className="flex items-center justify-between gap-3"><label htmlFor="graph-language" className="text-xs font-semibold">{text.languages}</label><Select value={state.language} onValueChange={(value) => { if (value in languages) state.setLanguage(value as GraphToolViewState["language"]); }}><SelectTrigger id="graph-language" className="w-52 bg-white text-xs"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(languages).map(([id, name]) => <SelectItem key={id} value={id}>{name}</SelectItem>)}</SelectContent></Select></div>
        <p className="text-xs leading-7 text-muted-foreground">{state.language === "curl" ? text.curlNote : state.language === "dify" ? text.difyNote : text.boundary}</p><CodeBlock key={state.language} code={getGraphToolExample(state.language, gatewayOrigin)} title={languages[state.language]} text={text} />
      </div>;
  const requestSchema = {
    type: "object", required: ["method", "path", "headers"], properties: {
      method: { type: "string", const: graphToolRequestContract.method }, path: { type: "string", const: graphToolRequestContract.path },
      headers: { type: "object", description: text.requestHeaders, required: ["api-key"], properties: {
        "api-key": { type: "string", description: text.subscriptionHeader },
        "X-Core-Connection-Session": { type: "string", description: text.sessionHeader },
      } }, body: { type: "null", description: text.noRequestBody },
    },
  };
  const authenticationSchema = {
    type: "object", required: ["error", "authentication"], properties: {
      error: { type: "object", description: text.connectionRequired, required: ["code"], properties: { code: { type: "string", const: graphToolAuthenticationContract.error.code } } },
      authentication: { type: "object", description: text.authenticationData, required: Object.keys(graphToolAuthenticationContract.authentication), properties: {
        protocolVersion: { type: "integer", const: 2 }, status: { type: "string", const: "need_login" },
        loginUrl: { type: "string", format: "uri" }, provisionalConnectionSession: { type: "string" },
        transactionId: { type: "string" }, pollSecret: { type: "string" },
        statusUrl: { type: "string", format: "uri" }, expiresIn: { type: "integer", minimum: 1 },
      } },
    },
  };
  const successSchema = { ...graphToolSuccessContract, properties: Object.fromEntries(Object.entries(graphToolSuccessContract.properties).map(([field, definition]) => [field, field in text.profileFields ? { ...definition, description: text.profileFields[field as keyof typeof text.profileFields] } : definition])) };
  const schema = <div className="space-y-6"><p className="text-sm leading-7 text-muted-foreground">{text.secretNotice}</p><ApiSchemaReference locale={locale} title={text.requestSchema} schema={requestSchema} /><ApiSchemaReference locale={locale} title={text.successSchema} schema={successSchema} /><p className="text-sm leading-7 text-muted-foreground">{text.attributesNote}</p><ApiSchemaReference locale={locale} title={text.authenticationSchema} schema={authenticationSchema} /></div>;
  return <ApiDocumentationLayout locale={locale} value={view} onValueChange={setView} settings={settings} request={guide} schema={schema} response={response} busy={running || state.phase === "waiting"} />;
}

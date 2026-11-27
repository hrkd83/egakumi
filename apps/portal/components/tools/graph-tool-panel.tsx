"use client";

import { useFieldIds } from "@/lib/use-field-ids";
import { deactivatePanelRequest } from "@/lib/panel-request-lifecycle";

import { useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { Check, CheckCircle2, Code2, Copy, LoaderCircle, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiDocumentationLayout, ApiSchemaReference } from "@/components/api-documentation-layout";
import { TryAuthenticationControl, useTryAuthentication } from "@/components/try-authentication";
import { startPortalApiAuthorization } from "@/lib/api-auth/browser";
import type { GraphToolDictionary } from "@/lib/i18n/graph-tool";
import type { Locale } from "@/lib/i18n/config";
import { GRAPH_TOOL_PATH, GRAPH_TOOL_PROFILE_FIELDS, graphToolProfileValue } from "@/lib/graph-tool-contracts";
import { executeGraphTool, GraphToolResponseError } from "./graph-tool-client";
import { createGraphToolStore, type GraphToolViewState } from "./graph-tool-store";
import { getGraphToolExample, graphToolRequestContract, graphToolSuccessContract } from "./graph-tool-examples";

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
 * @remarks 秘密情報はBFFが保持します。画面を閉じるか本人のAPI接続が失効すると、通信を中止します。
 */
export function GraphToolPanel({ text, locale, gatewayOrigin }: { text: GraphToolDictionary; locale: Locale; gatewayOrigin: string | null }) {
  const formId = useFieldIds();
  const [store] = useState(createGraphToolStore);
  const state = useStore(store);
  const [view, setView] = useState<"request" | "schema" | "response">("request");
  const [reauthenticating, setReauthenticating] = useState(false);
  const [reauthenticationFailed, setReauthenticationFailed] = useState(false);
  const request = useRef<AbortController | null>(null);
  const authentication = useTryAuthentication();
  const authState = authentication.store;
  const running = state.phase === "running";

  useEffect(() => () => deactivatePanelRequest(request, (pending) => { if (pending) store.getState().reset(); }), [store]);

  useEffect(() => authState.subscribe((current, previous) => {
    if (previous.status === "connected" && current.status !== "connected") {
      deactivatePanelRequest(request, () => store.getState().reset());
    }
  }), [authState, store]);

  /** Portalの本人Bearerを使って、Graphの固定APIを試用します。 */
  async function execute() {
    const credential = authentication.credential;
    if (request.current || !credential || !gatewayOrigin) return;
    setReauthenticationFailed(false);
    const controller = new AbortController();
    request.current = controller;
    store.getState().begin();
    setView("response");
    try {
      const result = await executeGraphTool(credential, controller.signal);
      if (!controller.signal.aborted) store.getState().receive(result);
    } catch (error) {
      if (!controller.signal.aborted) store.getState().fail(error instanceof GraphToolResponseError ? error.failure : { error: { code: "GatewayUnavailable" } });
    } finally { if (request.current === controller) request.current = null; }
  }

  /** Graphが要求した追加条件で、試用のEntra認証だけを取り直します。 */
  async function reauthenticateGraph(claims: string) {
    setReauthenticating(true);
    setReauthenticationFailed(false);
    try { await startPortalApiAuthorization(window.location.pathname, claims); }
    catch { setReauthenticationFailed(true); }
    finally { setReauthenticating(false); }
  }

  const claims = state.failure?.claims;
  const result = state.result;
  const settings = <div className="space-y-5">
    <section className="space-y-5">
      <TryAuthenticationControl model={authentication} locale={locale} />
      <div className="space-y-2 rounded-lg border border-border bg-white p-3"><h3 className="flex items-center gap-2 text-xs font-semibold"><Code2 className="size-4 text-primary" />{text.fixedRequest}</h3><p className="break-all font-mono text-xs leading-6"><span className="mr-2 font-semibold text-primary">GET</span>{gatewayOrigin ?? "https://gateway.example.com"}{GRAPH_TOOL_PATH}</p><p className="text-xs leading-6 text-muted-foreground">{text.fixedNote}</p></div>
      <div className="flex flex-wrap items-center justify-between gap-3"><p className="max-w-xl text-xs leading-6 text-muted-foreground">{text.loginNote}</p><Button type="button" size="sm" disabled={!authentication.credential || !gatewayOrigin || running || reauthenticating} onClick={() => { void execute(); }}>{running ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}{running ? text.running : text.execute}</Button></div>
    </section>

    {state.failure && <div role="alert" className="space-y-2 rounded-xl border border-destructive/20 bg-destructive/4 p-4 text-sm leading-7 text-destructive"><p>{text.errors[state.failure.error.code]}</p>{state.failure.requestId && <p className="break-all font-mono text-xs">{text.requestId}: {state.failure.requestId}</p>}
      {claims && <Button size="sm" variant="outline" type="button" disabled={reauthenticating}
        onClick={() => { void reauthenticateGraph(claims); }}>{text.reauthenticate}</Button>}
      {reauthenticationFailed && <p>{text.errors.ApiAuthenticationUnavailable}</p>}</div>}

  </div>;
  const response = <div className="space-y-4" aria-busy={running}>
        {result ? <><h3 className="flex items-center gap-2 text-sm font-semibold text-primary"><CheckCircle2 className="size-4" />{text.completed}</h3>
          <dl className="grid gap-x-6 gap-y-5 rounded-xl border border-border bg-white p-5 sm:grid-cols-2">{GRAPH_TOOL_PROFILE_FIELDS.map((field) => <div key={field} className="min-w-0"><dt className="text-xs text-muted-foreground">{text.profileFields[field]}<code className="mt-1 block text-xs">{field}</code></dt><dd className={`mt-2 break-words text-sm font-medium ${field === "id" ? "font-mono" : ""}`}>{graphToolProfileValue(result.profile, field, text)}</dd></div>)}</dl>
          <p className="text-xs leading-6 text-muted-foreground">{text.attributesNote}</p>
          <p className="text-xs text-muted-foreground">HTTP 200 · {text.duration}: {(result.durationMs / 1_000).toFixed(2)} s</p>
          <CodeBlock code={JSON.stringify(result.profile, null, 2)} title="application/json" text={text} />
          {result.requestId && <p className="break-all font-mono text-xs text-muted-foreground">{text.requestId}: {result.requestId}</p>}
        </> : <p className="flex min-h-36 items-center justify-center gap-2 rounded-xl border border-dashed border-border p-5 text-center text-sm leading-7 text-muted-foreground">{running && <LoaderCircle className="size-4 shrink-0 animate-spin" />}{running ? text.running : text.noResponse}</p>}
      </div>;
  const guide = <div className="space-y-6">
        <section className="space-y-3"><h2 className="text-base font-semibold">{text.fixedRequest}</h2><div className="rounded-xl border border-border bg-muted/20 px-5 py-4 font-mono text-sm leading-7"><span className="mr-3 font-semibold text-primary">GET</span><code className="break-all">{gatewayOrigin ?? "https://gateway.example.com"}{GRAPH_TOOL_PATH}</code></div><p className="text-sm leading-7 text-muted-foreground">{text.fixedNote}</p><CodeBlock title={text.upstreamSelect} code={`GET https://graph.microsoft.com/v1.0${graphToolRequestContract.upstream}`} text={text} /><p className="text-sm leading-7 text-muted-foreground">{text.attributesNote}</p><p className="text-sm leading-7 text-muted-foreground">{text.permissionsNote} <a href="https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0" target="_blank" rel="noopener noreferrer" className="font-medium text-primary underline underline-offset-4">{text.permissionsReference}</a></p></section>
        <p className="text-sm leading-7">{text.guideIntro}</p><ol className="list-decimal space-y-2 pl-5 text-sm leading-7 text-muted-foreground">{text.steps.map((step) => <li key={step}>{step}</li>)}</ol>
        <div className="flex items-center justify-between gap-3"><label htmlFor={formId("graph-language")} className="text-xs font-semibold">{text.languages}</label><Select value={state.language} onValueChange={(value) => { if (value in languages) state.setLanguage(value as GraphToolViewState["language"]); }}><SelectTrigger id={formId("graph-language")} className="w-52 bg-white text-xs"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(languages).map(([id, name]) => <SelectItem key={id} value={id}>{name}</SelectItem>)}</SelectContent></Select></div>
        <p className="text-xs leading-7 text-muted-foreground">{state.language === "curl" ? text.curlNote : state.language === "dify" ? text.difyNote : text.boundary}</p><CodeBlock key={state.language} code={getGraphToolExample(state.language, gatewayOrigin)} title={languages[state.language]} text={text} />
      </div>;
  const requestSchema = {
    type: "object", required: ["method", "path", "headers"], properties: {
      method: { type: "string", const: graphToolRequestContract.method }, path: { type: "string", const: graphToolRequestContract.path },
      headers: { type: "object", description: text.requestHeaders, required: ["Authorization"], properties: {
        Authorization: { type: "string", description: "Bearer <GATEWAY_ACCESS_TOKEN>" },
      } }, body: { type: "null", description: text.noRequestBody },
    },
  };
  const successSchema = { ...graphToolSuccessContract, properties: Object.fromEntries(Object.entries(graphToolSuccessContract.properties).map(([field, definition]) => [field, field in text.profileFields ? { ...definition, description: text.profileFields[field as keyof typeof text.profileFields] } : definition])) };
  const schema = <div className="space-y-6"><p className="text-sm leading-7 text-muted-foreground">{text.secretNotice}</p><ApiSchemaReference locale={locale} title={text.requestSchema} schema={requestSchema} /><ApiSchemaReference locale={locale} title={text.successSchema} schema={successSchema} /><p className="text-sm leading-7 text-muted-foreground">{text.attributesNote}</p></div>;
  return <ApiDocumentationLayout locale={locale} value={view} onValueChange={setView} settings={settings} request={guide} schema={schema} response={response} busy={running} />;
}

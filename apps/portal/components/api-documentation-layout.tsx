"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { BookOpen, Braces, Play } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Locale } from "@/lib/i18n/config";

/** ガイドを入口とし、仕様の参照と実際の試行を分ける共通ドキュメント枠です。 */
export function ApiDocumentationLayout({ locale, value, onValueChange, selection, settings, request, schema, response, responseActions, busy = false, executable = true }: {
  locale: Locale;
  value: "request" | "schema" | "response";
  onValueChange: (value: "request" | "schema" | "response") => void;
  selection?: ReactNode;
  settings: ReactNode;
  request: ReactNode;
  schema: ReactNode;
  response: ReactNode;
  responseActions?: ReactNode;
  busy?: boolean;
  executable?: boolean;
}) {
  const ja = locale === "ja";
  return <div className="api-documentation space-y-7">
    {selection && <div className="rounded-xl border border-border bg-white p-5 sm:p-6">{selection}</div>}
    <Tabs value={value} onValueChange={(next) => { if (next === "request" || next === "schema" || next === "response") onValueChange(next); }} className="gap-7">
      <TabsList variant="line" className="h-auto w-full flex-wrap justify-start gap-x-2 gap-y-2 border-b border-border p-0 sm:gap-x-5">
        <TabsTrigger value="request" disabled={busy} className="h-12 gap-2 px-1 text-sm"><BookOpen className="size-4" />{ja ? "使い方ガイド" : "Guide"}</TabsTrigger>
        <TabsTrigger value="schema" disabled={busy} className="h-12 gap-2 px-1 text-sm"><Braces className="size-4" />{ja ? "API仕様" : "API reference"}</TabsTrigger>
        {executable && <TabsTrigger value="response" className="h-12 gap-2 px-1 text-sm"><Play className="size-4" />{ja ? "試す" : "Try it"}</TabsTrigger>}
      </TabsList>
      <TabsContent value="request" className="m-0 space-y-7">
        {executable && <ol className="grid gap-4 rounded-xl border border-border bg-white p-5 sm:grid-cols-3 sm:p-6">
          {[
            [ja ? "APIキーを用意" : "Prepare an API key", <span key="key"><Link href={`/${locale}/api-keys`} className="font-medium text-primary underline underline-offset-4">{ja ? "APIキー管理" : "API key management"}</Link>{ja ? "で、このAPIにアクセスできるキーを確認します。" : " lists the keys that grant access to this API."}</span>],
            [ja ? "アプリへ組み込む" : "Integrate with your app", ja ? "下のコード例を参考に、アプリのバックエンドから呼び出します。" : "Use the examples below to call the API from your app’s backend."],
            [ja ? "応答を確認" : "Check the response", ja ? "API仕様で返却形式を確認できます。「試す」では実際にリクエストできます。" : "Review response formats in the API reference, or send a real request in Try it."],
          ].map(([title, description], index) => <li key={index} className="flex gap-3"><span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary">{index + 1}</span><div><h2 className="font-semibold">{title}</h2><p className="mt-2 text-sm leading-7 text-muted-foreground">{description}</p></div></li>)}
        </ol>}
        <div className="min-w-0 space-y-6">{request}</div>
      </TabsContent>
      <TabsContent value="schema" className="m-0 min-w-0 space-y-6">{schema}</TabsContent>
      <TabsContent value="response" className="m-0">
        <div className="grid items-start gap-6 xl:grid-cols-[minmax(320px,380px)_minmax(0,1fr)]">
          <section className="min-w-0 rounded-xl border border-border bg-white p-5 sm:p-6"><h2 className="mb-5 text-lg font-semibold">{ja ? "リクエスト設定" : "Request settings"}</h2>{settings}</section>
          <section className="min-w-0 space-y-5 rounded-xl border border-border bg-white p-5 sm:p-6"><div className="flex min-w-0 flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">{ja ? "実行結果" : "Result"}</h2>{responseActions}</div>{response}</section>
        </div>
      </TabsContent>
    </Tabs>
  </div>;
}

/** JSON Schemaの主要フィールドを表で読み、完全な定義も必要に応じて展開できます。 */
export function ApiSchemaReference({ locale, title, schema }: { locale: Locale; title: string; schema: Record<string, unknown> }) {
  const ja = locale === "ja";
  const variants = Array.isArray(schema.oneOf) ? schema.oneOf : Array.isArray(schema.anyOf) ? schema.anyOf : [schema];
  return <section className="overflow-hidden rounded-xl border border-border bg-white">
    <h2 className="border-b border-border px-5 py-4 text-lg font-semibold">{title}</h2>
    {variants.map((variant, index) => {
      const definition = variant && typeof variant === "object" ? variant as Record<string, unknown> : {};
      const properties = definition.properties && typeof definition.properties === "object" ? definition.properties as Record<string, Record<string, unknown>> : {};
      const required = Array.isArray(definition.required) ? definition.required : [];
      return Object.keys(properties).length > 0 ? <div key={index} className="overflow-x-auto">
        {variants.length > 1 && <h3 className="px-5 pt-5 font-semibold">{typeof definition.title === "string" ? definition.title : `${ja ? "形式" : "Variant"} ${index + 1}`}</h3>}
        <table className="w-full text-left text-sm"><thead><tr className="border-b border-border bg-muted/50"><th className="px-5 py-3 font-semibold">{ja ? "フィールド" : "Field"}</th><th className="px-4 py-3 font-semibold">{ja ? "型 / 必須" : "Type / required"}</th><th className="px-5 py-3 font-semibold">{ja ? "説明・制約" : "Description / constraints"}</th></tr></thead><tbody>
          {Object.entries(properties).map(([name, field]) => <tr key={name} className="border-b border-border/70 last:border-0 align-top"><td className="px-5 py-4 font-mono font-medium text-foreground">{name}</td><td className="px-4 py-4"><code>{Array.isArray(field.type) ? field.type.join(" | ") : typeof field.type === "string" ? field.type : field.oneOf || field.anyOf ? "union" : "—"}</code>{required.includes(name) && <span className="mt-1 block text-xs font-medium text-primary">{ja ? "必須" : "Required"}</span>}</td><td className="max-w-xl px-5 py-4 leading-7 text-muted-foreground">{typeof field.description === "string" && <p>{field.description}</p>}{Array.isArray(field.enum) && <p className="break-words font-mono">{field.enum.map(String).join(" / ")}</p>}{["minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "format", "const"].filter((key) => field[key] !== undefined).map((key) => <span key={key} className="mr-3 inline-block font-mono">{key}: {String(field[key])}</span>)}</td></tr>)}
        </tbody></table>
      </div> : <p key={index} className="px-5 py-4 text-sm text-muted-foreground">{ja ? "詳細な構造と入れ子のフィールドは、以下のJSON Schemaで確認できます。" : "See the JSON Schema below for the full structure and nested fields."}</p>;
    })}
    <details className="border-t border-border"><summary className="cursor-pointer px-5 py-4 text-sm font-medium text-primary">{ja ? "完全なJSON Schemaを表示" : "View complete JSON Schema"}</summary><pre className="whitespace-pre-wrap break-words border-t border-border bg-muted/40 p-5 font-mono text-[14px] leading-7"><code>{JSON.stringify(schema, null, 2)}</code></pre></details>
  </section>;
}

"use client";

import { useEffect, useState } from "react";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { z } from "zod";
import { SubscriptionManagementConnection } from "@/components/subscription-management-connection";
import { Button } from "@/components/ui/button";
import { startPortalApiAuthorization } from "@/lib/api-auth/browser";
import type { TryCredential } from "@/lib/api-auth/try-credential";
import type { Locale } from "@/lib/i18n/config";

const statusResponse = z.object({ status: z.enum(["disconnected", "connected", "busy", "reauthentication_required"]) });
type ConnectionStatus = z.infer<typeof statusResponse>["status"] | "loading" | "disabled" | "unavailable";
type Selection = { mode: "api-key" | "bearer"; status: ConnectionStatus; error: boolean; submitting: boolean };

const messages = {
  ja: {
    label: "試用の認証方式", key: "APIキー", bearer: "アクセストークン", start: "APIに接続", renew: "再認証",
    disconnect: "API接続を解除", connected: "APIに接続済み", busy: "認証を更新しています",
    unavailable: "API接続状態を確認できません", failed: "API接続を完了できませんでした",
  },
  en: {
    label: "Try authentication", key: "API key", bearer: "Access token", start: "Connect to API", renew: "Reauthenticate",
    disconnect: "Disconnect API", connected: "Connected to API", busy: "Refreshing authentication",
    unavailable: "Couldn't check the API connection", failed: "Couldn't complete the API connection",
  },
} as const;

/** 各Try画面の選択と接続状態だけを保持し、Bearer値をブラウザーへ出しません。 */
export function useTryAuthentication(keyId: string | undefined) {
  const [store] = useState(() => createStore<Selection>(() => ({
    mode: "api-key", status: "loading", error: false, submitting: false,
  })));
  const selection = useStore(store);
  useEffect(() => {
    const cancellation = new AbortController();
    const outcome = new URL(window.location.href).searchParams.get("api-auth");
    if (outcome === "connected" || outcome === "failed") {
      store.setState({ mode: "bearer", error: outcome === "failed" });
    }
    async function refresh() {
      try {
        const response = await fetch("/api/api-auth/status", { cache: "no-store", signal: cancellation.signal });
        const status = response.status === 404 ? "disabled" :
          response.ok ? statusResponse.parse(await response.json()).status : "unavailable";
        if (!cancellation.signal.aborted) store.setState({ status });
      } catch {
        if (!cancellation.signal.aborted) store.setState({ status: "unavailable" });
      }
    }
    void refresh();
    const timer = window.setInterval(() => { if (store.getState().mode === "bearer") void refresh(); }, 5_000);
    return () => { cancellation.abort(); window.clearInterval(timer); };
  }, [store]);

  const credential: TryCredential | undefined = selection.mode === "api-key"
    ? keyId ? { apiKeyId: keyId } : undefined
    : selection.status === "connected" ? { authentication: "bearer" } : undefined;
  return { store, selection, credential };
}

/** 選択と追加認証・接続解除を提供します。既存のキー選択は呼出側に残します。 */
export function TryAuthenticationPicker({ model, locale, disabled = false, onModeChange }: {
  model: ReturnType<typeof useTryAuthentication>; locale: Locale; disabled?: boolean; onModeChange?: () => void;
}) {
  const { store, selection } = model;
  const labels = messages[locale];
  const keyConnection = selection.mode === "api-key" ? <SubscriptionManagementConnection locale={locale} disabled={disabled} /> : null;

  async function start() {
    store.setState({ submitting: true, error: false });
    try {
      await startPortalApiAuthorization(window.location.pathname);
    } catch { store.setState({ submitting: false, error: true }); }
  }

  async function disconnect() {
    store.setState({ submitting: true, error: false });
    try {
      const response = await fetch("/api/api-auth/disconnect", { method: "POST" });
      if (!response.ok) throw new Error();
      onModeChange?.();
      store.setState({ status: "disconnected" });
    } catch { store.setState({ error: true }); }
    finally { store.setState({ submitting: false }); }
  }

  function select(mode: Selection["mode"]) {
    if (mode === selection.mode) return;
    onModeChange?.();
    store.setState({ mode, error: false });
  }

  const updating = disabled || selection.submitting;
  return <>{selection.status !== "disabled" && selection.status !== "loading" && <fieldset className="mb-3 space-y-2">
    <legend className="text-sm font-medium">{labels.label}</legend>
    <div className="flex flex-wrap items-center gap-2">
      <Button type="button" size="sm" variant={selection.mode === "api-key" ? "default" : "outline"}
        disabled={updating} aria-pressed={selection.mode === "api-key"}
        onClick={() => select("api-key")}>{labels.key}</Button>
      <Button type="button" size="sm" variant={selection.mode === "bearer" ? "default" : "outline"}
        disabled={updating} aria-pressed={selection.mode === "bearer"}
        onClick={() => select("bearer")}>{labels.bearer}</Button>
    </div>
    {selection.mode === "bearer" && <div className="flex flex-wrap items-center gap-2">
      {selection.status === "connected" && <>
        <span role="status" className="text-sm text-muted-foreground">{labels.connected}</span>
        <Button type="button" variant="outline" size="sm" disabled={updating} onClick={disconnect}>{labels.disconnect}</Button>
        <Button type="button" variant="outline" size="sm" disabled={updating} onClick={start}>{labels.renew}</Button>
      </>}
      {selection.status === "busy" && <span role="status" className="text-sm text-muted-foreground">{labels.busy}</span>}
      {selection.status === "unavailable" && <span role="alert" className="text-sm text-muted-foreground">{labels.unavailable}</span>}
      {(selection.status === "disconnected" || selection.status === "reauthentication_required") &&
        <Button type="button" variant="outline" size="sm" disabled={updating} onClick={start}>
          {selection.status === "disconnected" ? labels.start : labels.renew}
        </Button>}
      {selection.error && <span role="alert" className="text-sm text-destructive">{labels.failed}</span>}
    </div>}
  </fieldset>}{keyConnection}</>;
}

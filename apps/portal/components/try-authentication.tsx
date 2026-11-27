"use client";

import { useEffect, useState } from "react";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { startPortalApiAuthorization } from "@/lib/api-auth/browser";
import type { TryCredential } from "@/lib/api-auth/try-credential";
import type { Locale } from "@/lib/i18n/config";

const statusResponse = z.object({ status: z.enum(["disconnected", "connected", "busy", "reauthentication_required"]) });
type ConnectionStatus = z.infer<typeof statusResponse>["status"] | "loading" | "disabled" | "unavailable";
type ConnectionState = { status: ConnectionStatus; error: boolean; submitting: boolean };

const messages = {
  ja: {
    label: "API試用の接続", start: "APIに接続", renew: "再認証",
    disconnect: "API接続を解除", connected: "APIに接続済み", busy: "認証を更新しています",
    disabled: "API試用は構成されていません", unavailable: "API接続状態を確認できません", failed: "API接続を完了できませんでした",
  },
  en: {
    label: "API trial connection", start: "Connect to API", renew: "Reauthenticate",
    disconnect: "Disconnect API", connected: "Connected to API", busy: "Refreshing authentication",
    disabled: "API trials are unavailable", unavailable: "Couldn't check the API connection", failed: "Couldn't complete the API connection",
  },
} as const;

/** 各Try画面の選択と接続状態だけを保持し、Bearer値をブラウザーへ出しません。 */
export function useTryAuthentication() {
  const [store] = useState(() => createStore<ConnectionState>(() => ({
    status: "loading", error: false, submitting: false,
  })));
  const selection = useStore(store);
  useEffect(() => {
    const cancellation = new AbortController();
    const outcome = new URL(window.location.href).searchParams.get("api-auth");
    if (outcome === "connected" || outcome === "failed") {
      store.setState({ error: outcome === "failed" });
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
    const timer = window.setInterval(refresh, 5_000);
    return () => { cancellation.abort(); window.clearInterval(timer); };
  }, [store]);

  const credential: TryCredential | undefined = selection.status === "connected" ? { authentication: "bearer" } : undefined;
  return { store, selection, credential };
}

/** 試用に必要なBearer接続の開始、追加認証、解除を提供します。 */
export function TryAuthenticationControl({ model, locale, disabled = false, onDisconnect }: {
  model: ReturnType<typeof useTryAuthentication>; locale: Locale; disabled?: boolean; onDisconnect?: () => void;
}) {
  const { store, selection } = model;
  const labels = messages[locale];

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
      onDisconnect?.();
      store.setState({ status: "disconnected" });
    } catch { store.setState({ error: true }); }
    finally { store.setState({ submitting: false }); }
  }

  const updating = disabled || selection.submitting;
  if (selection.status === "loading") return null;
  return <fieldset className="mb-3 space-y-2">
    <legend className="text-sm font-medium">{labels.label}</legend>
    <div className="flex flex-wrap items-center gap-2">
      {selection.status === "disabled" && <span role="status" className="text-sm text-muted-foreground">{labels.disabled}</span>}
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
    </div>
  </fieldset>;
}

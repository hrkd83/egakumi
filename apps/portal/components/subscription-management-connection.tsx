"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { startPortalApiAuthorization, subscriptionManagementRequiredEvent } from "@/lib/api-auth/browser";
import { apiAuthPurposeHeader, subscriptionManagementPurpose } from "@/lib/api-auth/purpose";
import type { Locale } from "@/lib/i18n/config";

const reply = z.object({ status: z.enum(["disconnected", "connected", "busy", "reauthentication_required"]) });
type ConnectionStatus = z.infer<typeof reply>["status"] | "loading" | "disabled" | "unavailable";
type Connection = { status: ConnectionStatus; opened: boolean; rejected: boolean; failed: boolean; submitting: boolean };

const messages = {
  ja: {
    connect: "APIキーの利用・管理を続ける",
    waiting: "APIキー用の認証を確認しています…",
    unavailable: "APIキー用の認証を確認できませんでした",
    failed: "APIキー用の認証を完了できませんでした",
    retry: "接続を確認し直す",
  },
  en: {
    connect: "Continue to use or manage API keys",
    waiting: "Checking sign-in for API keys…",
    unavailable: "Couldn't check sign-in for API keys",
    failed: "Couldn't complete sign-in for API keys",
    retry: "Check the connection again",
  },
};

/** 管理画面の初回読取りとTryのキー選択に、本人の追加認証への導線を提供します。 */
export function SubscriptionManagementConnection({ locale, children, disabled }: {
  locale: Locale; children?: ReactNode; disabled?: boolean;
}) {
  const [store] = useState(() => createStore<Connection>(() => ({
    status: "loading", opened: false, rejected: false, failed: false, submitting: false,
  })));
  const state = useStore(store);
  const labels = messages[locale];

  useEffect(() => {
    const cancellation = new AbortController();
    if (new URL(window.location.href).searchParams.get("subscription-auth") === "failed") {
      store.setState({ failed: true });
    }
    let checking = false;
    async function refresh() {
      if (checking || document.visibilityState !== "visible" || store.getState().status === "disabled" || store.getState().rejected) return;
      checking = true;
      try {
        const response = await fetch("/api/api-auth/status", {
          headers: { [apiAuthPurposeHeader]: subscriptionManagementPurpose }, cache: "no-store",
          signal: AbortSignal.any([cancellation.signal, AbortSignal.timeout(10_000)]),
        });
        const status = response.status === 404 ? "disabled" :
          response.ok ? reply.parse(await response.json()).status : "unavailable";
        if (!cancellation.signal.aborted) store.setState((previous) => ({
          status, opened: previous.opened || status === "connected" || status === "disabled",
        }));
      } catch {
        if (!cancellation.signal.aborted) store.setState({ status: "unavailable" });
      } finally {
        checking = false;
      }
    }
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 30_000);
    const reject = () => store.setState({ rejected: true });
    window.addEventListener("focus", refresh);
    window.addEventListener(subscriptionManagementRequiredEvent, reject);
    return () => {
      cancellation.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener(subscriptionManagementRequiredEvent, reject);
    };
  }, [store]);

  async function connect() {
    store.setState({ failed: false, submitting: true });
    try {
      await startPortalApiAuthorization(window.location.pathname, subscriptionManagementPurpose);
    } catch {
      store.setState({ failed: true, submitting: false });
    }
  }

  const waiting = state.status === "loading" || state.status === "busy";
  const needsAuthentication = state.rejected || state.status === "disconnected" || state.status === "reauthentication_required";
  return <>
    {(needsAuthentication || state.status === "unavailable" || state.failed || (waiting && Boolean(children) && !state.opened)) &&
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {needsAuthentication && <Button type="button" size="sm" variant="outline" disabled={disabled || state.submitting}
          onClick={connect}>{labels.connect}</Button>}
        {waiting && <span role="status" className="text-sm text-muted-foreground">{labels.waiting}</span>}
        {state.status === "unavailable" && <>
          <span role="alert" className="text-sm text-destructive">{labels.unavailable}</span>
          <Button type="button" size="sm" variant="outline" onClick={() => window.location.reload()}>{labels.retry}</Button>
        </>}
        {state.failed && <span role="alert" className="text-sm text-destructive">{labels.failed}</span>}
      </div>}
    {state.opened && children}
  </>;
}

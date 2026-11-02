"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { CircleAlert, LoaderCircle, RefreshCw, Search, ShieldCheck, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";
import { apiKeyErrorCode } from "../api-keys/client";
import { errorMessage } from "../api-keys/presentation";
import { loadApiKeyAdministration, mergeAdministrationPages } from "./client";
import { createAdministrationStore } from "./store";

/** 基盤管理者だけが、所有グループの削除・適格性と影響する購読数を確認します。 */
export function ApiKeyAdministrationDashboard({ dictionary, locale }: { dictionary: Dictionary; locale: Locale }) {
  const text = dictionary.apiKeyAdministration;
  const [store] = useState(createAdministrationStore);
  const state = useStore(store);
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);

  /** 取得に失敗した結果は空一覧や正常件数に置き換えず、再試行を案内します。 */
  const load = useCallback(async (refresh: boolean) => {
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    const request = ++sequence.current;
    store.setState({ loading: true, refreshing: refresh, loadingMore: false, error: null, overview: null });
    try {
      const overview = await loadApiKeyAdministration(refresh, current.signal);
      if (!current.signal.aborted && request === sequence.current) store.setState({ overview });
    } catch (error) {
      if (!current.signal.aborted && request === sequence.current) store.setState({ error: apiKeyErrorCode(error), overview: null });
    } finally {
      if (!current.signal.aborted && request === sequence.current) store.setState({ loading: false, refreshing: false });
    }
  }, [store]);

  /** 続きはグループID順の位置から読み、全体集計をページ内の件数で置き換えません。 */
  async function loadMore() {
    const before = store.getState();
    const cursor = before.overview?.nextCursor;
    if (!cursor || before.loading || before.loadingMore) return;
    const current = new AbortController(); controller.current = current;
    const request = ++sequence.current;
    store.setState({ loadingMore: true, error: null });
    try {
      const page = await loadApiKeyAdministration(false, current.signal, cursor);
      const previous = store.getState().overview;
      if (!current.signal.aborted && request === sequence.current && previous) store.setState({ overview: mergeAdministrationPages(previous, page) });
    } catch (error) {
      if (!current.signal.aborted && request === sequence.current) store.setState({ error: apiKeyErrorCode(error), overview: null });
    } finally {
      if (!current.signal.aborted && request === sequence.current) store.setState({ loadingMore: false });
    }
  }

  useEffect(() => { void load(false); return () => controller.current?.abort(); }, [load]);

  /** 未確認時刻を日付に変換せず、現在の表示言語で日時を読みやすく表示します。 */
  function date(value: string | null): string {
    return value ? new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : text.neverChecked;
  }

  const overview = state.overview;
  const query = state.query.trim().toLocaleLowerCase(locale);
  const groups = overview?.groups.filter((group) => (state.status === "all" || group.directoryStatus === state.status)
    && `${group.displayName} ${group.id}`.toLocaleLowerCase(locale).includes(query));

  return <div className="mx-auto max-w-[1400px]">
    <section className="mb-7 flex flex-wrap items-start justify-between gap-4"><div className="max-w-2xl"><p className="mb-3 text-xs font-bold tracking-[.2em] text-primary">PLATFORM ADMINISTRATION</p><h1 className="text-[30px] font-semibold tracking-tight">{text.title}</h1><p className="mt-3 text-[13px] leading-7 text-muted-foreground">{text.description}</p></div><Badge variant="outline" className="gap-2 rounded-full border-primary/15 bg-primary/4 px-3 py-1.5 text-xs text-primary"><ShieldCheck className="size-3.5" />{text.adminOnly}</Badge></section>
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3"><p className="max-w-2xl text-xs leading-6 text-muted-foreground">{text.refreshHelp}</p><Button variant="outline" disabled={state.loading || state.loadingMore} onClick={() => void load(true)}><RefreshCw className={state.refreshing ? "animate-spin" : ""} />{text.refresh}</Button></div>
    {state.loading ? <div role="status" className="flex min-h-64 items-center justify-center gap-3 rounded-2xl border border-border bg-white text-sm text-muted-foreground"><LoaderCircle className="size-5 animate-spin" />{state.refreshing ? text.refreshing : text.loading}</div>
      : state.error ? <div role="alert" className="space-y-4 rounded-2xl border border-amber-200 bg-amber-50 p-6"><CircleAlert className="size-6 text-amber-800" /><p className="text-sm leading-7 text-amber-950">{errorMessage(dictionary, state.error)}</p><Button variant="outline" onClick={() => void load(false)}>{text.retry}</Button></div>
        : overview && <>
          <div className="mb-6 grid gap-3 sm:grid-cols-3">{[
            { label: text.trackedGroups, value: overview.summary.totalGroups },
            { label: text.affectedSubscriptions, value: overview.summary.affectedSubscriptions },
            { label: text.unknownGroups, value: overview.summary.unknownGroups },
          ].map(({ label, value }) => <div key={label} className="rounded-xl border border-border bg-white p-5"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-2 text-2xl font-semibold">{value}</p></div>)}</div>
          {overview.syncErrors > 0 && <p role="status" className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-950">{text.syncErrors.replace("{count}", String(overview.syncErrors))}</p>}
          <p className="mb-3 text-xs leading-6 text-muted-foreground">{text.loadedCount.replace("{loaded}", String(overview.groups.length)).replace("{total}", String(overview.summary.totalGroups))}</p>
          <section className="overflow-hidden rounded-2xl border border-border bg-white"><div className="flex flex-wrap gap-3 border-b border-border p-4"><div className="relative min-w-48 flex-1"><Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input value={state.query} onChange={(event) => store.setState({ query: event.target.value })} placeholder={text.search} aria-label={text.search} className="pl-9 text-xs" /></div><Select value={state.status} onValueChange={(status) => store.setState({ status: status as typeof state.status })}><SelectTrigger className="w-40 text-xs" aria-label={text.status}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">{text.allStatuses}</SelectItem>{(["active", "deleted", "ineligible", "unknown"] as const).map((status) => <SelectItem key={status} value={status}>{text.statuses[status]}</SelectItem>)}</SelectContent></Select></div>
            {!overview.groups.length ? <div className="px-6 py-14 text-center"><Users className="mx-auto mb-4 size-8 text-muted-foreground/50" /><h2 className="text-sm font-semibold">{text.empty}</h2><p className="mt-2 text-xs leading-6 text-muted-foreground">{text.emptyHelp}</p></div> : !groups?.length ? <p role="status" className="px-6 py-14 text-center text-sm text-muted-foreground">{overview.nextCursor ? text.noLoadedMatches : text.noMatches}</p>
              : <div className="overflow-x-auto"><table className="w-full min-w-[800px] border-collapse text-left"><thead className="bg-muted/30 text-xs text-muted-foreground"><tr>{[text.group, text.status, text.lastChecked, text.activeSubscriptions, text.affectedSubscriptions].map((label) => <th key={label} scope="col" className="px-5 py-3 font-medium">{label}</th>)}</tr></thead><tbody className="divide-y divide-border">{groups.map((group) => <tr key={group.id}><th scope="row" className="px-5 py-5"><span className="block text-xs font-semibold">{group.displayName}</span><span className="mt-1.5 block break-all font-mono text-xs font-normal text-muted-foreground">{group.id}</span></th><td className="px-5 py-5"><Badge variant="outline" className={`text-xs ${group.directoryStatus === "active" ? "border-primary/15 bg-primary/5 text-primary" : "border-amber-200 bg-amber-50 text-amber-900"}`}>{text.statuses[group.directoryStatus]}</Badge>{group.lastErrorCode && <span className="mt-1.5 block text-xs text-muted-foreground">{text.checkFailed}</span>}</td><td className="px-5 py-5 text-xs text-muted-foreground">{date(group.lastCheckedAt)}</td><td className="px-5 py-5 text-xs">{group.activeSubscriptions}</td><td className="px-5 py-5 text-xs font-semibold">{group.affectedSubscriptions}</td></tr>)}</tbody></table></div>}
          </section>
          {overview.nextCursor && <div className="mt-4 flex justify-center"><Button variant="outline" disabled={state.loadingMore} onClick={() => void loadMore()}>{state.loadingMore ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}{state.loadingMore ? text.loadingMore : text.loadMore}</Button></div>}
          <p className="mt-4 text-xs leading-6 text-muted-foreground">{text.checkedAt.replace("{date}", date(overview.checkedAt))}</p><p className="mt-2 text-xs leading-6 text-muted-foreground">{text.noAutomaticChanges}</p>
        </>}
  </div>;
}

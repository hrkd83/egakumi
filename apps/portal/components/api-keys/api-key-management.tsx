"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { ArrowUpRight, Check, ClipboardCheck, KeyRound, LoaderCircle, Plus, RefreshCw, Search, ShieldCheck, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RELEASED_API_PRODUCT_ID, type ApiKeyRecord } from "@/lib/api-keys/contracts";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";
import { apiKeyErrorCode, loadApiKeys } from "./client";
import { createApiKeyDisplayStore } from "./store";
import { CreateKeyDialog, KeyDetailDialog } from "./key-dialogs";
import { errorMessage, formatKeyDate, KeyState } from "./presentation";

/**
 * 本人の所属が確認できたグループだけに、APIキーの発行と棚卸の入口を提供します。
 * @remarks Zustandには表示条件のみを保存し、キーの秘密値はダイアログ内から外へ渡しません。
 */
export function ApiKeyManagement({ dictionary, locale, isPlatformAdministrator = false }: { dictionary: Dictionary; locale: Locale; isPlatformAdministrator?: boolean }) {
  const text = dictionary.apiKeys;
  const [store] = useState(createApiKeyDisplayStore);
  const groupId = useStore(store, (state) => state.groupId);
  const query = useStore(store, (state) => state.query);
  const stateFilter = useStore(store, (state) => state.stateFilter);
  const selectedId = useStore(store, (state) => state.selectedId);
  const createOpen = useStore(store, (state) => state.createOpen);
  const overview = useStore(store, (state) => state.overview);
  const loading = useStore(store, (state) => state.loading);
  const error = useStore(store, (state) => state.error);
  const notice = useStore(store, (state) => state.notice);
  const loadSequence = useRef(0);

  /** 新しい取得だけを採用し、失敗時に以前の権限の一覧を残しません。 */
  const fetchOverview = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++loadSequence.current;
    try {
      const result = await loadApiKeys(signal);
      if (signal?.aborted || sequence !== loadSequence.current) return;
      store.setState({ overview: result });
      if (!result.groups.some((group) => group.id === store.getState().groupId)) {
        store.getState().selectGroup(result.groups[0]?.id ?? "");
      }
    } catch (failure) {
      if (signal?.aborted || sequence !== loadSequence.current) return;
      store.setState({ overview: null, error: apiKeyErrorCode(failure) });
    } finally {
      if (!signal?.aborted && sequence === loadSequence.current) store.setState({ loading: false });
    }
  }, [store]);

  useEffect(() => {
    const controller = new AbortController();
    void fetchOverview(controller.signal);
    return () => controller.abort();
  }, [fetchOverview]);

  /** 利用者の更新操作で表示中の秘密値を閉じ、所属確認からやり直します。 */
  function refresh() {
    store.getState().selectKey(null);
    store.getState().setCreateOpen(false);
    store.setState({ loading: true, error: null, notice: null });
    void fetchOverview();
  }

  const group = overview?.groups.find((item) => item.id === groupId);
  const groupKeys = overview?.keys.filter((key) => key.groupId === groupId && key.state !== "deleted") ?? [];
  const selected = groupKeys.find((key) => key.id === selectedId);
  const normalizedQuery = query.trim().toLocaleLowerCase(locale);
  const visibleKeys = groupKeys.filter((key) => {
    const product = group?.products.find((item) => item.id === key.productId);
    return (stateFilter === "all" || key.state === stateFilter)
      && `${key.displayName} ${product?.displayName ?? key.productId} ${key.systemName ?? ""} ${key.teamName ?? ""} ${key.companyName ?? ""}`.toLocaleLowerCase(locale).includes(normalizedQuery);
  });

  /** 操作結果の公開項目だけを台帳へ反映します。 */
  function applyRecord(record: ApiKeyRecord, message: string) {
    const current = store.getState().overview;
    store.setState({ overview: current ? { ...current, keys: current.keys.some((key) => key.id === record.id)
      ? current.keys.map((key) => key.id === record.id ? record : key) : [...current.keys, record] } : current, notice: message });
    if (record.state === "deleted") store.getState().selectKey(null);
  }

  /** 所有権の移管後は秘密表示と以前の台帳を破棄し、変更後の所属範囲を取り直します。 */
  function finishTransfer() {
    store.getState().selectKey(null);
    store.setState({ overview: null, loading: true, error: null, notice: text.transfer.completed });
    void fetchOverview();
  }

  return <div className="mx-auto max-w-[1400px]">
    <section className="mb-8 flex flex-wrap items-start justify-between gap-4">
      <div className="max-w-2xl"><p className="mb-3 text-xs font-bold tracking-[.22em] text-primary">{text.eyebrow}</p><h1 className="text-[30px] font-semibold tracking-[-.035em] sm:text-[34px]">{text.title}</h1><p className="mt-3 text-[13px] leading-7 text-muted-foreground">{text.description}</p></div>
      <Badge variant="outline" className="mt-2 gap-2 rounded-full border-primary/15 bg-primary/4 px-3 py-1.5 text-xs text-primary"><Users className="size-3.5" />{text.groupOwned}</Badge>
    </section>

    {notice && <p role="status" className="mb-5 flex items-start gap-2 rounded-xl border border-primary/15 bg-primary/5 px-4 py-3 text-xs leading-6 text-primary"><Check className="mt-1 size-4 shrink-0" />{notice}</p>}
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-60 max-w-full flex-1 sm:max-w-md"><label htmlFor="key-owner-group" className="mb-2 block text-xs font-medium">{text.memberGroup}</label><Select value={groupId} onValueChange={(value) => { store.getState().selectGroup(value); store.setState({ notice: null }); }} disabled={loading || !overview?.groups.length}>
        <SelectTrigger id="key-owner-group" className="h-11! w-full bg-white"><Users className="size-4 text-muted-foreground" /><SelectValue placeholder={text.selectGroup} /></SelectTrigger><SelectContent>{overview?.groups.map((item) => <SelectItem key={item.id} value={item.id}>{item.displayName}</SelectItem>)}</SelectContent>
      </Select></div>
      <div className="flex gap-2"><Button variant="outline" className="h-10 bg-white px-4 text-xs" disabled={loading} onClick={refresh}><RefreshCw className={loading ? "animate-spin" : ""} />{text.refresh}</Button><Button className="h-10 px-4 text-xs" disabled={loading || !group?.products.some((product) => product.id === RELEASED_API_PRODUCT_ID)} onClick={() => { store.setState({ notice: null }); store.getState().setCreateOpen(true); }}><Plus />{text.issue}</Button></div>
    </div>

    {loading ? <div role="status" className="flex min-h-64 items-center justify-center gap-3 rounded-2xl border border-border bg-white text-sm text-muted-foreground"><LoaderCircle className="size-5 animate-spin" />{text.loading}</div>
      : error ? <div role="alert" className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-sm leading-7 text-amber-950"><ShieldCheck className="mb-3 size-6" />{errorMessage(dictionary, error)}</div>
      : !overview?.groups.length ? <section className="rounded-2xl border border-border bg-white p-8 sm:p-12"><span className="mb-5 flex size-12 items-center justify-center rounded-xl bg-primary/5 text-primary"><Users className="size-6" /></span><h2 className="text-lg font-semibold">{text.noGroupsTitle}</h2><p className="mt-3 max-w-2xl text-sm leading-7 text-muted-foreground">{text.noGroups}</p></section>
      : <>
        <div className="mb-6 grid gap-3 sm:grid-cols-3">{[
          { label: text.count, count: groupKeys.length, icon: KeyRound },
          { label: text.activeCount, count: groupKeys.filter((key) => key.state === "active").length, icon: ShieldCheck },
          { label: text.reviewCount, count: groupKeys.filter((key) => !key.lastReviewedAt).length, icon: ClipboardCheck },
        ].map(({ label, count, icon: Icon }) => <div key={label} className="flex items-center gap-4 rounded-xl border border-border bg-white p-5"><span className="flex size-10 items-center justify-center rounded-xl bg-primary/5 text-primary"><Icon className="size-5" strokeWidth={1.6} /></span><div><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-semibold tracking-tight">{count}</p></div></div>)}</div>
        <section className="overflow-hidden rounded-2xl border border-border bg-white">
          <div className="flex flex-wrap gap-3 border-b border-border p-4 sm:p-5"><div className="relative min-w-44 flex-1"><Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input value={query} onChange={(event) => store.getState().setQuery(event.target.value)} placeholder={text.search} aria-label={text.search} className="pl-9 text-xs" /></div><Select value={stateFilter} onValueChange={(value) => { if (value === "all" || value === "active" || value === "suspended") store.getState().setStateFilter(value); }}><SelectTrigger className="w-40 text-xs" aria-label={text.state}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">{text.allStates}</SelectItem><SelectItem value="active">{text.states.active}</SelectItem><SelectItem value="suspended">{text.states.suspended}</SelectItem></SelectContent></Select></div>
          {!groupKeys.length ? <div className="px-6 py-14 text-center"><KeyRound className="mx-auto mb-4 size-8 text-muted-foreground/50" strokeWidth={1.3} /><h2 className="text-sm font-semibold">{text.noKeysTitle}</h2><p className="mt-2 text-xs leading-6 text-muted-foreground">{text.noKeys}</p></div>
            : !visibleKeys.length ? <p role="status" className="px-6 py-14 text-center text-sm text-muted-foreground">{text.noMatches}</p>
              : <div className="overflow-x-auto"><table className="w-full min-w-[720px] border-collapse text-left"><thead className="bg-muted/30 text-xs text-muted-foreground"><tr><th scope="col" className="px-5 py-3 font-medium">{text.usage}</th><th scope="col" className="px-4 py-3 font-medium">{text.product}</th><th scope="col" className="px-4 py-3 font-medium">{text.state}</th><th scope="col" className="px-4 py-3 font-medium">{text.lastReviewed}</th><th scope="col" className="px-5 py-3"><span className="sr-only">{text.details}</span></th></tr></thead><tbody className="divide-y divide-border">{visibleKeys.map((key) => <tr key={key.id} className="hover:bg-muted/20"><th scope="row" className="px-5 py-5"><span className="block text-xs font-semibold">{key.displayName}</span><span className="mt-1.5 block text-xs font-normal text-muted-foreground">{text.createdAt} {formatKeyDate(key.createdAt, locale, "—")}</span></th><td className="px-4 py-5 text-xs">{group?.products.find((item) => item.id === key.productId)?.displayName ?? key.productId}</td><td className="px-4 py-5"><KeyState state={key.state} dictionary={dictionary} /></td><td className="px-4 py-5 text-xs text-muted-foreground">{formatKeyDate(key.lastReviewedAt, locale, text.notReviewed)}</td><td className="px-5 py-5 text-right"><Button variant="ghost" size="sm" className="text-primary" aria-label={`${key.displayName} · ${text.details}`} onClick={() => store.getState().selectKey(key.id)}>{text.details}<ArrowUpRight className="size-3.5" /></Button></td></tr>)}</tbody></table></div>}
        </section>
      </>}

    {createOpen && overview && <CreateKeyDialog key={groupId} overview={overview} initialGroupId={groupId} dictionary={dictionary} locale={locale} isPlatformAdministrator={isPlatformAdministrator} onClose={() => store.getState().setCreateOpen(false)} onCreated={(record) => { store.getState().setCreateOpen(false); if (groupId !== record.groupId) store.getState().selectGroup(record.groupId); applyRecord(record, text.created); }} />}
    {selected && group && <KeyDetailDialog key={selected.id} record={selected} group={group} dictionary={dictionary} locale={locale} onClose={() => store.getState().selectKey(null)} onChanged={(record) => applyRecord(record, record.state === "deleted" ? text.deleted : text.updated)} onTransferred={finishTransfer} />}
  </div>;
}

"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { RefreshCw, ShieldCheck, ArrowRight, CircleAlert, LoaderCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { SubscriptionSyncText } from "@/lib/i18n/subscription-sync";
import type { SubscriptionDifference, SyncInventory, SyncPreview, SyncResult } from "@/lib/subscription-sync/contracts";
import { apiKeyErrorCode } from "../api-keys/client";
import { applySync, loadSyncInventory, previewSync } from "./client";

type Screen = { inventory: SyncInventory | null; busy: boolean; error: string | null; query: string; differencesOnly: boolean;
  page: number; selected: string[]; preview: SyncPreview | null; confirmed: boolean; results: SyncResult[] };
/** 管理画面ごとに選択・検索・確認状態を独立させます。 */
function screenStore() { return createStore<Screen>(() => ({ inventory: null, busy: true, error: null, query: "", differencesOnly: true, page: 0,
  selected: [], preview: null, confirmed: false, results: [] })); }
/** 一致・master・処理途中を反映対象として選択させません。 */
function actionable(row: SubscriptionDifference) { return ["update", "recreate", "delete", "delete-unregistered"].includes(row.action); }

/** 基盤管理者が全台帳を照合し、SQLを正として確認済み差分をAPIMへ反映します。 */
export function SubscriptionSyncDashboard({ text }: { text: SubscriptionSyncText }) {
  const [store] = useState(screenStore), state = useStore(store);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const load = useCallback(async (keepResults = false) => {
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    store.setState({ busy: true, error: null, inventory: null, selected: [], preview: null, confirmed: false, ...(keepResults ? {} : { results: [] }) });
    try { const inventory = await loadSyncInventory(current.signal); if (!current.signal.aborted) store.setState({ inventory }); }
    catch (error) { if (!current.signal.aborted) store.setState({ error: apiKeyErrorCode(error) }); }
    finally { if (!current.signal.aborted) store.setState({ busy: false }); }
  }, [store]);
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; controller.current?.abort(); }; }, [load]);
  /** 確認を開く前にもSQLとAPIMを再読込します。 */
  async function review() {
    store.setState({ busy: true, error: null, results: [] });
    try { const preview = await previewSync(state.selected); if (mounted.current) store.setState({ preview, confirmed: false }); }
    catch (error) { if (mounted.current) store.setState({ error: apiKeyErrorCode(error) }); }
    finally { if (mounted.current) store.setState({ busy: false }); }
  }
  /** 確定の二重送信を避け、結果取得後は元のSQL/APIMを再照会します。 */
  async function apply() {
    if (!state.preview || !state.confirmed || state.busy) return;
    store.setState({ busy: true, error: null });
    try {
      const result = await applySync(state.preview.token);
      if (mounted.current) { store.setState({ results: result.results, preview: null }); await load(true); }
    } catch (error) { if (mounted.current) store.setState({ error: apiKeyErrorCode(error), preview: null, confirmed: false }); }
    finally { if (mounted.current) store.setState({ busy: false }); }
  }
  const rows = state.inventory?.rows ?? [];
  const filtered = rows.filter((row) => (!state.differencesOnly || row.action !== "none")
    && `${row.sql?.purpose ?? ""} ${row.sql?.companyName ?? ""} ${row.sql?.groupName ?? ""} ${row.apim?.displayName ?? ""} ${row.sql?.apimSubscriptionId ?? row.apim?.id ?? ""}`.toLocaleLowerCase().includes(state.query.toLocaleLowerCase()));
  const page = Math.min(state.page, Math.max(0, Math.ceil(filtered.length / 50) - 1));
  const visible = filtered.slice(page * 50, (page + 1) * 50);
  const error = state.error && (["SyncConflict", "SyncPlanExpired", "UpstreamConflict", "SyncAuditUnavailable"].includes(state.error) ? text.conflict : text.error);
  /** 差分表はプレビューでも同じ列を使い、更新前後をその場で比較できます。 */
  function table(data: SubscriptionDifference[], selecting: boolean) {
    return <div className="overflow-x-auto rounded-xl border bg-white"><table className="w-full text-left text-sm"><thead className="bg-muted/60 text-xs text-muted-foreground"><tr>
      {selecting && <th className="p-3">{text.selected}</th>}<th className="p-3">{text.name}</th><th className="p-3">{text.company}</th><th className="p-3">{text.sql}</th><th className="p-3">{text.apim}</th><th className="p-3">{text.action}</th>
    </tr></thead><tbody>{data.map((row) => <tr key={row.id} className="border-t align-top">
      {selecting && <td className="p-3"><input type="checkbox" aria-label={`${text.selected}: ${row.sql?.purpose ?? row.apim?.displayName}`} className="size-4 accent-primary"
        checked={state.selected.includes(row.id)} disabled={state.busy || !state.inventory?.writable || !actionable(row) || (state.selected.length >= 10 && !state.selected.includes(row.id))}
        onChange={(event) => store.setState({ selected: event.target.checked ? [...state.selected, row.id] : state.selected.filter((id) => id !== row.id) })} /></td>}
      <td className="max-w-64 p-3"><div className="font-medium break-words">{row.sql?.purpose ?? row.apim?.displayName}</div><div className="mt-1 break-all text-xs text-muted-foreground">{row.sql?.apimSubscriptionId ?? row.apim?.id}</div>
        {row.apim && row.desired && row.apim.displayName !== row.desired.displayName && <div className="mt-2 text-xs">{row.apim.displayName} <ArrowRight className="inline size-3" /> {row.desired.displayName}</div>}
        {(row.desired || row.apim) && <div className="mt-1 break-all text-xs text-muted-foreground">{row.apim?.scope.includes("/products/") ? row.apim.scope.split("/products/")[1] : row.apim?.id === "master" ? "master" : row.apim?.scope ?? text.missing}{row.desired && row.apim?.scope.split("/products/")[1] !== row.desired.productId && <><ArrowRight className="mx-1 inline size-3" />{row.desired.productId}</>}</div>}
      </td><td className="max-w-56 p-3"><div>{row.sql?.companyName ?? text.noCompany}</div><div className="mt-1 text-xs text-muted-foreground">{row.sql?.groupName ?? text.unknown}</div></td>
      <td className="p-3">{row.sql?.state ?? text.missing}</td><td className="p-3">{row.apim?.state ?? text.missing}</td><td className="p-3"><Badge variant="outline">{text.actions[row.action]}</Badge>{row.reactivates && <p className="mt-2 text-xs font-semibold text-amber-800">{text.reactivate}</p>}</td>
    </tr>)}</tbody></table>{!data.length && <p className="p-8 text-sm text-muted-foreground">{text.empty}</p>}</div>;
  }
  return <section className="mx-auto max-w-7xl space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-4"><div><Badge variant="outline"><ShieldCheck className="mr-1 size-3" />PLATFORM ADMINISTRATION</Badge><h1 className="mt-3 text-2xl font-semibold tracking-tight">{text.title}</h1><p className="mt-2 max-w-3xl text-sm leading-7 text-muted-foreground">{text.description}</p></div><Button variant="outline" disabled={state.busy} onClick={() => void load()}><RefreshCw className="size-4" />{text.refresh}</Button></div>
    {state.inventory && <div className="space-y-3 rounded-xl border bg-white p-5 text-sm"><div><Badge variant="outline">{state.inventory.source === "local" ? text.local : text.azure}</Badge></div><p className="break-all"><span className="text-muted-foreground">{text.source}: </span>{state.inventory.server} / {state.inventory.database}</p><p className="break-all"><span className="text-muted-foreground">{text.target}: </span>{state.inventory.apimResourceId}</p>
      {!state.inventory.writable && <p className="rounded-lg bg-amber-50 p-3 leading-6 text-amber-900">{text.readonly}</p>}
      <div className="flex gap-6 pt-2"><span>{text.total}: <strong>{rows.length}</strong></span><span>{text.matching}: <strong>{rows.filter((row) => row.action === "none").length}</strong></span><span>{text.changes}: <strong>{rows.filter(actionable).length}</strong></span></div></div>}
    {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900"><CircleAlert className="mr-2 inline size-4" />{error}<span className="mt-1 block text-xs">{state.error}</span></div>}
    {state.results.length > 0 && <div role="status" className="space-y-2 rounded-xl border bg-white p-5"><h2 className="font-semibold">{text.result}</h2><p className="text-sm text-muted-foreground">{text.completed}</p>{state.results.map((result) => <p key={result.id} className="break-all text-sm">{result.id} · {text[result.outcome]} {result.code ? `(${result.code})` : ""}</p>)}</div>}
    <div className="flex flex-wrap items-center gap-4"><Input value={state.query} placeholder={text.search} aria-label={text.search} className="max-w-lg" onChange={(event) => store.setState({ query: event.target.value, page: 0 })} /><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={state.differencesOnly} onChange={(event) => store.setState({ differencesOnly: event.target.checked, page: 0 })} />{text.differences}</label><Button className="ml-auto" disabled={state.busy || !state.inventory?.writable || !state.selected.length} onClick={() => void review()}>{state.busy && <LoaderCircle className="size-4 animate-spin" />}{text.preview} ({state.selected.length})</Button></div>
    <p className="text-xs text-muted-foreground">{text.limit}</p>
    {state.busy && !state.inventory ? <p role="status" className="p-8 text-sm text-muted-foreground">{text.loading}</p> : table(visible, true)}
    <div className="flex items-center justify-end gap-3 text-sm"><Button variant="outline" disabled={state.busy || page === 0} onClick={() => store.setState({ page: page - 1 })}>{text.previous}</Button>{page + 1} / {Math.max(1, Math.ceil(filtered.length / 50))}<Button variant="outline" disabled={state.busy || (page + 1) * 50 >= filtered.length} onClick={() => store.setState({ page: page + 1 })}>{text.next}</Button></div>
    <div className="space-y-3 rounded-xl border bg-white p-5"><h2 className="font-semibold">{text.history}</h2><p className="text-xs leading-6 text-muted-foreground">{text.historyHelp}</p><div className="max-h-80 space-y-2 overflow-y-auto">{state.inventory?.audit.map((event) => <p className="break-all border-b py-2 text-xs" key={event.id}>{event.recordedAt} · {event.subscriptionId} · {text.actions[event.action as keyof typeof text.actions] ?? event.action} · {text.phases[event.phase as keyof typeof text.phases] ?? event.phase} {event.code ? `(${event.code})` : ""}</p>)}{!state.inventory?.audit.length && <p className="text-sm text-muted-foreground">{text.noHistory}</p>}</div></div>
    <p className="text-xs text-muted-foreground">{text.actorHelp}</p>
    <Dialog open={!!state.preview} onOpenChange={(open) => { if (!open && !state.busy) store.setState({ preview: null, confirmed: false }); }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-5xl" showCloseButton={!state.busy}><DialogHeader><DialogTitle>{text.review}</DialogTitle><DialogDescription className="leading-6">{text.reviewHelp}</DialogDescription></DialogHeader>
      {state.preview && <>{table(state.preview.rows, false)}<p className="text-xs text-muted-foreground">{text.expires}: {state.preview.expiresAt}</p><label className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm leading-6"><input className="mt-1 size-4 shrink-0" type="checkbox" checked={state.confirmed} disabled={state.busy} onChange={(event) => store.setState({ confirmed: event.target.checked })} />{text.confirm}</label></>}
      <DialogFooter><Button variant="outline" disabled={state.busy} onClick={() => store.setState({ preview: null, confirmed: false })}>{text.cancel}</Button><Button disabled={state.busy || !state.confirmed} onClick={() => void apply()}>{state.busy ? text.applying : text.apply}</Button></DialogFooter></DialogContent></Dialog>
  </section>;
}

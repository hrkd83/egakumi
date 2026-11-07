"use client";

import { useFieldIds } from "@/lib/use-field-ids";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowRightLeft, LoaderCircle, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ApiKeyRecord } from "@/lib/api-keys/contracts";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import { apiKeyErrorCode, findApiKeyOwnerGroups, transferApiKeyOwner, type ApiKeyOwnerTarget } from "./client";
import { errorMessage } from "./presentation";

/** 移管元メンバーが環境の対象条件に一致するグループを検索し、確認後に直接所有権を移します。 */
export function ApiKeyOwnershipTransfer({ record, sourceName, dictionary, disabled, onBusyChange, onOpen, onTransferred }: {
  record: ApiKeyRecord; sourceName: string; dictionary: Dictionary; disabled: boolean; onBusyChange: (busy: boolean) => void;
  onOpen: () => void; onTransferred: (record: ApiKeyRecord) => void;
}) {
  const formId = useFieldIds();
  const text = dictionary.apiKeys.transfer;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [groups, setGroups] = useState<ApiKeyOwnerTarget[] | null>(null);
  const [targetId, setTargetId] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const target = groups?.find((group) => group.id === targetId);
  const busy = disabled || searching || saving;

  useEffect(() => () => { controller.current?.abort(); onBusyChange(false); }, [onBusyChange]);

  /** 入力内容をGraphへ明示検索し、前の検索で得た候補や選択は使い回しません。 */
  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || query.trim().length < 2) return;
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    setSearching(true); setError(null); setGroups(null); setNextCursor(null); setTargetId(""); setConfirmed(false);
    try {
      const result = await findApiKeyOwnerGroups(record.id, query, undefined, current.signal);
      if (!current.signal.aborted) { setGroups(result.groups.filter((group) => group.id !== record.groupId && group.directoryStatus === "active")); setNextCursor(result.nextCursor); }
    } catch (failure) { if (!current.signal.aborted) setError(apiKeyErrorCode(failure)); }
    finally { if (!current.signal.aborted) setSearching(false); }
  }

  /** 続きのページもサーバー認可を通して取得し、検索条件を維持して候補を追加します。 */
  async function loadMore() {
    if (busy || !nextCursor) return;
    const current = new AbortController(); controller.current = current;
    setSearching(true); setError(null);
    try {
      const result = await findApiKeyOwnerGroups(record.id, query, nextCursor, current.signal);
      if (!current.signal.aborted) {
        setGroups((previous) => [...new Map([...(previous ?? []), ...result.groups].filter((group) => group.id !== record.groupId && group.directoryStatus === "active").map((group) => [group.id, group])).values()]);
        setNextCursor(result.nextCursor);
        setConfirmed(false);
      }
    } catch (failure) { if (!current.signal.aborted) setError(apiKeyErrorCode(failure)); }
    finally { if (!current.signal.aborted) setSearching(false); }
  }

  /** 移管後は元の所有者情報や秘密値を画面に残さず、親で一覧を再取得します。 */
  async function transfer() {
    if (busy || !target || !confirmed) return;
    const current = new AbortController(); controller.current = current;
    setSaving(true); onBusyChange(true); setError(null);
    try {
      const result = await transferApiKeyOwner(record.id, { targetGroupId: target.id, expectedSourceGroupId: record.groupId }, current.signal);
      if (!current.signal.aborted) onTransferred(result);
    } catch (failure) { if (!current.signal.aborted) setError(apiKeyErrorCode(failure)); }
    finally { if (!current.signal.aborted) { setSaving(false); onBusyChange(false); } }
  }

  return <section className="rounded-xl border border-border p-4">
    <div className="flex items-start justify-between gap-3"><div><h3 className="text-xs font-semibold">{text.title}</h3><p className="mt-1 text-xs leading-6 text-muted-foreground">{text.description}</p></div><Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => { onOpen(); setOpen((value) => !value); }}><ArrowRightLeft />{open ? dictionary.apiKeys.cancel : text.open}</Button></div>
    {open && <div className="mt-4 space-y-4">
      <form onSubmit={search} className="space-y-2"><label htmlFor={formId(`owner-search-${record.id}`)} className="block text-xs font-medium">{text.searchLabel}</label><div className="flex gap-2"><Input id={formId(`owner-search-${record.id}`)} value={query} minLength={2} maxLength={80} required autoComplete="off" disabled={busy} placeholder={text.searchPlaceholder} className="min-w-0 text-xs" onChange={(event) => { setQuery(event.target.value); setGroups(null); setNextCursor(null); setTargetId(""); setConfirmed(false); setError(null); }} /><Button type="submit" variant="outline" disabled={busy || query.trim().length < 2}>{searching ? <LoaderCircle className="animate-spin" /> : <Search />}{text.search}</Button></div><p className="text-xs leading-5 text-muted-foreground">{text.searchHelp}</p></form>
      {groups !== null && (groups.length === 0 ? <p role="status" className="text-xs text-muted-foreground">{nextCursor ? text.noPageTargets : text.noTargets}</p> : <div>
        <label htmlFor={formId(`owner-target-${record.id}`)} className="mb-2 block text-xs font-medium">{text.target}</label>
        <Select value={targetId} disabled={busy} onValueChange={(value) => { setTargetId(value); setConfirmed(false); setError(null); }}>
          <SelectTrigger id={formId(`owner-target-${record.id}`)} className="w-full text-xs"><SelectValue placeholder={text.choose} /></SelectTrigger>
          <SelectContent>{[true, false].map((membership) => {
            const candidates = groups.filter((group) => group.isMember === membership);
            return candidates.length ? <SelectGroup key={String(membership)}><SelectLabel>{membership ? text.memberGroups : text.otherGroups}</SelectLabel>{candidates.map((group) => <SelectItem key={group.id} value={group.id}><span>{group.displayName}</span><span className={`ml-2 rounded px-1.5 py-0.5 text-xs ${membership ? "bg-primary/8 text-primary" : "bg-amber-100 text-amber-900"}`}>{membership ? text.member : text.notMember}</span></SelectItem>)}</SelectGroup> : null;
          })}</SelectContent>
        </Select>
        {nextCursor && <p className="mt-2 text-xs text-muted-foreground">{text.refineSearch}</p>}
      </div>)}
      {nextCursor && <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void loadMore()}>{searching && <LoaderCircle className="animate-spin" />}{text.loadMore}</Button>}
      {target && <div className="space-y-3 rounded-lg border border-amber-200 bg-amber-50/50 p-3">
        <dl className="grid gap-3 text-xs"><div><dt className="text-muted-foreground">{text.source}</dt><dd className="mt-1 font-semibold">{sourceName}</dd><dd className="mt-1 break-all font-mono text-xs text-muted-foreground">{record.groupId}</dd></div><div><dt className="text-muted-foreground">{text.target}</dt><dd className="mt-1 font-semibold">{target.displayName} · {target.isMember ? text.member : text.notMember}</dd><dd className="mt-1 break-all font-mono text-xs text-muted-foreground">{target.id}</dd></div></dl>
        {!target.isMember && <p role="status" className="rounded-md border border-amber-300 bg-amber-100/50 p-2 text-xs font-semibold leading-6 text-amber-950">{text.losesAccess}</p>}
        <p className="text-xs leading-6 text-muted-foreground">{text.notice}</p>
        <label className="flex items-start gap-2 text-xs leading-6"><input type="checkbox" className="mt-1 size-4 accent-primary" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} /><span>{text.confirm}</span></label>
        <div className="flex justify-end"><Button type="button" disabled={busy || !confirmed} onClick={() => void transfer()}>{saving ? <LoaderCircle className="animate-spin" /> : <ArrowRightLeft />}{saving ? dictionary.apiKeys.working : text.submit}</Button></div>
      </div>}
      {error && <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-xs leading-6 text-destructive">{errorMessage(dictionary, error)}</p>}
    </div>}
  </section>;
}

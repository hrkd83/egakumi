"use client";

import { useFieldIds } from "@/lib/use-field-ids";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Clipboard, ClipboardCheck, Eye, EyeOff, LoaderCircle, Pause, Play, Trash2, Users, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { type ApiKeyGroup, type ApiKeyRecord, type ApiKeySecrets } from "@/lib/api-keys/contracts";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";
import { actOnApiKey, apiKeyErrorCode } from "./client";
import { errorMessage, formatKeyDate, KeyState } from "./presentation";
import { ApiKeyLimitProfileEditor } from "./limit-profile";
import { ApiKeyOwnershipTransfer } from "./ownership-transfer";

export { CreateKeyDialog } from "./create-key-dialog";

/**
 * 明示操作で取得したキーを最大60秒だけ表示します。
 * @remarks このコンポーネントを閉じる・タブを隠す・ページを離れると破棄します。キーは親やZustandへ渡しません。
 */
function SecretKeys({ id, dictionary, onHide }: { id: string; dictionary: Dictionary; onHide: () => void }) {
  const formId = useFieldIds();
  const text = dictionary.apiKeys;
  const [secrets, setSecrets] = useState<ApiKeySecrets | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<keyof ApiKeySecrets | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(onHide, 60_000);
    const hideWhenAway = () => { if (document.visibilityState !== "visible") onHide(); };
    document.addEventListener("visibilitychange", hideWhenAway);
    window.addEventListener("pagehide", onHide);
    void actOnApiKey(id, "reveal", controller.signal).then((result) => {
      if (!controller.signal.aborted) setSecrets(result);
    }).catch((failure) => { if (!controller.signal.aborted) setError(apiKeyErrorCode(failure)); });
    return () => {
      controller.abort(); clearTimeout(timeout);
      document.removeEventListener("visibilitychange", hideWhenAway);
      window.removeEventListener("pagehide", onHide);
    };
  }, [id, onHide]);

  /** キーをログに出さず、利用者の明示操作でクリップボードへコピーします。 */
  async function copy(field: keyof ApiKeySecrets) {
    if (!secrets) return;
    setCopyFailed(false);
    try { await navigator.clipboard.writeText(secrets[field]); setCopied(field); }
    catch { setCopyFailed(true); }
  }

  return <div className="space-y-4 rounded-xl border border-primary/15 bg-primary/3 p-4">
    <div className="flex items-center justify-between gap-3"><p className="text-xs leading-5 text-muted-foreground">{text.keyNotice}</p><Button size="sm" variant="ghost" onClick={onHide}><EyeOff />{text.hideKeys}</Button></div>
    {error ? <p role="alert" className="text-xs leading-6 text-destructive">{errorMessage(dictionary, error)}</p> : !secrets ? <p role="status" className="flex items-center gap-2 text-xs"><LoaderCircle className="size-4 animate-spin" />{text.working}</p> : (["primaryKey", "secondaryKey"] as const).map((field) => <div key={field}><label htmlFor={formId(`secret-${field}`)} className="mb-2 block text-xs font-medium">{text[field]}</label><div className="flex gap-2"><Input id={formId(`secret-${field}`)} type="text" readOnly autoComplete="off" spellCheck={false} value={secrets[field]} className="min-w-0 bg-white font-mono text-xs" /><Button size="icon" variant="outline" aria-label={`${text[field]} · ${text.copy}`} onClick={() => void copy(field)}>{copied === field ? <Check /> : <Clipboard />}</Button></div></div>)}
    {copied && <p role="status" className="text-xs text-primary">{text.copied}</p>}
    {copyFailed && <p role="alert" className="text-xs leading-6 text-destructive">{text.copyFailed}</p>}
  </div>;
}

/** 所有グループと影響範囲を見ながら、購読の停止・再開・棚卸・削除を行います。 */
export function KeyDetailDialog({ record, group, dictionary, locale, onClose, onChanged, onTransferred }: {
  record: ApiKeyRecord; group: ApiKeyGroup; dictionary: Dictionary; locale: Locale;
  onClose: () => void; onChanged: (record: ApiKeyRecord) => void; onTransferred: (record: ApiKeyRecord) => void;
}) {
  const formId = useFieldIds();
  const text = dictionary.apiKeys;
  const [showSecrets, setShowSecrets] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteName, setDeleteName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [limitsSaving, setLimitsSaving] = useState(false);
  const [transferSaving, setTransferSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const hideSecrets = useCallback(() => setShowSecrets(false), []);
  const changeLimitsSaving = useCallback((saving: boolean) => {
    setLimitsSaving(saving);
    if (saving) setShowSecrets(false);
  }, []);
  const changeTransferSaving = useCallback((saving: boolean) => {
    setTransferSaving(saving);
    if (saving) setShowSecrets(false);
  }, []);
  const dialogBusy = Boolean(busy) || limitsSaving || transferSaving;
  const actionable = record.state === "active" || record.state === "suspended";
  const deletable = record.state !== "deleted";
  useEffect(() => () => controller.current?.abort(), []);

  /** キーの表示を閉じてから管理操作を行い、成功した公開台帳だけを親へ返します。 */
  async function act(action: "suspend" | "resume" | "review" | "delete") {
    if (dialogBusy) return;
    hideSecrets();
    setBusy(action); setError(null);
    controller.current = new AbortController();
    try {
      const updated = await actOnApiKey(record.id, action, controller.current.signal);
      if (!controller.current.signal.aborted) { onChanged(updated); setDeleteOpen(false); setDeleteName(""); }
    } catch (failure) {
      if (!controller.current.signal.aborted) setError(apiKeyErrorCode(failure));
    } finally {
      if (!controller.current.signal.aborted) setBusy(null);
    }
  }

  return <Dialog open onOpenChange={(open) => { if (!open && !dialogBusy) onClose(); }}>
    <DialogContent showCloseButton={false} className="max-h-[90dvh] gap-5 overflow-y-auto p-6 sm:max-w-2xl" onEscapeKeyDown={(event) => { if (dialogBusy) event.preventDefault(); }} onInteractOutside={(event) => { if (dialogBusy) event.preventDefault(); }}>
      <DialogHeader className="pr-8"><div className="mb-2"><KeyState state={record.state} dictionary={dictionary} /></div><DialogTitle className="break-words text-xl leading-7">{record.displayName}</DialogTitle><DialogDescription className="text-xs leading-6">{text.detailsDescription}</DialogDescription></DialogHeader>
      <Button variant="ghost" size="icon-sm" className="absolute right-3 top-3" aria-label={text.close} disabled={dialogBusy} onClick={onClose}><X /></Button>
      <dl className="grid gap-x-6 gap-y-4 rounded-xl bg-muted/30 p-4 sm:grid-cols-2">{[
        [text.group, group.displayName], [text.product, group.products.find((item) => item.id === record.productId)?.displayName ?? record.productId],
        [text.createdAt, formatKeyDate(record.createdAt, locale, "—")], [text.lastReviewed, formatKeyDate(record.lastReviewedAt, locale, text.notReviewed)],
      ].map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1.5 break-words text-xs font-medium">{value}</dd></div>)}<div className="sm:col-span-2"><dt className="text-xs text-muted-foreground">{text.subscriptionId}</dt><dd className="mt-1.5 break-all font-mono text-xs text-muted-foreground">{record.apimSubscriptionId}</dd></div></dl>
      <section aria-labelledby={formId("key-usage-metadata")}><h3 id={formId("key-usage-metadata")} className="mb-3 text-sm font-semibold">{text.createForm.metadataTitle}</h3><dl className="grid gap-x-6 gap-y-4 rounded-xl border border-border p-4 sm:grid-cols-2">{[
        [text.createForm.clientType, record.clientType ? text.createForm.clientTypes[record.clientType] : null],
        [text.createForm.companyName, record.companyName], [text.createForm.systemName, record.systemName],
        [text.createForm.teamName, record.teamName], [text.createForm.contactEmail, record.contactEmail],
      ].map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className={`mt-1.5 break-words text-sm ${value ? "font-medium" : "text-muted-foreground"}`}>{value || text.createForm.notProvided}</dd></div>)}</dl>{!record.clientType && !record.companyId && !record.contactEmail && <p className="mt-2 text-xs leading-5 text-muted-foreground">{text.createForm.legacyNotice}</p>}</section>
      <p className="flex items-start gap-2 text-xs leading-6 text-muted-foreground"><Users className="mt-1 size-4 shrink-0" />{text.ownerHelp}</p>
      {showSecrets ? <SecretKeys id={record.id} dictionary={dictionary} onHide={hideSecrets} /> : <Button variant="outline" className="w-full text-xs" disabled={dialogBusy || !actionable || deleteOpen} onClick={() => setShowSecrets(true)}><Eye />{text.showKeys}</Button>}
      {actionable && <ApiKeyLimitProfileEditor id={record.id} dictionary={dictionary} disabled={Boolean(busy) || transferSaving || deleteOpen} onSavingChange={changeLimitsSaving} />}
      {actionable && <ApiKeyOwnershipTransfer record={record} sourceName={group.displayName} dictionary={dictionary} disabled={Boolean(busy) || limitsSaving || deleteOpen} onBusyChange={changeTransferSaving} onOpen={hideSecrets} onTransferred={onTransferred} />}
      {actionable ? <div className="space-y-4 border-t border-border pt-5">
        <div className="flex items-start justify-between gap-4"><div><h3 className="text-xs font-semibold">{record.state === "active" ? text.suspend : text.resume}</h3><p className="mt-1 max-w-sm text-xs leading-5 text-muted-foreground">{text.suspendNotice}</p></div><Button variant="outline" className="text-xs" disabled={dialogBusy || deleteOpen} onClick={() => void act(record.state === "active" ? "suspend" : "resume")}>{busy === "suspend" || busy === "resume" ? <LoaderCircle className="animate-spin" /> : record.state === "active" ? <Pause /> : <Play />}{record.state === "active" ? text.suspend : text.resume}</Button></div>
        <div className="flex items-start justify-between gap-4"><div><h3 className="text-xs font-semibold">{text.review}</h3><p className="mt-1 max-w-sm text-xs leading-5 text-muted-foreground">{text.reviewNotice}</p></div><Button variant="outline" className="text-xs" disabled={dialogBusy || deleteOpen} onClick={() => void act("review")}>{busy === "review" ? <LoaderCircle className="animate-spin" /> : <ClipboardCheck />}{text.review}</Button></div>
      </div> : <p role="status" className="rounded-xl bg-muted/40 p-4 text-xs leading-6 text-muted-foreground">{text.processingNotice}</p>}
      {deleteOpen && <section className="space-y-3 rounded-xl border border-destructive/20 bg-destructive/3 p-4"><h3 className="text-sm font-semibold text-destructive">{text.deleteTitle}</h3><p className="text-xs leading-6 text-muted-foreground">{text.deleteNotice}</p><label htmlFor={formId("delete-purpose")} className="block text-xs font-medium">{text.deleteConfirm}：<span className="select-all">{record.displayName}</span></label><Input id={formId("delete-purpose")} autoComplete="off" value={deleteName} disabled={dialogBusy} onChange={(event) => setDeleteName(event.target.value)} /><div className="flex justify-end gap-2"><Button variant="outline" disabled={dialogBusy} onClick={() => { setDeleteOpen(false); setDeleteName(""); }}>{text.cancel}</Button><Button variant="destructive" disabled={dialogBusy || deleteName !== record.displayName} onClick={() => void act("delete")}>{busy === "delete" ? <LoaderCircle className="animate-spin" /> : <Trash2 />}{text.deleteFinal}</Button></div></section>}
      {error && <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-xs leading-6 text-destructive">{errorMessage(dictionary, error)}</p>}
      <DialogFooter className="-mx-6 -mb-6 items-center p-4 sm:justify-between"><Button variant="destructive" className="text-xs" disabled={dialogBusy || !deletable || deleteOpen} onClick={() => { hideSecrets(); setDeleteOpen(true); }}><Trash2 />{text.delete}</Button><Button variant="outline" disabled={dialogBusy} onClick={onClose}>{busy ? text.working : text.close}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}

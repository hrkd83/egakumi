"use client";

import { useFieldIds } from "@/lib/use-field-ids";

import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { Building2, CircleAlert, LoaderCircle, Pencil, Plus, RefreshCw, Search, ShieldCheck, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { CompanyAdministrationText } from "@/lib/i18n/company-administration";
import type { Locale } from "@/lib/i18n/config";
import { apiKeyErrorCode } from "../api-keys/client";
import { createCompany, loadCompanies, updateCompany, type CompanyRecord } from "./client";

type Editor = { company: CompanyRecord | null; name: string; isActive: boolean; busy: boolean; error: string | null };
type CompanyScreenState = { companies: CompanyRecord[]; loading: boolean; error: string | null; notice: boolean;
  query: string; status: "all" | "active" | "inactive"; editor: Editor | null };

/** 会社画面ごとに独立した表示状態を持ち、サーバー描画をまたいで利用者の状態を共有しません。 */
function createCompanyScreenStore() {
  return createStore<CompanyScreenState>(() => ({ companies: [], loading: true, error: null, notice: false,
    query: "", status: "all", editor: null }));
}

/** 内部例外や応答本文を出さず、入力修正・再読込が必要な失敗だけを案内します。 */
function companyError(text: CompanyAdministrationText, code: string): string {
  if (["InvalidInput", "InvalidRequest"].includes(code)) return text.invalid;
  if (["AccessDenied", "AuthenticationRequired", "AuthorizationUnavailable"].includes(code)) return text.forbidden;
  if (["CompanyNameExists", "CompanyNameConflict", "CompanyAlreadyExists", "DuplicateCompany"].includes(code)) return text.duplicate;
  if (["CompanyVersionConflict", "VersionConflict"].includes(code)) return text.conflict;
  return text.error;
}

/** 基盤管理グループのメンバーが、発行フォームで使用する会社マスタを管理します。 */
export function CompanyAdministrationDashboard({ text, locale }: { text: CompanyAdministrationText; locale: Locale }) {
  const formId = useFieldIds();
  const [store] = useState(createCompanyScreenStore);
  const state = useStore(store);
  const listController = useRef<AbortController | null>(null);
  const saveController = useRef<AbortController | null>(null);

  /** 照会失敗を空の正常一覧に置き換えず、古い権限の表示を破棄します。 */
  const load = useCallback(async () => {
    listController.current?.abort();
    const current = new AbortController(); listController.current = current;
    store.setState({ loading: true, error: null, companies: [], notice: false });
    try {
      const companies = await loadCompanies(current.signal);
      if (!current.signal.aborted) store.setState({ companies });
    } catch (error) {
      if (!current.signal.aborted) store.setState({ error: apiKeyErrorCode(error) });
    } finally {
      if (!current.signal.aborted) store.setState({ loading: false });
    }
  }, [store]);

  useEffect(() => { void load(); return () => { listController.current?.abort(); saveController.current?.abort(); }; }, [load]);

  /** 編集元の版を保持し、一覧が変わっても別の会社を更新しません。 */
  function openEditor(company: CompanyRecord | null) {
    store.setState({ editor: { company, name: company?.name ?? "", isActive: company?.isActive ?? true, busy: false, error: null }, notice: false });
  }

  /** 保存中は閉じず、失敗時も入力を保持します。 */
  function closeEditor() { if (!store.getState().editor?.busy) store.setState({ editor: null }); }

  /** 入力項目を変えた場合だけ、その入力に対するエラーを解除します。 */
  function changeEditor(values: Partial<Pick<Editor, "name" | "isActive">>) {
    store.setState((previous) => ({ editor: previous.editor ? { ...previous.editor, ...values, error: null } : null }));
  }

  /** 名称・選択可否の更新は、管理APIへ読取時の更新日時も送って確定します。 */
  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const editor = store.getState().editor;
    if (!editor || editor.busy) return;
    const name = editor.name.trim();
    if (!name || name.length > 160) { store.setState({ editor: { ...editor, error: "InvalidRequest" } }); return; }
    const current = new AbortController(); saveController.current = current;
    store.setState({ editor: { ...editor, busy: true, error: null } });
    try {
      const company = editor.company
        ? await updateCompany(editor.company.id, { name, isActive: editor.isActive, expectedUpdatedAt: editor.company.updatedAt }, current.signal)
        : await createCompany(name, current.signal);
      if (!current.signal.aborted) store.setState((previous) => ({ editor: null, notice: true,
        companies: [...previous.companies.filter((record) => record.id !== company.id), company].sort((a, b) => a.name.localeCompare(b.name, locale)) }));
    } catch (error) {
      if (!current.signal.aborted) store.setState({ editor: { ...editor, busy: false, error: apiKeyErrorCode(error) } });
    }
  }

  const query = state.query.trim().toLocaleLowerCase(locale);
  const records = state.companies.filter((company) => company.name.toLocaleLowerCase(locale).includes(query)
    && (state.status === "all" || company.isActive === (state.status === "active")));
  const editor = state.editor;

  return <div className="mx-auto max-w-[1400px]">
    <section className="mb-7 flex flex-wrap items-start justify-between gap-5">
      <div className="max-w-3xl"><p className="mb-3 text-xs font-bold tracking-[.22em] text-primary">{text.eyebrow}</p>
        <h1 className="text-3xl font-semibold tracking-tight">{text.title}</h1><p className="mt-3 text-[13px] leading-7 text-muted-foreground">{text.description}</p>
        <Badge variant="outline" className="mt-3 gap-1.5 border-primary/15 text-primary"><ShieldCheck className="size-3" />{text.adminOnly}</Badge>
      </div>
      <div className="flex gap-2"><Button variant="outline" disabled={state.loading || !!editor?.busy} onClick={() => void load()}><RefreshCw className={state.loading ? "animate-spin" : ""} />{text.refresh}</Button>
        <Button disabled={state.loading || !!state.error} onClick={() => openEditor(null)}><Plus />{text.add}</Button></div>
    </section>
    {state.notice && <p role="status" className="mb-5 rounded-xl border border-primary/15 bg-primary/5 px-4 py-3 text-sm text-primary">{text.saved}</p>}
    {state.error ? <div role="alert" className="flex gap-3 rounded-xl border border-destructive/20 bg-destructive/5 p-5 text-sm leading-6 text-destructive"><CircleAlert className="mt-0.5 size-5 shrink-0" />{companyError(text, state.error)}</div>
      : state.loading ? <p role="status" className="flex items-center gap-3 rounded-xl border border-border bg-white p-8 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />{text.loading}</p>
      : <>
        <div className="mb-6 grid max-w-xl grid-cols-2 gap-4">{[[text.count, state.companies.length], [text.activeCount, state.companies.filter((company) => company.isActive).length]].map(([label, count]) =>
          <div key={label} className="rounded-xl border border-border bg-white p-5"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-2 text-3xl font-semibold">{count}</p></div>)}</div>
        <section className="overflow-hidden rounded-xl border border-border bg-white">
          <div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:justify-between"><div className="relative sm:max-w-md sm:flex-1"><Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input value={state.query} aria-label={text.search} placeholder={text.search} className="pl-9" onChange={(event) => store.setState({ query: event.target.value })} /></div>
            <Select value={state.status} onValueChange={(status) => store.setState({ status: status as CompanyScreenState["status"] })}><SelectTrigger aria-label={text.status} className="w-full sm:w-60"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">{text.allStatuses}</SelectItem><SelectItem value="active">{text.activeOnly}</SelectItem><SelectItem value="inactive">{text.inactiveOnly}</SelectItem></SelectContent></Select></div>
          {!state.companies.length ? <div className="px-6 py-14 text-center"><Building2 className="mx-auto mb-4 size-9 text-muted-foreground/50" /><h2 className="text-base font-semibold">{text.empty}</h2><p className="mx-auto mt-3 max-w-xl text-sm leading-7 text-muted-foreground">{text.emptyHelp}</p><Button variant="outline" className="mt-5" onClick={() => openEditor(null)}><Plus />{text.add}</Button></div>
            : !records.length ? <p role="status" className="p-10 text-center text-sm text-muted-foreground">{text.noMatches}</p>
            : <div className="overflow-x-auto"><table className="w-full min-w-[650px] text-left"><thead className="bg-muted/30 text-xs text-muted-foreground"><tr>{[text.name, text.status, text.updatedAt, text.actions].map((label) => <th key={label} scope="col" className="px-5 py-3 font-medium">{label}</th>)}</tr></thead>
              <tbody className="divide-y divide-border">{records.map((company) => <tr key={company.id}><th scope="row" className="max-w-md break-words px-5 py-5 text-sm font-medium">{company.name}</th>
                <td className="px-5 py-5"><Badge variant="outline" className={company.isActive ? "border-primary/15 text-primary" : "text-muted-foreground"}>{company.isActive ? text.active : text.inactive}</Badge></td>
                <td className="px-5 py-5 text-xs text-muted-foreground">{new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(company.updatedAt))}</td>
                <td className="px-5 py-5"><Button variant="outline" size="sm" onClick={() => openEditor(company)} aria-label={`${company.name} · ${text.edit}`}><Pencil className="size-3.5" />{text.edit}</Button></td></tr>)}</tbody></table></div>}
        </section><p className="mt-4 text-xs leading-6 text-muted-foreground">{text.archiveHelp}</p>
      </>}
    {editor && <Dialog open onOpenChange={(open) => { if (!open) closeEditor(); }}><DialogContent showCloseButton={false} className="max-h-[90dvh] gap-0 overflow-y-auto p-0 sm:max-w-xl" onEscapeKeyDown={(event) => { if (editor.busy) event.preventDefault(); }} onInteractOutside={(event) => { if (editor.busy) event.preventDefault(); }}>
      <DialogHeader className="border-b border-border p-6 pr-12"><DialogTitle>{editor.company ? text.editTitle : text.createTitle}</DialogTitle><DialogDescription className="text-sm leading-6">{text.nameHelp}</DialogDescription></DialogHeader>
      <Button variant="ghost" size="icon-sm" className="absolute right-3 top-3" aria-label={text.close} disabled={editor.busy} onClick={closeEditor}><X /></Button>
      <form onSubmit={save}><div className="space-y-5 p-6"><div><label htmlFor={formId("company-name")} className="mb-2 block text-sm font-medium">{text.name}</label><Input id={formId("company-name")} required autoComplete="organization" maxLength={160} value={editor.name} disabled={editor.busy} placeholder={text.namePlaceholder} onChange={(event) => changeEditor({ name: event.target.value })} /></div>
        {editor.company && <div><label htmlFor={formId("company-availability")} className="mb-2 block text-sm font-medium">{text.availability}</label><Select value={editor.isActive ? "active" : "inactive"} disabled={editor.busy} onValueChange={(value) => changeEditor({ isActive: value === "active" })}><SelectTrigger id={formId("company-availability")} className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="active">{text.available}</SelectItem><SelectItem value="inactive">{text.unavailable}</SelectItem></SelectContent></Select><p className="mt-3 text-xs leading-6 text-muted-foreground">{text.archiveHelp}</p></div>}
        {editor.error && <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-sm leading-6 text-destructive">{companyError(text, editor.error)}</p>}</div>
        <DialogFooter className="mx-0 mb-0 px-6 py-4"><Button type="button" variant="outline" disabled={editor.busy} onClick={closeEditor}>{text.cancel}</Button><Button type="submit" disabled={editor.busy || !editor.name.trim()}>{editor.busy && <LoaderCircle className="animate-spin" />}{editor.busy ? text.saving : text.save}</Button></DialogFooter>
      </form></DialogContent></Dialog>}
  </div>;
}

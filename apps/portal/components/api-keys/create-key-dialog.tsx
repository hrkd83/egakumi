"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Building2, KeyRound, LoaderCircle, RefreshCw, Users, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RELEASED_API_PRODUCT_ID, apiKeyClientTypeSchema, createApiKeySchema, type ApiKeyOverview, type ApiKeyRecord, type CreateApiKeyInput } from "@/lib/api-keys/contracts";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";
import { apiKeyErrorCode, issueApiKey, loadApiKeyCompanies, type ApiKeyCompany } from "./client";
import { prepareApiKeyIssueAttempt, type ApiKeyIssueAttempt } from "./create-key-form";
import { errorMessage } from "./presentation";

const clientTypes = apiKeyClientTypeSchema.options;
type Field = Exclude<keyof CreateApiKeyInput, "idempotencyKey">;

/** 必須・任意と入力エラーを同じ位置に示し、項目の読み取りを助けます。 */
function FormField({ id, label, requirement, help, error, children, className = "" }: {
  id: string; label: string; requirement: string; help?: string; error?: string; children: ReactNode; className?: string;
}) {
  return <div className={`min-w-0 ${className}`}>
    <label htmlFor={id} className="mb-2 flex flex-wrap items-center gap-2 text-sm font-medium">
      {label}<span className="rounded bg-muted px-1.5 py-0.5 text-xs font-normal text-muted-foreground">{requirement}</span>
    </label>
    {children}
    {help && <p id={`${id}-help`} className="mt-2 text-xs leading-5 text-muted-foreground">{help}</p>}
    {error && <p id={`${id}-error`} className="mt-2 text-xs leading-5 text-destructive">{error}</p>}
  </div>;
}

/** 所有グループと利用情報を確認し、リリース済みAPIに共通のキーを発行します。 */
export function CreateKeyDialog({ overview, initialGroupId, dictionary, locale, isPlatformAdministrator = false, onClose, onCreated }: {
  overview: ApiKeyOverview; initialGroupId: string; dictionary: Dictionary; locale: Locale; isPlatformAdministrator?: boolean;
  onClose: () => void; onCreated: (record: ApiKeyRecord) => void;
}) {
  const text = dictionary.apiKeys;
  const form = text.createForm;
  const [groupId, setGroupId] = useState(initialGroupId);
  const [displayName, setDisplayName] = useState("");
  const [clientType, setClientType] = useState<CreateApiKeyInput["clientType"] | "">("");
  const [systemName, setSystemName] = useState("");
  const [teamName, setTeamName] = useState("");
  const [companyId, setCompanyId] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [companies, setCompanies] = useState<ApiKeyCompany[]>([]);
  const [companiesLoading, setCompaniesLoading] = useState(true);
  const [companyError, setCompanyError] = useState<string | null>(null);
  const [companyRetry, setCompanyRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<Field, string>>>({});
  const submitting = useRef(false);
  const idempotency = useRef<ApiKeyIssueAttempt | null>(null);
  const group = overview.groups.find((item) => item.id === groupId);
  const product = group?.products.find((item) => item.id === RELEASED_API_PRODUCT_ID);
  const count = overview.keys.filter((key) => key.groupId === groupId && key.productId === RELEASED_API_PRODUCT_ID && key.state !== "deleted").length;
  const limitReached = Boolean(product && count >= product.maxSubscriptions);
  const validCompany = companies.some((company) => company.id === companyId);

  useEffect(() => {
    const controller = new AbortController();
    void loadApiKeyCompanies(controller.signal).then((result) => {
      if (!controller.signal.aborted) setCompanies(result);
    }).catch((failure) => {
      if (!controller.signal.aborted) setCompanyError(apiKeyErrorCode(failure));
    }).finally(() => { if (!controller.signal.aborted) setCompaniesLoading(false); });
    return () => controller.abort();
  }, [companyRetry]);

  /** 入力を保持したまま、対象項目と全体のエラー表示だけを解除します。 */
  function edited(field: Field) {
    setError(null);
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
  }

  /** 別タブで会社を登録した後も、発行フォームの入力を保持したまま選択肢だけを更新します。 */
  function reloadCompanies() {
    setCompaniesLoading(true);
    setCompanyError(null);
    setCompanyRetry((value) => value + 1);
  }

  /** 必須項目をその場で案内し、送信中の再クリックと失敗後の二重発行を防ぎます。 */
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const input = { groupId, displayName, clientType, systemName: systemName.trim() || undefined,
      teamName: teamName.trim() || undefined, companyId, contactEmail: contactEmail.trim() };
    const parsed = createApiKeySchema.safeParse({ ...input, idempotencyKey: "00000000-0000-4000-8000-000000000000" });
    if (!parsed.success) {
      const fields: Partial<Record<Field, string>> = {};
      for (const issue of parsed.error.issues) {
        const field = issue.path[0] as Field;
        if (field in form.fieldErrors) fields[field] = form.fieldErrors[field as keyof typeof form.fieldErrors];
      }
      setFieldErrors(fields); setError("InvalidForm");
      return;
    }
    if (!product || limitReached || !validCompany || companiesLoading || companyError) { setError("InvalidForm"); return; }
    const { idempotencyKey: ignored, ...validated } = parsed.data;
    void ignored;
    idempotency.current = prepareApiKeyIssueAttempt(validated, idempotency.current);
    submitting.current = true;
    setBusy(true); setError(null); setFieldErrors({});
    try { onCreated(await issueApiKey({ ...validated, idempotencyKey: idempotency.current.key })); }
    catch (failure) { setError(apiKeyErrorCode(failure)); }
    finally { submitting.current = false; setBusy(false); }
  }

  /** 補足とエラーを結び、支援技術にも同じ入力案内を渡します。 */
  function describedBy(field: Field, help = false) {
    return [help ? `create-key-${field}-help` : null, fieldErrors[field] ? `create-key-${field}-error` : null].filter(Boolean).join(" ") || undefined;
  }

  return <Dialog open onOpenChange={(open) => { if (!open && !submitting.current) onClose(); }}>
    <DialogContent showCloseButton={false} className="flex max-h-[92dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-[760px]" onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }} onInteractOutside={(event) => { if (busy) event.preventDefault(); }}>
      <DialogHeader className="shrink-0 border-b border-border px-6 py-5 pr-14 sm:px-8">
        <div className="mb-1 flex items-center gap-3"><span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/5 text-primary"><KeyRound className="size-5" /></span><DialogTitle className="text-xl font-semibold">{text.issue}</DialogTitle></div>
        <DialogDescription className="text-sm leading-6">{text.createDescription}</DialogDescription>
      </DialogHeader>
      <Button variant="ghost" size="icon-sm" className="absolute right-4 top-5" aria-label={text.close} disabled={busy} onClick={onClose}><X /></Button>
      <form noValidate onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 space-y-7 overflow-y-auto overscroll-contain px-6 py-6 sm:px-8">
          <section aria-labelledby="create-key-owner-title" className="space-y-4">
            <h3 id="create-key-owner-title" className="flex items-center gap-2 text-sm font-semibold"><Users className="size-4 text-primary" />{form.ownerSection}</h3>
            <FormField id="create-key-groupId" label={text.memberGroup} requirement={form.required} help={text.ownerHelp} error={fieldErrors.groupId}>
              <Select value={groupId} disabled={busy} onValueChange={(value) => { setGroupId(value); edited("groupId"); }}><SelectTrigger id="create-key-groupId" className="h-11! w-full" aria-required aria-invalid={Boolean(fieldErrors.groupId)} aria-describedby={describedBy("groupId", true)}><SelectValue placeholder={text.selectGroup} /></SelectTrigger><SelectContent>{overview.groups.map((item) => <SelectItem key={item.id} value={item.id}>{item.displayName}</SelectItem>)}</SelectContent></Select>
            </FormField>
            <div className="rounded-xl border border-primary/10 bg-primary/4 px-4 py-3"><p className="text-sm font-medium text-primary">{form.releasedAccess}</p><p className="mt-1 text-xs leading-5 text-muted-foreground">{form.releasedAccessHelp}</p></div>
            {limitReached && <p role="status" className="text-xs text-destructive">{text.capacityReached}</p>}
            {!product && <p role="status" className="text-xs leading-5 text-destructive">{form.noReleasedProduct}</p>}
          </section>
          <section aria-labelledby="create-key-usage-title" className="space-y-4 border-t border-border pt-6">
            <h3 id="create-key-usage-title" className="text-sm font-semibold">{form.usageSection}</h3>
            <div className="grid gap-x-5 gap-y-5 sm:grid-cols-2">
              <FormField id="create-key-displayName" label={text.usage} requirement={form.required} help={text.usageHelp} error={fieldErrors.displayName} className="sm:col-span-2"><Input id="create-key-displayName" autoComplete="off" maxLength={80} value={displayName} disabled={busy} required placeholder={text.usagePlaceholder} onChange={(event) => { setDisplayName(event.target.value); edited("displayName"); }} aria-invalid={Boolean(fieldErrors.displayName)} aria-describedby={describedBy("displayName", true)} className="h-11" /></FormField>
              <FormField id="create-key-clientType" label={form.clientType} requirement={form.required} help={form.clientTypeHelp} error={fieldErrors.clientType}><Select value={clientType} disabled={busy} onValueChange={(value) => { if (clientTypes.includes(value as typeof clientTypes[number])) { setClientType(value as CreateApiKeyInput["clientType"]); edited("clientType"); } }}><SelectTrigger id="create-key-clientType" className="h-11! w-full" aria-required aria-invalid={Boolean(fieldErrors.clientType)} aria-describedby={describedBy("clientType", true)}><SelectValue placeholder={form.selectClientType} /></SelectTrigger><SelectContent>{clientTypes.map((type) => <SelectItem key={type} value={type}>{form.clientTypes[type]}</SelectItem>)}</SelectContent></Select></FormField>
              <FormField id="create-key-systemName" label={form.systemName} requirement={form.optional} error={fieldErrors.systemName}><Input id="create-key-systemName" autoComplete="off" maxLength={120} value={systemName} disabled={busy} placeholder={form.systemNamePlaceholder} onChange={(event) => { setSystemName(event.target.value); edited("systemName"); }} aria-invalid={Boolean(fieldErrors.systemName)} aria-describedby={describedBy("systemName")} className="h-11" /></FormField>
              <FormField id="create-key-teamName" label={form.teamName} requirement={form.optional} error={fieldErrors.teamName} className="sm:col-span-2"><Input id="create-key-teamName" autoComplete="off" maxLength={120} value={teamName} disabled={busy} placeholder={form.teamNamePlaceholder} onChange={(event) => { setTeamName(event.target.value); edited("teamName"); }} aria-invalid={Boolean(fieldErrors.teamName)} aria-describedby={describedBy("teamName")} className="h-11" /></FormField>
            </div>
          </section>
          <section aria-labelledby="create-key-contact-title" className="space-y-4 border-t border-border pt-6">
            <h3 id="create-key-contact-title" className="flex items-center gap-2 text-sm font-semibold"><Building2 className="size-4 text-primary" />{form.contactSection}</h3>
            <div className="grid gap-x-5 gap-y-5 sm:grid-cols-2">
              <FormField id="create-key-companyId" label={form.companyName} requirement={form.required} error={fieldErrors.companyId}>
                <Select value={companyId} disabled={busy || companiesLoading || Boolean(companyError) || !companies.length} onValueChange={(value) => { setCompanyId(value); edited("companyId"); }}><SelectTrigger id="create-key-companyId" className="h-11! w-full" aria-required aria-invalid={Boolean(fieldErrors.companyId)} aria-describedby={describedBy("companyId")}><SelectValue placeholder={companiesLoading ? form.loadingCompanies : form.selectCompany} /></SelectTrigger><SelectContent>{companies.map((company) => <SelectItem key={company.id} value={company.id}>{company.name}</SelectItem>)}</SelectContent></Select>
              </FormField>
              <FormField id="create-key-contactEmail" label={form.contactEmail} requirement={form.required} help={form.contactEmailHelp} error={fieldErrors.contactEmail}><Input id="create-key-contactEmail" type="email" autoComplete="email" maxLength={254} value={contactEmail} disabled={busy} required placeholder={form.contactEmailPlaceholder} onChange={(event) => { setContactEmail(event.target.value); edited("contactEmail"); }} aria-invalid={Boolean(fieldErrors.contactEmail)} aria-describedby={describedBy("contactEmail", true)} className="h-11" /></FormField>
            </div>
            {companiesLoading ? <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="size-3.5 animate-spin" />{form.loadingCompanies}</p>
              : companyError ? <div role="alert" className="rounded-xl border border-destructive/15 bg-destructive/3 p-3"><p className="text-xs leading-5 text-destructive">{form.companiesUnavailable}</p><Button type="button" variant="outline" size="sm" className="mt-2" disabled={busy} onClick={reloadCompanies}><RefreshCw />{form.reloadCompanies}</Button></div>
                : !companies.length && <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4"><p className="text-xs leading-6 text-amber-950">{isPlatformAdministrator ? form.noCompaniesAdmin : form.noCompanies}</p><div className="mt-3 flex flex-wrap items-center gap-4">{isPlatformAdministrator && <Link href={`/${locale}/company-administration`} target="_blank" rel="noopener noreferrer" className="inline-flex text-xs font-medium text-primary underline underline-offset-4">{form.manageCompanies}</Link>}<Button type="button" variant="outline" size="sm" disabled={busy} onClick={reloadCompanies}><RefreshCw />{form.reloadCompanies}</Button></div></div>}
            {!companiesLoading && !companyError && companies.length > 0 && <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={reloadCompanies}><RefreshCw />{form.reloadCompanies}</Button>}
            <p className="text-xs leading-5 text-muted-foreground">{form.companyOwnershipHelp}</p>
          </section>
          <div className="rounded-xl border border-border bg-muted/20 px-4 py-3"><p className="text-xs font-medium">{text.budgetPolicy}：{text.budgetModes.unlimited}</p><p className="mt-1 text-xs leading-5 text-muted-foreground">{text.unlimitedNotice}</p></div>
          {error && <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-sm leading-6 text-destructive">{error === "InvalidForm" ? text.invalidForm : errorMessage(dictionary, error)}</p>}
        </div>
        <DialogFooter className="m-0! shrink-0 items-center px-6 py-4 sm:justify-between sm:px-8"><p className="hidden max-w-xs text-xs leading-5 text-muted-foreground sm:block">{text.createNotice}</p><div className="flex w-full flex-col-reverse gap-2 sm:w-auto sm:flex-row"><Button type="button" variant="outline" disabled={busy} onClick={onClose}>{text.cancel}</Button><Button type="submit" disabled={busy || !product || !displayName.trim() || !clientType || !contactEmail.trim() || !validCompany || companiesLoading || Boolean(companyError) || limitReached}>{busy ? <LoaderCircle className="animate-spin" /> : <KeyRound />}{busy ? text.working : text.issue}</Button></div></DialogFooter>
      </form>
    </DialogContent>
  </Dialog>;
}

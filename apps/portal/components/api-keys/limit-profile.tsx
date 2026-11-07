"use client";

import { useFieldIds } from "@/lib/use-field-ids";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ChevronDown, LoaderCircle, RefreshCw, RotateCcw, Save, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { unlimitedApiKeyLimits, type ApiKeyLimitProfile } from "@/lib/api-keys/limit-contracts";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import { apiKeyErrorCode } from "./client";
import { errorMessage } from "./presentation";
import { limitDraftFromProfile, limitsFromDraft, loadApiKeyLimits, saveApiKeyLimits, type ApiKeyLimitDraft } from "./limit-client";

/** 初期値を無制限とし、購読ごとの保護上限を他の管理者と競合しないよう編集します。 */
export function ApiKeyLimitProfileEditor({ id, dictionary, disabled, onSavingChange }: {
  id: string; dictionary: Dictionary; disabled: boolean; onSavingChange: (saving: boolean) => void;
}) {
  const formId = useFieldIds();
  const text = dictionary.apiKeys.limits;
  const [expanded, setExpanded] = useState(false);
  const [profile, setProfile] = useState<ApiKeyLimitProfile | null>(null);
  const [draft, setDraft] = useState<ApiKeyLimitDraft>(() => limitDraftFromProfile(unlimitedApiKeyLimits()));
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const busy = disabled || loading || saving;
  const allUnlimited = !draft.requestEnabled && !draft.tokenRateEnabled && !draft.quotaEnabled;
  const dirty = profile !== null && JSON.stringify(draft) !== JSON.stringify(limitDraftFromProfile(profile));
  const conflict = error === "LimitVersionConflict";

  /** 最新値の読み込みは明示操作で行い、編集中の変更を通信再試行で勝手に置き換えません。 */
  const reload = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true); setError(null); setSaved(false);
    try {
      const result = await loadApiKeyLimits(id, current.signal);
      if (!current.signal.aborted) { setProfile(result); setDraft(limitDraftFromProfile(result)); }
    } catch (failure) {
      if (!current.signal.aborted) { setProfile(null); setError(apiKeyErrorCode(failure)); }
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }, [id]);

  useEffect(() => () => {
    controller.current?.abort();
    onSavingChange(false);
  }, [onSavingChange]);

  /** 設定を開く操作で読み込みを始め、閉じる操作では不要な通信を中断します。 */
  function toggle() {
    if (saving) return;
    if (!expanded) void reload();
    else controller.current?.abort();
    setExpanded((value) => !value);
  }

  /** 保存値とは独立した入力欄だけを変更し、保存前に上限を適用した表示にはしません。 */
  function change<K extends keyof ApiKeyLimitDraft>(name: K, value: ApiKeyLimitDraft[K]) {
    setDraft((previous) => ({ ...previous, [name]: value }));
    setSaved(false);
    if (!conflict) setError(null);
  }

  /** 全項目を無制限へ戻す操作も、保存ボタンを押すまではサーバーへ送信しません。 */
  function resetUnlimited() {
    setDraft(limitDraftFromProfile(unlimitedApiKeyLimits()));
    setSaved(false);
    if (!conflict) setError(null);
  }

  /** 読み込んだ版が一致するときだけ、検証済みの制限値を保存します。 */
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !profile || conflict) return;
    let values;
    try { values = limitsFromDraft(draft); }
    catch (failure) { setError(apiKeyErrorCode(failure)); return; }
    const current = new AbortController();
    controller.current = current;
    setSaving(true); onSavingChange(true); setSaved(false); setError(null);
    try {
      const result = await saveApiKeyLimits(id, { ...values, expectedVersion: profile.version }, current.signal);
      if (!current.signal.aborted) { setProfile(result); setDraft(limitDraftFromProfile(result)); setSaved(true); }
    } catch (failure) {
      if (!current.signal.aborted) setError(apiKeyErrorCode(failure));
    } finally {
      if (!current.signal.aborted) { setSaving(false); onSavingChange(false); }
    }
  }

  /** 各項目を無制限か制限ありに切り替える、キーボード操作可能な選択欄です。 */
  function mode(name: "requestEnabled" | "tokenRateEnabled" | "quotaEnabled", labelId: string) {
    return <Select value={draft[name] ? "limited" : "unlimited"} disabled={busy || !profile} onValueChange={(value) => change(name, value === "limited")}>
      <SelectTrigger aria-labelledby={labelId} className="h-8 w-32 bg-white text-xs"><SelectValue /></SelectTrigger>
      <SelectContent><SelectItem value="unlimited">{text.unlimited}</SelectItem><SelectItem value="limited">{text.limited}</SelectItem></SelectContent>
    </Select>;
  }

  /** 入力中も上下限を表示し、数値の解釈は送信前のZod検証へ集約します。 */
  function numberField(name: "calls" | "seconds" | "tokensPerMinute" | "quotaTokens", label: string, maximum: number) {
    const fieldId = formId(`limit-${id}-${name}`);
    const range = `1–${maximum.toLocaleString("en-US")}`;
    return <div className="min-w-0 flex-1"><label htmlFor={fieldId} className="mb-1.5 block text-xs text-muted-foreground">{label}</label>
      <Input id={fieldId} type="text" inputMode="numeric" pattern="[0-9]+" maxLength={10} autoComplete="off" required disabled={busy || !profile}
        value={draft[name]} placeholder={range} aria-describedby={formId(`limit-${id}-${name}-range`)}
        onChange={(event) => change(name, event.target.value)} className="h-9 bg-white text-xs" />
      <span id={formId(`limit-${id}-${name}-range`)} className="mt-1.5 block text-xs leading-4 text-muted-foreground tabular-nums">{range}</span>
    </div>;
  }

  return <section className="rounded-xl border border-border">
    <button type="button" className="flex w-full items-start gap-3 p-4 text-left" aria-expanded={expanded} aria-controls={formId(`limits-${id}`)} disabled={disabled || saving}
      onClick={toggle}>
      <SlidersHorizontal className="mt-0.5 size-4 shrink-0 text-primary" /><span className="flex-1"><span className="block text-xs font-semibold">{text.title}</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">{text.description}</span></span><ChevronDown className={`mt-0.5 size-4 shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`} />
    </button>
    {expanded && <div id={formId(`limits-${id}`)} className="border-t border-border p-4">
      {loading ? <p role="status" className="flex items-center gap-2 py-3 text-xs text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />{text.loading}</p>
        : !profile ? <div className="space-y-3"><p role="alert" className="text-xs leading-6 text-destructive">{errorMessage(dictionary, error ?? "ServiceUnavailable")}</p><Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => void reload()}><RefreshCw />{text.reload}</Button></div>
          : <form onSubmit={submit} className="space-y-4">
            <div className="flex items-center justify-between gap-3"><span className="rounded-full bg-muted px-2.5 py-1 text-xs font-medium">{allUnlimited ? text.allUnlimited : text.hasLimits}{dirty ? ` · ${text.unsaved}` : ""}</span><Button type="button" variant="ghost" size="sm" disabled={busy || allUnlimited} onClick={resetUnlimited}><RotateCcw />{text.reset}</Button></div>
            <fieldset disabled={busy} className="space-y-4">
              <div className="rounded-lg bg-muted/25 p-3"><div className="flex items-center justify-between gap-3"><h4 id={formId(`limit-${id}-requests`)} className="text-xs font-medium">{text.requestRate}</h4>{mode("requestEnabled", formId(`limit-${id}-requests`))}</div><p className="mt-2 text-xs leading-5 text-muted-foreground">{text.requestHelp}</p>{draft.requestEnabled && <div className="mt-3 flex flex-wrap gap-3">{numberField("calls", text.calls, 1_000_000)}{numberField("seconds", text.seconds, 300)}</div>}</div>
              <div className="rounded-lg bg-muted/25 p-3"><div className="flex items-center justify-between gap-3"><h4 id={formId(`limit-${id}-tpm`)} className="text-xs font-medium">{text.tokenRate}</h4>{mode("tokenRateEnabled", formId(`limit-${id}-tpm`))}</div><p className="mt-2 text-xs leading-5 text-muted-foreground">{text.tokenHelp}</p>{draft.tokenRateEnabled && <div className="mt-3">{numberField("tokensPerMinute", text.tokensPerMinute, 2_147_483_647)}</div>}</div>
              <div className="rounded-lg bg-muted/25 p-3"><div className="flex items-center justify-between gap-3"><h4 id={formId(`limit-${id}-quota`)} className="text-xs font-medium">{text.tokenQuota}</h4>{mode("quotaEnabled", formId(`limit-${id}-quota`))}</div><p className="mt-2 text-xs leading-5 text-muted-foreground">{text.quotaHelp}</p>{draft.quotaEnabled && <div className="mt-3 flex flex-wrap gap-3">{numberField("quotaTokens", text.totalTokens, 2_147_483_647)}<div className="min-w-36 flex-1"><label htmlFor={formId(`limit-${id}-period`)} className="mb-1.5 block text-xs text-muted-foreground">{text.period}</label><Select value={draft.quotaPeriod} disabled={busy} onValueChange={(value) => change("quotaPeriod", value as ApiKeyLimitDraft["quotaPeriod"])}><SelectTrigger id={formId(`limit-${id}-period`)} className="h-9 w-full bg-white text-xs"><SelectValue /></SelectTrigger><SelectContent>{(["Hourly", "Daily", "Weekly", "Monthly", "Yearly"] as const).map((period) => <SelectItem key={period} value={period}>{text.periods[period]}</SelectItem>)}</SelectContent></Select></div></div>}<p className="mt-2 text-xs leading-5 text-muted-foreground">{text.resetTime}</p></div>
            </fieldset>
            <p className="text-xs leading-6 text-muted-foreground">{text.scopeNotice}</p>
            <p className="rounded-lg bg-primary/4 p-3 text-xs leading-6 text-muted-foreground">{text.activationNotice}</p>
            {error && <div role="alert" className="space-y-2 rounded-lg bg-destructive/5 p-3 text-xs leading-6 text-destructive"><p>{errorMessage(dictionary, error)}</p>{conflict && <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void reload()}><RefreshCw />{text.reload}</Button>}</div>}
            {saved && <p role="status" className="text-xs leading-6 text-primary">{text.saved}</p>}
            <div className="flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{text.version.replace("{version}", String(profile.version))}</span><Button type="submit" size="sm" disabled={busy || !dirty || conflict}>{saving ? <LoaderCircle className="animate-spin" /> : <Save />}{saving ? text.saving : text.save}</Button></div>
          </form>}
    </div>}
  </section>;
}

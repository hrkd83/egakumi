"use client";

import { LoaderCircle, LogOut, UserRound, X } from "lucide-react";
import { useState } from "react";
import { authClient } from "@/lib/auth-client";
import type { PortalAccount } from "@/lib/auth-session";
import type { Locale } from "@/lib/i18n/config";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";

/**
 * サーバーで検証済みの本人情報と、セッションを終了する操作を表示します。
 * @param props - 公開してよい名前・メール、表示言語、文言。
 * @returns アカウントボタンとログアウトのダイアログ。
 */
export function AccountMenu({ account, locale, dictionary }: { account: PortalAccount; locale: Locale; dictionary: Dictionary }) {
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const text = dictionary.auth;

  /** SQLのセッションを失効させ、画面のメモリーに残る利用者別の表示状態も破棄します。 */
  async function signOut() {
    setPending(true);
    setFailed(false);
    try {
      const result = await authClient.signOut();
      if (result.error) throw new Error("SIGN_OUT_FAILED");
      window.location.replace(`/${locale}/login`);
    } catch {
      setFailed(true);
      setPending(false);
    }
  }

  return <Dialog>
    <DialogTrigger asChild><Button variant="ghost" size="icon" aria-label={dictionary.common.account} className="rounded-full bg-[#eaf2ed] text-primary"><UserRound className="size-4" /></Button></DialogTrigger>
    <DialogContent className="sm:max-w-sm" showCloseButton={false}>
      <DialogClose asChild><Button variant="ghost" size="icon-sm" className="absolute right-3 top-3" aria-label={dictionary.common.close}><X className="size-4" /></Button></DialogClose>
      <DialogTitle>{text.account}</DialogTitle>
      <DialogDescription>{text.signedInWithMicrosoft}</DialogDescription>
      <div className="rounded-xl border border-border bg-muted/40 p-4"><p className="break-words font-medium">{account.name}</p>{account.email && <p className="mt-2 break-all text-sm text-muted-foreground">{account.email}</p>}</div>
      {failed && <p role="alert" className="text-sm text-destructive">{text.signOutError}</p>}
      <Button variant="outline" onClick={signOut} disabled={pending}>{pending ? <LoaderCircle className="size-4 animate-spin" /> : <LogOut className="size-4" />}{pending ? text.signingOut : text.signOut}</Button>
    </DialogContent>
  </Dialog>;
}

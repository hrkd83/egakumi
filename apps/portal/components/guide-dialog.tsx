"use client";

import { ArrowRight, BookOpen, Check, Code2, Compass, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import type { Dictionary } from "@/lib/i18n/dictionaries";

/**
 * カタログから利用開始までの流れを、サンプルのガイドとして表示します。
 * @param props - 選択中の言語の表示文言と、サイドバー用の表示指定。
 * @returns 説明用ダイアログと、その表示ボタン。
 */
export function GuideDialog({ dictionary, compact = false }: { dictionary: Dictionary; compact?: boolean }) {
  const guide = dictionary.catalog.guide;
  const steps = [
    { icon: Compass, title: guide.step1, body: guide.body1 },
    { icon: Code2, title: guide.step2, body: guide.body2 },
    { icon: Check, title: guide.step3, body: guide.body3 },
  ];

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant={compact ? "ghost" : "outline"} className={compact ? "w-full justify-between px-0 text-primary hover:bg-transparent" : "h-10 gap-2 bg-white"}>
          <span className="flex items-center gap-2"><BookOpen className="size-4" />{guide.button}</span>
          {compact && <ArrowRight className="size-4" />}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl" showCloseButton={false}>
        <DialogClose asChild><Button variant="ghost" size="icon-sm" className="absolute right-3 top-3" aria-label={dictionary.common.close}><X className="size-4" /></Button></DialogClose>
        <DialogHeader className="gap-3">
          <span className="mb-2 flex size-11 items-center justify-center rounded-xl bg-primary/8 text-primary"><BookOpen className="size-5" /></span>
          <DialogTitle className="text-xl">{guide.title}</DialogTitle>
          <DialogDescription>{guide.description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-6 py-5">
          {steps.map(({ icon: Icon, title, body }, index) => (
            <div className="flex gap-4" key={title}>
              <span className="flex size-9 shrink-0 items-center justify-center rounded-full border border-border text-primary"><Icon className="size-4" /></span>
              <div><h3 className="text-sm font-semibold"><span className="mr-2 text-muted-foreground">0{index + 1}</span>{title}</h3><p className="mt-1.5 text-sm leading-7 text-muted-foreground">{body}</p></div>
            </div>
          ))}
        </div>
        <p className="rounded-lg bg-muted p-3 text-xs leading-6 text-muted-foreground">{guide.note}</p>
      </DialogContent>
    </Dialog>
  );
}

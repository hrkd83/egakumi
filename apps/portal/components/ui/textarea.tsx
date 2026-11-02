import * as React from "react"
import { cn } from "cn"

/** 単一行の入力欄と同じ背景・枠線・状態表示を持つ複数行の入力欄です。 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-16 w-full rounded-lg border border-input bg-muted/60 px-2.5 py-2 text-base transition-colors outline-none placeholder:text-muted-foreground hover:border-primary/70 focus-visible:border-primary focus-visible:ring-3 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:border-border disabled:bg-muted disabled:text-muted-foreground disabled:opacity-100 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-muted dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }

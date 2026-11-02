import * as React from "react"
import { cn } from "cn"

/** 背景と枠線で入力範囲を明示し、フォーカス・無効状態を区別する入力欄です。 */
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "h-8 w-full min-w-0 rounded-lg border border-input bg-muted/60 px-2.5 py-1 text-base transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground hover:border-primary/70 focus-visible:border-primary focus-visible:ring-3 focus-visible:ring-ring/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-border disabled:bg-muted disabled:text-muted-foreground disabled:opacity-100 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-muted dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Input }

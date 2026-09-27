import * as React from "react"
import { cn } from "cn"

type ProgressProps = React.ComponentProps<"div"> & {
  value: number
  label: string
}

function Progress({ className, value, label, ...props }: ProgressProps) {
  const clamped = Math.max(0, Math.min(100, Math.round(value)))
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      data-slot="progress"
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}
      {...props}
    >
      <div className="h-full bg-primary transition-[width]" style={{ width: `${clamped}%` }} />
    </div>
  )
}

export { Progress }

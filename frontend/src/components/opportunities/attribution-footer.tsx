import { ExternalLinkIcon } from "lucide-react";

import type { Attribution } from "@/lib/opportunities/api";
import { cn } from "@/lib/utils";

/** SPEC 11 wording; the API sends the same string on every record. */
export const DISCLAIMER = "Verify every detail on the official portal before submitting.";

export function SourceLink({
  attribution,
  url,
  className,
  iconOnly = false,
}: {
  attribution: Attribution;
  /** Record-specific URL when different from the attribution's portal link. */
  url?: string | null;
  className?: string;
  iconOnly?: boolean;
}) {
  const href = url ?? attribution.source_url;
  const label = attribution.source_name || attribution.source_id;
  if (!href) return <span className={className}>{label}</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-center gap-1 rounded-sm underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        className,
      )}
      aria-label={iconOnly ? `${label} (opens the official portal)` : undefined}
    >
      {iconOnly ? null : <span>{label}</span>}
      <ExternalLinkIcon className="size-3.5 shrink-0" aria-hidden="true" />
      {iconOnly ? <span className="sr-only">{label}</span> : <span className="sr-only">(opens the official portal)</span>}
    </a>
  );
}

export function DisclaimerNote({ text = DISCLAIMER, className }: { text?: string; className?: string }) {
  return (
    <p role="note" data-testid="disclaimer" className={cn("text-xs text-muted-foreground", className)}>
      {text}
    </p>
  );
}

/** Source attribution + official link + disclaimer, shown on every record view (SPEC 11). */
export function AttributionFooter({
  attribution,
  disclaimer = DISCLAIMER,
  sourceUrl,
  className,
}: {
  attribution: Attribution;
  disclaimer?: string;
  sourceUrl?: string | null;
  className?: string;
}) {
  return (
    <footer
      data-testid="attribution-footer"
      className={cn("flex flex-col gap-1 rounded-xl border bg-muted/40 px-4 py-3 text-sm", className)}
    >
      <p className="flex flex-wrap items-center gap-x-2">
        <span className="text-muted-foreground">Source:</span>
        <SourceLink attribution={attribution} url={sourceUrl} className="font-medium" />
        {attribution.source_url && sourceUrl && sourceUrl !== attribution.source_url ? (
          <>
            <span aria-hidden="true" className="text-muted-foreground">
              ·
            </span>
            <a
              href={attribution.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-muted-foreground underline-offset-4 hover:underline"
            >
              Portal home
            </a>
          </>
        ) : null}
      </p>
      <DisclaimerNote text={disclaimer} />
    </footer>
  );
}

"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { errorMessage } from "@/lib/api/browser";
import { disablePush, enablePush, pushState, type PushStatus } from "@/lib/notifications/push";
import { ApiError } from "@/lib/opportunities/api";
import { cn } from "@/lib/utils";

/**
 * Web push opt-in (SPEC 7). The button states are: this browser cannot do it,
 * the deployment has no VAPID key, permission is blocked, off, on. Only "off"
 * and "on" are actionable; the rest explain themselves rather than failing on
 * click.
 */
export function PushOptIn({ className }: { className?: string }) {
  const [status, setStatus] = React.useState<PushStatus | null>(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    pushState()
      .then((next) => {
        if (!cancelled) setStatus(next);
      })
      .catch(() => {
        if (!cancelled) setStatus({ kind: "unsupported", reason: "Web push could not be checked." });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = async () => {
    if (!status || busy) return;
    setBusy(true);
    try {
      const next = status.kind === "on" ? await disablePush() : await enablePush();
      setStatus(next);
      if (next.kind === "on") toast.success("This browser will receive web push");
      else if (next.kind === "off") toast.success("Web push turned off for this browser");
      else if (next.kind === "denied") toast.info(next.reason);
    } catch (caught) {
      toast.error(
        caught instanceof ApiError
          ? errorMessage(caught.body, "Could not save the subscription")
          : caught instanceof Error
            ? caught.message
            : "Could not subscribe this browser",
      );
    } finally {
      setBusy(false);
    }
  };

  const actionable = status?.kind === "on" || status?.kind === "off";
  const label = status?.kind === "on" ? "Turn off web push" : "Enable web push";

  return (
    <div className={cn("grid gap-1.5", className)} data-testid="push-opt-in" data-state={status?.kind ?? "loading"}>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant={status?.kind === "on" ? "outline" : "default"}
          size="sm"
          onClick={toggle}
          disabled={!actionable || busy}
        >
          {busy ? "Working…" : label}
        </Button>
        {status?.kind === "on" ? (
          <span className="text-xs text-muted-foreground">This browser is subscribed.</span>
        ) : null}
      </div>
      {status && status.kind !== "on" && status.kind !== "off" ? (
        <p className="text-xs text-muted-foreground">{status.reason}</p>
      ) : null}
      {status?.kind === "off" ? (
        <p className="text-xs text-muted-foreground">
          Push reaches this browser only; each device opts in separately.
        </p>
      ) : null}
    </div>
  );
}

"use client";

import { XIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { describeError, ErrorNote, StatusBadge } from "@/components/admin/common";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import {
  getTenant,
  grantSupportAccess,
  updateTenant,
  type Plan,
  type TenantDetail,
} from "@/lib/admin/api";
import { formatPeriod, formatTimestamp, formatTokens, formatUsd } from "@/lib/admin/format";

const PLANS: Plan[] = ["free", "pro", "enterprise"];
/** SPEC 3: support access is time-boxed, never standing. */
const WINDOWS = [15, 30, 60, 120, 240];

export type TenantDrawerProps = {
  tenantId: string;
  period: string;
  onClose: () => void;
  onChanged: () => void;
};

/**
 * Tenant detail: plan editor (SPEC 3 "manage tenants, plans") and the
 * support-access dialog, which refuses to submit without a reason because the
 * reason is what lands in the audit_log row.
 */
export function TenantDrawer({ tenantId, period, onClose, onChanged }: TenantDrawerProps) {
  const [detail, setDetail] = React.useState<TenantDetail | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [plan, setPlan] = React.useState<Plan | "">("");
  const [saving, setSaving] = React.useState(false);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [reason, setReason] = React.useState("");
  const [minutes, setMinutes] = React.useState(60);
  const [granting, setGranting] = React.useState(false);
  const [reload, setReload] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getTenant(tenantId, period, controller.signal)
      .then((result) => {
        setDetail(result);
        setPlan(result.tenant.plan);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(describeError(err, "Could not load this tenant."));
      });
    return () => controller.abort();
  }, [tenantId, period, reload]);

  const savePlan = async () => {
    if (!detail || !plan || plan === detail.tenant.plan) return;
    setSaving(true);
    try {
      await updateTenant(tenantId, { plan });
      toast.success(`${detail.tenant.slug} is now on the ${plan} plan`);
      setReload((n) => n + 1);
      onChanged();
    } catch (err) {
      toast.error(describeError(err, "Could not change the plan."));
    } finally {
      setSaving(false);
    }
  };

  const submitSupportAccess = async (event: React.FormEvent) => {
    event.preventDefault();
    const clean = reason.trim();
    if (clean.length < 3) return;
    setGranting(true);
    try {
      const granted = await grantSupportAccess(tenantId, { reason: clean, minutes });
      toast.success(
        `Support access open until ${formatTimestamp(granted.grant.expires_at)} — logged`,
      );
      setDialogOpen(false);
      setReason("");
      setReload((n) => n + 1);
    } catch (err) {
      toast.error(describeError(err, "Could not open support access."));
    } finally {
      setGranting(false);
    }
  };

  return (
    <aside
      aria-label="Tenant detail"
      data-testid="tenant-drawer"
      className="flex w-full max-w-sm shrink-0 flex-col gap-4 rounded-xl border bg-muted/20 p-4"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate font-heading text-base font-medium">
            {detail ? detail.tenant.name : "Loading…"}
          </h2>
          {detail ? (
            <p className="truncate text-xs text-muted-foreground">{detail.tenant.slug}</p>
          ) : null}
        </div>
        <Button type="button" variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close">
          <XIcon />
        </Button>
      </div>

      {error ? <ErrorNote message={error} /> : null}

      {detail ? (
        <>
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <dt className="text-muted-foreground">Region</dt>
            <dd className="uppercase">{detail.tenant.region}</dd>
            <dt className="text-muted-foreground">Residency</dt>
            <dd className="uppercase">{detail.tenant.data_residency}</dd>
            <dt className="text-muted-foreground">Members</dt>
            <dd className="tabular-nums">{detail.tenant.member_count}</dd>
            <dt className="text-muted-foreground">Profiles</dt>
            <dd className="tabular-nums">{detail.tenant.profile_count}</dd>
            <dt className="text-muted-foreground">Created</dt>
            <dd>{formatTimestamp(detail.tenant.created_at)}</dd>
            <dt className="text-muted-foreground">Internal</dt>
            <dd>{detail.tenant.is_internal ? "Yes" : "No"}</dd>
          </dl>

          <section className="grid gap-2" aria-label="Plan">
            <Label htmlFor="tenant-plan">Plan</Label>
            <div className="flex items-center gap-2">
              <NativeSelect
                id="tenant-plan"
                value={plan}
                onChange={(event) => setPlan(event.target.value as Plan)}
              >
                {PLANS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </NativeSelect>
              <Button
                type="button"
                size="sm"
                disabled={saving || !plan || plan === detail.tenant.plan}
                onClick={savePlan}
              >
                {saving ? "Saving…" : "Save"}
              </Button>
            </div>
            <ul className="grid gap-0.5 text-xs text-muted-foreground">
              {Object.entries(detail.plan_limits).map(([resource, limit]) => (
                <li key={resource} className="flex justify-between gap-2">
                  <span>{resource.replace(/_/g, " ")}</span>
                  <span className="tabular-nums">{limit === null ? "unlimited" : limit}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="grid gap-1 text-sm" aria-label="Usage">
            <h3 className="font-medium">Usage · {formatPeriod(detail.period)}</h3>
            <div className="flex justify-between">
              <span className="text-muted-foreground">LLM cost</span>
              <span className="tabular-nums" data-testid="drawer-cost">
                {formatUsd(detail.usage.cost_microusd)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Tokens in / out</span>
              <span className="tabular-nums">
                {formatTokens(detail.usage.tokens_in)} / {formatTokens(detail.usage.tokens_out)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Agent runs</span>
              <span className="tabular-nums">{detail.usage.agent_runs}</span>
            </div>
          </section>

          <section className="grid gap-1 text-sm" aria-label="Billing">
            <h3 className="font-medium">Billing</h3>
            {detail.billing ? (
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">{detail.billing.provider}</span>
                <StatusBadge
                  status={detail.billing.status === "active" ? "ok" : detail.billing.status}
                />
              </div>
            ) : (
              <p className="text-muted-foreground">No provider customer yet.</p>
            )}
          </section>

          <section className="grid gap-2 text-sm" aria-label="Support access">
            <h3 className="font-medium">Support access</h3>
            <p className="text-xs text-muted-foreground">
              Platform admins cannot read tenant data without a logged, time-boxed grant.
            </p>
            {detail.support_access ? (
              <p className="text-xs" data-testid="active-grant">
                Active until {formatTimestamp(detail.support_access.expires_at)} —{" "}
                {detail.support_access.reason}
              </p>
            ) : null}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setDialogOpen(true)}
              data-testid="open-support-access"
            >
              Request support access
            </Button>
          </section>
        </>
      ) : null}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <form onSubmit={submitSupportAccess} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Support access</DialogTitle>
              <DialogDescription>
                The reason and your identity are written to this tenant&apos;s audit log before the
                session opens. Access expires by itself.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-1.5">
              <Label htmlFor="support-reason">Reason</Label>
              <Textarea
                id="support-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="e.g. ticket #918: export fails with a 500"
                required
                minLength={3}
                autoFocus
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="support-minutes">Window</Label>
              <NativeSelect
                id="support-minutes"
                value={minutes}
                onChange={(event) => setMinutes(Number(event.target.value))}
              >
                {WINDOWS.map((value) => (
                  <option key={value} value={value}>
                    {value} minutes
                  </option>
                ))}
              </NativeSelect>
            </div>
            <DialogFooter showCloseButton>
              <Button type="submit" disabled={granting || reason.trim().length < 3}>
                {granting ? "Opening…" : "Open access"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </aside>
  );
}

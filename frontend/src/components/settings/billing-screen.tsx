"use client";

import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorMessage } from "@/lib/api/browser";
import { formatMoney } from "@/lib/money";
import { ApiError } from "@/lib/opportunities/api";
import { getBilling, startCheckout, type Billing } from "@/lib/settings/api";
import {
  PAID_PLANS,
  PLANS,
  PLAN_COMPARISON,
  PLAN_LABELS,
  PROVIDER_LABELS,
  gstErrors,
  needsGst,
  planAction,
  usageBars,
  type Plan,
} from "@/lib/settings/plans";

/** Stripe and Razorpay both bill in the currency's minor unit (cents, paise). */
const fromMinor = (amount: number | null) => (amount === null ? null : amount / 100);

function GstFields({
  gst,
  onChange,
  errors,
}: {
  gst: { gstin: string; place_of_supply: string; legal_name: string };
  onChange: (next: { gstin: string; place_of_supply: string; legal_name: string }) => void;
  errors: Record<string, string>;
}) {
  return (
    <fieldset className="grid gap-3 rounded-lg border p-3 sm:grid-cols-3" data-testid="gst-fields">
      <legend className="px-1 text-sm font-medium">GST details (India)</legend>
      <div className="grid gap-1.5">
        <Label htmlFor="gstin">GSTIN</Label>
        <Input
          id="gstin"
          value={gst.gstin}
          aria-invalid={!!errors.gstin || undefined}
          onChange={(event) => onChange({ ...gst, gstin: event.target.value.toUpperCase() })}
        />
        {errors.gstin ? (
          <p role="alert" className="text-xs text-destructive">
            {errors.gstin}
          </p>
        ) : null}
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="place-of-supply">Place of supply</Label>
        <Input
          id="place-of-supply"
          value={gst.place_of_supply}
          placeholder="29"
          aria-invalid={!!errors.place_of_supply || undefined}
          onChange={(event) => onChange({ ...gst, place_of_supply: event.target.value })}
        />
        {errors.place_of_supply ? (
          <p role="alert" className="text-xs text-destructive">
            {errors.place_of_supply}
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">Two-digit GST state code.</p>
        )}
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="gst-legal-name">Registered name</Label>
        <Input
          id="gst-legal-name"
          value={gst.legal_name}
          onChange={(event) => onChange({ ...gst, legal_name: event.target.value })}
        />
      </div>
    </fieldset>
  );
}

/** Settings > Billing (SPEC 3 plans, 10.4 screen 8; owner only on the API). */
export function BillingScreen() {
  const [billing, setBilling] = React.useState<Billing | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [gst, setGst] = React.useState({ gstin: "", place_of_supply: "", legal_name: "" });

  React.useEffect(() => {
    const controller = new AbortController();
    getBilling(controller.signal)
      .then((row) => {
        setBilling(row);
        const details = (row.gst_details ?? {}) as Record<string, unknown>;
        setGst({
          gstin: String(details.gstin ?? ""),
          place_of_supply: String(details.place_of_supply ?? ""),
          legal_name: String(details.legal_name ?? ""),
        });
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          caught instanceof ApiError && caught.status === 403
            ? "Only the tenant owner may see billing."
            : caught instanceof ApiError
              ? errorMessage(caught.body, `Billing could not be read (${caught.status}).`)
              : "Billing could not be read.",
        );
      });
    return () => controller.abort();
  }, []);

  const gstProblems = billing && needsGst(billing.provider) ? gstErrors(gst) : {};

  const upgrade = async (plan: Plan) => {
    if (!billing) return;
    if (Object.keys(gstProblems).length) return;
    setBusy(plan);
    try {
      const origin = window.location.origin;
      const checkout = await startCheckout({
        plan,
        success_url: `${origin}/app/settings/billing?checkout=success`,
        cancel_url: `${origin}/app/settings/billing?checkout=cancelled`,
        ...(needsGst(billing.provider) && (gst.gstin || gst.place_of_supply || gst.legal_name)
          ? {
              gst: {
                gstin: gst.gstin || null,
                place_of_supply: gst.place_of_supply || null,
                legal_name: gst.legal_name || null,
              },
            }
          : {}),
      });
      // The provider's hosted page finishes the payment; the plan only changes
      // when its webhook says so.
      window.location.assign(checkout.url);
    } catch (caught) {
      toast.error(
        caught instanceof ApiError
          ? errorMessage(caught.body, `Checkout could not be started (${caught.status})`)
          : "Checkout could not be started",
      );
      setBusy(null);
    }
  };

  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    );
  }
  if (!billing) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const bars = usageBars(billing.limits);
  const provider = PROVIDER_LABELS[billing.provider] ?? billing.provider;
  const invoice = billing.last_invoice;

  return (
    <div className="grid gap-6" data-testid="billing-screen">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">Billing</h2>
        <p className="text-sm text-muted-foreground">
          Your plan, what you have used against it, and how to change it.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            <span data-testid="current-plan">{PLAN_LABELS[billing.plan as Plan] ?? billing.plan}</span>
            <Badge variant="outline">{billing.status}</Badge>
            <Badge variant="outline">{provider}</Badge>
          </CardTitle>
          <CardDescription>
            Billed in {billing.currency}
            {billing.next_invoice_at
              ? ` · next invoice ${new Date(billing.next_invoice_at).toLocaleDateString()}`
              : " · no scheduled invoice"}
            {billing.current_period_end
              ? ` · period ends ${new Date(billing.current_period_end).toLocaleDateString()}`
              : ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <ul className="grid gap-3" data-testid="usage-bars">
            {bars.map((bar) => (
              <li key={bar.resource} className="grid gap-1" data-testid={`usage-${bar.resource}`}>
                <div className="flex items-center justify-between gap-3 text-sm">
                  <span>{bar.label}</span>
                  <span className="tabular-nums text-muted-foreground">{bar.text}</span>
                </div>
                <Progress value={bar.percent} label={`${bar.label}: ${bar.text}`} />
                {bar.atLimit ? (
                  <p className="text-xs text-muted-foreground">
                    At the plan limit — upgrading raises it.
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
          {invoice ? (
            <p className="text-sm text-muted-foreground">
              Last invoice{invoice.number ? ` ${invoice.number}` : ""}:{" "}
              {formatMoney(fromMinor(invoice.amount), invoice.currency === "INR" ? "INR" : "USD")} on{" "}
              {new Date(invoice.paid_at).toLocaleDateString()}
              {invoice.url ? (
                <>
                  {" · "}
                  <a href={invoice.url} className="underline underline-offset-4" rel="noreferrer noopener" target="_blank">
                    receipt
                  </a>
                </>
              ) : null}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Plans</CardTitle>
          <CardDescription>
            Limits are enforced server-side; a change takes effect when the provider confirms it.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="overflow-x-auto">
            <Table data-testid="plan-comparison">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Feature</TableHead>
                  {PLANS.map((plan) => (
                    <TableHead key={plan} data-current={plan === billing.plan ? "true" : undefined}>
                      {PLAN_LABELS[plan]}
                      {plan === billing.plan ? <span className="ml-1 text-xs text-muted-foreground">(current)</span> : null}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {PLAN_COMPARISON.map((feature) => (
                  <TableRow key={feature.label}>
                    <TableCell className="font-medium">{feature.label}</TableCell>
                    {PLANS.map((plan) => (
                      <TableCell key={plan}>{feature.values[plan]}</TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {needsGst(billing.provider) ? (
            <GstFields gst={gst} onChange={setGst} errors={gstProblems} />
          ) : null}

          <div className="flex flex-wrap gap-2">
            {PAID_PLANS.map((plan) => {
              const action = planAction(billing.plan, plan);
              return (
                <Button
                  key={plan}
                  type="button"
                  variant={action === "upgrade" ? "default" : "outline"}
                  disabled={action === "current" || busy !== null || Object.keys(gstProblems).length > 0}
                  onClick={() => void upgrade(plan)}
                  data-testid={`checkout-${plan}`}
                >
                  {action === "current"
                    ? `${PLAN_LABELS[plan]} — current plan`
                    : action === "upgrade"
                      ? `Upgrade to ${PLAN_LABELS[plan]}`
                      : `Switch to ${PLAN_LABELS[plan]}`}
                </Button>
              );
            })}
          </div>
          <p className="text-xs text-muted-foreground">
            Checkout opens {provider}; BidRadar never sees your card. To cancel or downgrade to Free,
            write to support — a plan only changes when the provider&apos;s webhook confirms it.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

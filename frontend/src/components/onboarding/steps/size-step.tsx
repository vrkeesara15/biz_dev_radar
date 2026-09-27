"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import * as React from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import type { Profile, ProfileUpdate } from "@/lib/api/browser";
import { formatMoney, parseMoney, type Currency } from "@/lib/money";
import { listItems, syncCollection, updateProfile, type ItemOut } from "@/lib/onboarding/api";
import {
  CERTIFICATION_KINDS,
  CURRENCY_BY_REGION,
  MSE_OWNERSHIP,
  fieldLabel,
  fieldMeta,
  isFieldAllowed,
  optionsForRegion,
  stripRegionForeign,
} from "@/lib/profile-fields";

import {
  CheckboxField,
  ErrorBanner,
  Field,
  FieldGrid,
  MoneyInput,
  RowList,
  Section,
  SelectField,
  StepFooter,
  TagsInput,
  controlProps,
  intOrNull,
  orNull,
  str,
} from "../form";
import type { StepProps } from "../types";

const money = z.object({ amount: z.string().trim(), currency: z.enum(["USD", "INR"]) });
const schema = z.object({
  employee_count_total: z.string().trim(),
  employees_by_country: z.array(
    z.object({
      country: z.string().trim().length(2, "ISO-2 country code"),
      count: z.string().trim().min(1, "Enter a count"),
    }),
  ),
  annual_revenue: z.array(
    z.object({
      fiscal_year: z.string().trim().regex(/^\d{4}$/, "Four-digit year"),
      amount: z.string().trim().min(1, "Enter an amount"),
      currency: z.enum(["USD", "INR"]),
    }),
  ),
  audited_fiscal_years: z.array(z.string()),
  bonding_capacity: money,
  net_worth: money,
  solvency_certificate_available: z.boolean(),
  mse_ownership: z.string(),
  socio_certs: z.array(
    z.object({
      id: z.string().optional(),
      kind: z.string().min(1, "Choose a certification"),
      cert_number: z.string().trim(),
      expires_on: z.string().trim(),
    }),
  ),
});
type Values = z.infer<typeof schema>;

const SOCIO_KINDS = CERTIFICATION_KINDS.filter((k) => k.family === "socio_economic");
const SOCIO_SET = new Set(SOCIO_KINDS.map((k) => k.value));

function defaults(profile: Profile, certs: ItemOut<"certifications">[]): Values {
  const currency = CURRENCY_BY_REGION[profile.region === "in" ? "IN" : "US"];
  return {
    employee_count_total: str(profile.employee_count_total),
    employees_by_country: Object.entries(profile.employees_by_country ?? {}).map(([country, count]) => ({
      country,
      count: str(count),
    })),
    annual_revenue: (profile.annual_revenue ?? []).map((r) => ({
      fiscal_year: str(r.fiscal_year),
      amount: r.amount,
      currency: r.currency as Currency,
    })),
    audited_fiscal_years: (profile.audited_fiscal_years ?? []).map(String),
    bonding_capacity: {
      amount: str(profile.bonding_capacity_amount),
      currency: (profile.bonding_capacity_currency as Currency) ?? currency,
    },
    net_worth: { amount: str(profile.net_worth_amount), currency: (profile.net_worth_currency as Currency) ?? currency },
    solvency_certificate_available: Boolean(profile.solvency_certificate_available),
    mse_ownership: str(profile.mse_ownership),
    socio_certs: certs
      .filter((c) => SOCIO_SET.has(c.kind))
      .map((c) => ({ id: c.id, kind: c.kind, cert_number: str(c.cert_number), expires_on: str(c.expires_on) })),
  };
}

function toPayload(v: Values, region: "US" | "IN"): ProfileUpdate {
  const bonding = parseMoney(v.bonding_capacity.amount);
  const netWorth = parseMoney(v.net_worth.amount);
  const payload: Record<string, unknown> = {
    employee_count_total: intOrNull(v.employee_count_total),
    employees_by_country: Object.fromEntries(
      v.employees_by_country.map((r) => [r.country.trim().toUpperCase(), Number.parseInt(r.count, 10) || 0]),
    ),
    annual_revenue: v.annual_revenue.map((r) => ({
      fiscal_year: Number.parseInt(r.fiscal_year, 10),
      amount: parseMoney(r.amount) ?? "0",
      currency: r.currency,
    })),
    audited_fiscal_years: v.audited_fiscal_years.map((y) => Number.parseInt(y, 10)).filter((n) => Number.isInteger(n)),
    bonding_capacity_amount: bonding,
    bonding_capacity_currency: bonding ? v.bonding_capacity.currency : null,
    net_worth_amount: netWorth,
    net_worth_currency: netWorth ? v.net_worth.currency : null,
    solvency_certificate_available: v.solvency_certificate_available,
    mse_ownership: orNull(v.mse_ownership),
  };
  return stripRegionForeign(payload, region) as ProfileUpdate;
}

export function SizeStep({ profile, region, onBack, onComplete, onProfileChange }: StepProps & { profile: Profile }) {
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);
  const [serverCerts, setServerCerts] = React.useState<ItemOut<"certifications">[]>([]);
  const currency = CURRENCY_BY_REGION[region];

  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults(profile, []), mode: "onBlur" });
  const { register, control, handleSubmit, formState, reset } = form;
  const errors = formState.errors;
  const byCountry = useFieldArray({ control, name: "employees_by_country" });
  const revenue = useFieldArray({ control, name: "annual_revenue" });
  const certs = useFieldArray({ control, name: "socio_certs" });

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = region === "US" ? await listItems(profile.id, "certifications") : [];
        if (cancelled) return;
        setServerCerts(list);
        reset(defaults(profile, list));
      } catch (e) {
        if (!cancelled) setError(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.id]);

  const onSubmit = handleSubmit(async (values) => {
    setSaving(true);
    setError(null);
    try {
      const saved = await updateProfile(profile.id, toPayload(values, region));
      onProfileChange(saved);
      if (region === "US") {
        const previous = serverCerts.filter((c) => SOCIO_SET.has(c.kind));
        const fresh = await syncCollection(
          profile.id,
          "certifications",
          previous,
          values.socio_certs.map((c) => ({
            id: c.id,
            kind: c.kind as ItemOut<"certifications">["kind"],
            cert_number: orNull(c.cert_number),
            expires_on: orNull(c.expires_on),
          })),
        );
        setServerCerts([...serverCerts.filter((c) => !SOCIO_SET.has(c.kind)), ...fresh]);
      }
      await onComplete(saved);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  });

  if (loading) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading size and status…
      </p>
    );
  }

  const sizeStatus = Object.entries(profile.size_status_by_naics ?? {}) as [string, { status: string; basis: string | null; threshold: string | null; measured: string | null; reason: string }][];

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-6" aria-label="Size and status">
      <Section title="Size" description="Headcount drives size standards and staffing claims.">
        <FieldGrid>
          <Field id="f-employee_count_total" label={fieldLabel("employee_count_total")} error={errors.employee_count_total?.message}>
            <Input {...controlProps("f-employee_count_total", errors.employee_count_total?.message)} {...register("employee_count_total")} type="number" min={0} />
          </Field>
        </FieldGrid>
        <RowList
          title={fieldLabel("employees_by_country")}
          rows={byCountry.fields}
          rowLabel="country headcount"
          addLabel="Add country"
          emptyText="No per-country breakdown."
          onAdd={() => byCountry.append({ country: region, count: "" })}
          onRemove={(i) => byCountry.remove(i)}
          renderRow={(_, i) => {
            const e = errors.employees_by_country?.[i];
            return (
              <FieldGrid>
                <Field id={`f-ebc-${i}-country`} label="Country (ISO-2)" required error={e?.country?.message}>
                  <Input {...controlProps(`f-ebc-${i}-country`, e?.country?.message)} {...register(`employees_by_country.${i}.country`)} maxLength={2} className="uppercase" />
                </Field>
                <Field id={`f-ebc-${i}-count`} label="Employees" required error={e?.count?.message}>
                  <Input {...controlProps(`f-ebc-${i}-count`, e?.count?.message)} {...register(`employees_by_country.${i}.count`)} type="number" min={0} />
                </Field>
              </FieldGrid>
            );
          }}
        />
      </Section>

      <Section title="Finances" description={region === "IN" ? "Turnover criteria usually use the average of the last 3 FYs." : "SBA size standards use average receipts over the last 3 fiscal years."}>
        <RowList
          title={fieldLabel("annual_revenue")}
          rows={revenue.fields}
          rowLabel="revenue year"
          addLabel="Add fiscal year"
          emptyText="No revenue years yet."
          onAdd={() => {
            const last = revenue.fields.length ? Number(form.getValues(`annual_revenue.${revenue.fields.length - 1}.fiscal_year`)) : new Date().getFullYear();
            revenue.append({ fiscal_year: String(Number.isFinite(last) && last ? last - 1 : new Date().getFullYear() - 1), amount: "", currency });
          }}
          onRemove={(i) => revenue.remove(i)}
          renderRow={(_, i) => {
            const e = errors.annual_revenue?.[i];
            return (
              <FieldGrid>
                <Field id={`f-rev-${i}-fy`} label="Fiscal year" required error={e?.fiscal_year?.message}>
                  <Input {...controlProps(`f-rev-${i}-fy`, e?.fiscal_year?.message)} {...register(`annual_revenue.${i}.fiscal_year`)} inputMode="numeric" maxLength={4} />
                </Field>
                <Controller
                  control={control}
                  name={`annual_revenue.${i}`}
                  render={({ field }) => (
                    <MoneyInput
                      id={`f-rev-${i}-amount`}
                      label="Revenue"
                      required
                      amount={field.value.amount}
                      currency={field.value.currency}
                      onChange={(next) => field.onChange({ ...field.value, ...next })}
                      error={e?.amount?.message}
                    />
                  )}
                />
              </FieldGrid>
            );
          }}
        />
        {profile.average_turnover ? (
          <p className="text-xs text-muted-foreground">
            Average turnover (FY {profile.average_turnover.fiscal_years.join(", ")}):{" "}
            <span className="tabular-nums">{formatMoney(profile.average_turnover.amount, profile.average_turnover.currency as Currency)}</span>
          </p>
        ) : null}
        <FieldGrid>
          <Controller
            control={control}
            name="audited_fiscal_years"
            render={({ field }) => (
              <TagsInput id="f-audited_fiscal_years" label={fieldLabel("audited_fiscal_years")} value={field.value} onChange={field.onChange} placeholder="2023, 2024" help="Years with audited statements, comma separated." />
            )}
          />
          <Controller
            control={control}
            name="bonding_capacity"
            render={({ field }) => (
              <MoneyInput id="f-bonding_capacity" label={fieldLabel("bonding_capacity")} amount={field.value.amount} currency={field.value.currency} onChange={field.onChange} />
            )}
          />
          {isFieldAllowed("net_worth", region) ? (
            <Controller
              control={control}
              name="net_worth"
              render={({ field }) => (
                <MoneyInput id="f-net_worth" label={fieldLabel("net_worth")} amount={field.value.amount} currency={field.value.currency} onChange={field.onChange} />
              )}
            />
          ) : null}
          {isFieldAllowed("solvency_certificate_available", region) ? (
            <Controller
              control={control}
              name="solvency_certificate_available"
              render={({ field }) => (
                <CheckboxField id="f-solvency" label={fieldLabel("solvency_certificate_available")} checked={field.value} onChange={field.onChange} help="Common tender criterion." />
              )}
            />
          ) : null}
        </FieldGrid>
      </Section>

      <Section title="Status" description={region === "US" ? "Set-aside eligibility." : "Reserved procurement share for MSEs."}>
        {isFieldAllowed("size_status_by_naics", region) ? (
          <div className="grid gap-2">
            <h3 className="text-sm font-medium">{fieldLabel("size_status_by_naics")}</h3>
            <p className="text-xs text-muted-foreground">{fieldMeta("size_status_by_naics")?.help}</p>
            {sizeStatus.length === 0 ? (
              <p className="text-sm text-muted-foreground">Add NAICS codes in step 3 to see your size status.</p>
            ) : (
              <ul className="grid gap-1">
                {sizeStatus.map(([naics, s]) => (
                  <li key={naics} className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-mono">{naics}</span>
                    <Badge variant={s.status === "small" ? "default" : "outline"}>{s.status.replace(/_/g, " ")}</Badge>
                    <span className="text-xs text-muted-foreground">{s.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}
        {isFieldAllowed("certifications.socio_economic", region) ? (
          <RowList
            title={fieldLabel("certifications.socio_economic")}
            rows={certs.fields}
            rowLabel="certification"
            addLabel="Add certification"
            emptyText="No socio-economic certifications."
            onAdd={() => certs.append({ kind: "", cert_number: "", expires_on: "" })}
            onRemove={(i) => certs.remove(i)}
            renderRow={(_, i) => {
              const e = errors.socio_certs?.[i];
              return (
                <FieldGrid className="sm:grid-cols-3">
                  <Controller
                    control={control}
                    name={`socio_certs.${i}.kind`}
                    render={({ field }) => (
                      <SelectField id={`f-socio-${i}-kind`} label="Certification" required options={optionsForRegion(SOCIO_KINDS, region)} value={field.value} onChange={(v) => field.onChange(v ?? "")} error={e?.kind?.message} />
                    )}
                  />
                  <Field id={`f-socio-${i}-number`} label="Certificate number">
                    <Input {...controlProps(`f-socio-${i}-number`)} {...register(`socio_certs.${i}.cert_number`)} />
                  </Field>
                  <Field id={`f-socio-${i}-expires`} label="Expiry">
                    <Input {...controlProps(`f-socio-${i}-expires`)} {...register(`socio_certs.${i}.expires_on`)} type="date" />
                  </Field>
                </FieldGrid>
              );
            }}
          />
        ) : null}
        {isFieldAllowed("mse_ownership", region) ? (
          <FieldGrid>
            <Controller
              control={control}
              name="mse_ownership"
              render={({ field }) => (
                <SelectField id="f-mse_ownership" label={fieldLabel("mse_ownership")} options={MSE_OWNERSHIP} value={field.value} onChange={(v) => field.onChange(v ?? "")} />
              )}
            />
          </FieldGrid>
        ) : null}
      </Section>

      <ErrorBanner error={error} />
      <StepFooter step={2} saving={saving} onBack={onBack} />
    </form>
  );
}

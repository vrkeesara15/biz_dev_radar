"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import * as React from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { z } from "zod";

import { Input } from "@/components/ui/input";
import type { Profile, ProfileUpdate } from "@/lib/api/browser";
import { parseMoney } from "@/lib/money";
import { listItems, syncCollection, updateProfile, type ItemOut } from "@/lib/onboarding/api";
import {
  CONTRACT_TYPES,
  CURRENCY_BY_REGION,
  NOTICE_TYPES,
  TEAMING_ROLES,
  fieldLabel,
  isFieldAllowed,
  isMaskedValue,
  optionsForRegion,
} from "@/lib/profile-fields";

import {
  CheckboxField,
  CheckboxGroup,
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
  orNull,
  str,
} from "../form";
import type { StepProps } from "../types";

const schema = z
  .object({
    target_countries: z.array(z.string()),
    target_us_states: z.array(z.string()),
    target_in_states: z.array(z.string()),
    target_cities: z.array(z.string()),
    remote_ok: z.boolean(),
    target_buyers: z.array(z.string()),
    blocked_buyers: z.array(z.string()),
    value_min: z.string().trim(),
    value_max: z.string().trim(),
    notice_types_wanted: z.array(z.string()),
    contract_types_preferred: z.array(z.string()),
    teaming_roles: z.array(z.string()),
    partners: z.array(
      z.object({
        id: z.string().optional(),
        name: z.string().trim().min(1, "Partner name is required"),
        relationship: z.string().min(1, "Choose a relationship"),
        uei: z.string().trim(),
        pan: z.string().trim(),
        capabilities: z.array(z.string()),
        website: z.string().trim(),
        contact_email: z.string().trim(),
      }),
    ),
  })
  .refine(
    (v) => {
      const min = Number(parseMoney(v.value_min) ?? 0);
      const max = Number(parseMoney(v.value_max) ?? Number.POSITIVE_INFINITY);
      return !(v.value_min && v.value_max) || min <= max;
    },
    { message: "Minimum must not exceed maximum", path: ["value_max"] },
  );
type Values = z.infer<typeof schema>;

function defaults(profile: Profile, partners: ItemOut<"teaming-partners">[], region: "US" | "IN"): Values {
  return {
    target_countries: profile.target_countries ?? [],
    target_us_states: profile.target_us_states ?? [],
    target_in_states: profile.target_in_states ?? [],
    target_cities: profile.target_cities ?? [],
    remote_ok: Boolean(profile.remote_ok),
    target_buyers: profile.target_buyers ?? [],
    blocked_buyers: profile.blocked_buyers ?? [],
    value_min: str(region === "US" ? profile.value_min_usd : profile.value_min_inr),
    value_max: str(region === "US" ? profile.value_max_usd : profile.value_max_inr),
    notice_types_wanted: profile.notice_types_wanted ?? [],
    contract_types_preferred: profile.contract_types_preferred ?? [],
    teaming_roles: profile.teaming_roles ?? [],
    partners: partners.map((p) => ({
      id: p.id,
      name: p.name,
      relationship: p.relationship,
      uei: str(p.uei),
      pan: str(p.pan),
      capabilities: p.capabilities,
      website: str(p.website),
      contact_email: str(p.contact_email),
    })),
  };
}

export function WhereStep({ profile, region, onBack, onComplete, onProfileChange }: StepProps & { profile: Profile }) {
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);
  const [serverPartners, setServerPartners] = React.useState<ItemOut<"teaming-partners">[]>([]);
  const currency = CURRENCY_BY_REGION[region];

  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults(profile, [], region), mode: "onBlur" });
  const { register, control, handleSubmit, formState, reset } = form;
  const errors = formState.errors;
  const partners = useFieldArray({ control, name: "partners" });

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await listItems(profile.id, "teaming-partners");
        if (cancelled) return;
        setServerPartners(list);
        reset(defaults(profile, list, region));
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
      const min = parseMoney(values.value_min);
      const max = parseMoney(values.value_max);
      const payload: ProfileUpdate = {
        target_countries: values.target_countries.map((c) => c.toUpperCase()),
        target_cities: values.target_cities,
        remote_ok: values.remote_ok,
        target_buyers: values.target_buyers,
        blocked_buyers: values.blocked_buyers,
        notice_types_wanted: values.notice_types_wanted as ProfileUpdate["notice_types_wanted"],
        contract_types_preferred: values.contract_types_preferred as ProfileUpdate["contract_types_preferred"],
        teaming_roles: values.teaming_roles as ProfileUpdate["teaming_roles"],
        ...(region === "US"
          ? { target_us_states: values.target_us_states.map((s) => s.toUpperCase()), value_min_usd: min, value_max_usd: max }
          : { target_in_states: values.target_in_states.map((s) => s.toUpperCase()), value_min_inr: min, value_max_inr: max }),
      };
      const saved = await updateProfile(profile.id, payload);
      onProfileChange(saved);
      const fresh = await syncCollection(
        profile.id,
        "teaming-partners",
        serverPartners,
        values.partners.map((p) => ({
          id: p.id,
          name: p.name.trim(),
          relationship: p.relationship as ItemOut<"teaming-partners">["relationship"],
          uei: region === "US" ? orNull(p.uei)?.toUpperCase() ?? null : null,
          // Masked PAN echoes are no-ops server-side; only send new values.
          pan: region === "IN" && p.pan && !isMaskedValue(p.pan) ? p.pan.toUpperCase() : region === "IN" ? p.pan || null : null,
          capabilities: p.capabilities,
          website: orNull(p.website),
          contact_email: orNull(p.contact_email),
        })),
      );
      setServerPartners(fresh);
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
        Loading scope…
      </p>
    );
  }

  const tags = (name: "target_countries" | "target_us_states" | "target_in_states" | "target_cities" | "target_buyers" | "blocked_buyers", extra: { placeholder?: string; help?: string; transform?: (t: string) => string } = {}) => (
    <Controller
      control={control}
      name={name}
      render={({ field }) => <TagsInput id={`f-${name}`} label={fieldLabel(name)} value={field.value} onChange={field.onChange} placeholder={extra.placeholder} help={extra.help} transform={extra.transform} />}
    />
  );
  const upper = (t: string) => t.toUpperCase();

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-6" aria-label="Where and how big">
      <Section title="Geography" description="Where you can perform. Leave empty to match everywhere in your region.">
        <FieldGrid>
          {tags("target_countries", { placeholder: region === "US" ? "US, CA" : "IN", help: "ISO-2 country codes, comma separated.", transform: upper })}
          {isFieldAllowed("target_us_states", region) ? tags("target_us_states", { placeholder: "VA, MD, DC", help: "Two-letter state codes.", transform: upper }) : null}
          {isFieldAllowed("target_in_states", region) ? tags("target_in_states", { placeholder: "TG, KA, MH", help: "ISO 3166-2:IN codes (TG, KA, MH…).", transform: upper }) : null}
          {tags("target_cities", { placeholder: region === "US" ? "Arlington, Austin" : "Hyderabad, Bengaluru" })}
          <Controller
            control={control}
            name="remote_ok"
            render={({ field }) => <CheckboxField id="f-remote_ok" label={fieldLabel("remote_ok")} checked={field.value} onChange={field.onChange} />}
          />
        </FieldGrid>
      </Section>

      <Section title="Buyers" description="Boost matches from target buyers; never show blocked ones.">
        <FieldGrid>
          {tags("target_buyers", { placeholder: region === "US" ? "Department of Veterans Affairs, GSA" : "Ministry of Electronics and IT, NTPC" })}
          {tags("blocked_buyers")}
        </FieldGrid>
      </Section>

      <Section title="Value and types" description="Filter by estimated value and the notice and contract types you want.">
        <FieldGrid>
          <Controller
            control={control}
            name="value_min"
            render={({ field }) => <MoneyInput id="f-value_min" label={`Minimum value (${currency})`} amount={field.value} currency={currency} currencies={[currency]} onChange={(n) => field.onChange(n.amount)} />}
          />
          <Controller
            control={control}
            name="value_max"
            render={({ field }) => <MoneyInput id="f-value_max" label={`Maximum value (${currency})`} amount={field.value} currency={currency} currencies={[currency]} onChange={(n) => field.onChange(n.amount)} error={errors.value_max?.message} />}
          />
        </FieldGrid>
        <Controller
          control={control}
          name="notice_types_wanted"
          render={({ field }) => <CheckboxGroup idPrefix="f-notice" legend={fieldLabel("notice_types_wanted")} options={optionsForRegion(NOTICE_TYPES, region)} value={field.value} onChange={field.onChange} columns={3} />}
        />
        <Controller
          control={control}
          name="contract_types_preferred"
          render={({ field }) => <CheckboxGroup idPrefix="f-contract" legend={fieldLabel("contract_types_preferred")} options={optionsForRegion(CONTRACT_TYPES, region)} value={field.value} onChange={field.onChange} columns={3} />}
        />
      </Section>

      <Section title="Teaming" description="Roles you will take and partners we can suggest when a requirement is missing.">
        <Controller
          control={control}
          name="teaming_roles"
          render={({ field }) => <CheckboxGroup idPrefix="f-teaming" legend={fieldLabel("teaming_roles")} options={TEAMING_ROLES} value={field.value} onChange={field.onChange} columns={3} />}
        />
        <RowList
          title={fieldLabel("teaming_partners")}
          rows={partners.fields}
          rowLabel="partner"
          addLabel="Add partner"
          emptyText="No partners yet."
          onAdd={() => partners.append({ name: "", relationship: "sub", uei: "", pan: "", capabilities: [], website: "", contact_email: "" })}
          onRemove={(i) => partners.remove(i)}
          renderRow={(_, i) => {
            const e = errors.partners?.[i];
            return (
              <FieldGrid className="sm:grid-cols-3">
                <Field id={`f-partner-${i}-name`} label="Name" required error={e?.name?.message}>
                  <Input {...controlProps(`f-partner-${i}-name`, e?.name?.message)} {...register(`partners.${i}.name`)} />
                </Field>
                <Controller
                  control={control}
                  name={`partners.${i}.relationship`}
                  render={({ field }) => <SelectField id={`f-partner-${i}-rel`} label="Relationship" required options={TEAMING_ROLES} value={field.value} onChange={(v) => field.onChange(v ?? "")} error={e?.relationship?.message} />}
                />
                {region === "US" ? (
                  <Field id={`f-partner-${i}-uei`} label="UEI">
                    <Input {...controlProps(`f-partner-${i}-uei`)} {...register(`partners.${i}.uei`)} maxLength={12} className="font-mono" />
                  </Field>
                ) : (
                  <Field id={`f-partner-${i}-pan`} label="PAN" help="Stored encrypted, shown masked.">
                    <Input {...controlProps(`f-partner-${i}-pan`, undefined, "x")} {...register(`partners.${i}.pan`)} maxLength={10} className="font-mono" />
                  </Field>
                )}
                <Controller
                  control={control}
                  name={`partners.${i}.capabilities`}
                  render={({ field }) => <TagsInput id={`f-partner-${i}-caps`} label="Capabilities" value={field.value} onChange={field.onChange} />}
                />
                <Field id={`f-partner-${i}-website`} label="Website">
                  <Input {...controlProps(`f-partner-${i}-website`)} {...register(`partners.${i}.website`)} type="url" />
                </Field>
                <Field id={`f-partner-${i}-email`} label="Contact email">
                  <Input {...controlProps(`f-partner-${i}-email`)} {...register(`partners.${i}.contact_email`)} type="email" />
                </Field>
              </FieldGrid>
            );
          }}
        />
      </Section>

      <ErrorBanner error={error} />
      <StepFooter step={4} saving={saving} onBack={onBack} />
    </form>
  );
}

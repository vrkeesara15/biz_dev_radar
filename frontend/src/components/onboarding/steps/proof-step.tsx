"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import * as React from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Profile } from "@/lib/api/browser";
import { parseMoney, type Currency } from "@/lib/money";
import {
  listItems,
  syncCollection,
  updateProfile,
  uploadFile,
  type ItemOut,
} from "@/lib/onboarding/api";
import {
  AGENCY_TYPES,
  BOILERPLATE_KINDS,
  CERTIFICATION_KINDS,
  CPARS_RATINGS,
  CURRENCY_BY_REGION,
  INSURANCE_KINDS,
  PERFORMANCE_ROLES,
  PROFILE_FILE_KINDS,
  RATE_UNITS,
  fieldLabel,
  isFieldAllowed,
  optionsForRegion,
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

const opt = z.string().trim();
const schema = z.object({
  past_performance: z.array(
    z.object({
      id: z.string().optional(),
      title: opt.min(1, "Title is required"),
      customer: opt.min(1, "Customer is required"),
      customer_anonymized: z.boolean(),
      agency_type: opt,
      value: z.object({ amount: z.string(), currency: z.enum(["USD", "INR"]) }),
      period_start: opt,
      period_end: opt,
      role: z.enum(["prime", "sub"]),
      naics: opt,
      contract_number: opt,
      scope: opt.min(1, "Scope is required"),
      outcomes: opt,
      technologies: z.array(z.string()),
      ref_name: opt,
      ref_title: opt,
      ref_email: opt,
      ref_phone: opt,
      cpars_rating: opt,
      is_public: z.boolean(),
    }),
  ),
  personnel: z.array(
    z.object({
      id: z.string().optional(),
      name: opt.min(1, "Name is required"),
      role: opt.min(1, "Role is required"),
      years_experience: opt,
      clearances: z.array(z.string()),
      certifications: z.array(z.string()),
      education: opt,
      resume_file_id: opt,
      resume_name: opt,
      is_key_personnel: z.boolean(),
    }),
  ),
  rate_card: z.array(
    z.object({
      id: z.string().optional(),
      labor_category: opt.min(1, "Labor category is required"),
      unit: z.enum(["hour", "day", "month"]),
      rate: z.object({ amount: z.string().min(1, "Enter a rate"), currency: z.enum(["USD", "INR"]) }),
      min_years_experience: opt,
    }),
  ),
  security_certs: z.array(
    z.object({
      id: z.string().optional(),
      kind: z.string().min(1, "Choose a certification"),
      level: opt,
      cert_number: opt,
      expires_on: opt,
    }),
  ),
  cleared_personnel_count: opt,
  insurance: z.array(
    z.object({
      id: z.string().optional(),
      kind: z.string().min(1, "Choose a kind"),
      carrier: opt,
      policy_number: opt,
      limit: z.object({ amount: z.string(), currency: z.enum(["USD", "INR"]) }),
      expires_on: opt,
    }),
  ),
  boilerplate: z.array(
    z.object({
      id: z.string().optional(),
      kind: z.string().min(1, "Choose a kind"),
      title: opt.min(1, "Title is required"),
      body: opt.min(1, "Body is required"),
    }),
  ),
  files: z.array(
    z.object({
      id: z.string().optional(),
      file_id: z.string().min(1, "Upload a file"),
      kind: z.string().min(1, "Choose a kind"),
      title: opt,
      filename: opt,
      outcome: opt,
    }),
  ),
});
type Values = z.infer<typeof schema>;

type Lists = {
  pp: ItemOut<"past-performance">[];
  people: ItemOut<"personnel">[];
  rates: ItemOut<"rate-card">[];
  certs: ItemOut<"certifications">[];
  insurance: ItemOut<"insurance">[];
  boilerplate: ItemOut<"boilerplate">[];
  files: ItemOut<"files">[];
};
const EMPTY: Lists = { pp: [], people: [], rates: [], certs: [], insurance: [], boilerplate: [], files: [] };

const SECURITY_KINDS = CERTIFICATION_KINDS.filter((k) => k.family === "security");
const SECURITY_SET = new Set(SECURITY_KINDS.map((k) => k.value));

function defaults(profile: Profile, l: Lists, currency: Currency): Values {
  return {
    past_performance: l.pp.map((p) => {
      const ref = (p.reference_contact ?? {}) as Record<string, unknown>;
      return {
        id: p.id,
        title: p.title,
        customer: p.customer,
        customer_anonymized: p.customer_anonymized,
        agency_type: str(p.agency_type),
        value: { amount: str(p.value_amount), currency: (p.value_currency as Currency) ?? currency },
        period_start: str(p.period_start),
        period_end: str(p.period_end),
        role: p.role,
        naics: str(p.naics),
        contract_number: str(p.contract_number),
        scope: p.scope,
        outcomes: str(p.outcomes),
        technologies: p.technologies ?? [],
        ref_name: str(ref.name),
        ref_title: str(ref.title),
        ref_email: str(ref.email),
        ref_phone: str(ref.phone),
        cpars_rating: str(p.cpars_rating),
        is_public: p.is_public,
      };
    }),
    personnel: l.people.map((p) => ({
      id: p.id,
      name: p.name,
      role: p.role,
      years_experience: str(p.years_experience),
      clearances: p.clearances ?? [],
      certifications: p.certifications ?? [],
      education: str(p.education),
      resume_file_id: str(p.resume_file_id),
      resume_name: p.resume_file_id ? "Résumé on file" : "",
      is_key_personnel: p.is_key_personnel,
    })),
    rate_card: l.rates.map((r) => ({
      id: r.id,
      labor_category: r.labor_category,
      unit: r.unit,
      rate: { amount: r.rate_amount, currency: r.rate_currency as Currency },
      min_years_experience: str(r.min_years_experience),
    })),
    security_certs: l.certs
      .filter((c) => SECURITY_SET.has(c.kind))
      .map((c) => ({ id: c.id, kind: c.kind, level: str(c.level), cert_number: str(c.cert_number), expires_on: str(c.expires_on) })),
    cleared_personnel_count: str(profile.cleared_personnel_count),
    insurance: l.insurance.map((i) => ({
      id: i.id,
      kind: i.kind,
      carrier: str(i.carrier),
      policy_number: str(i.policy_number),
      limit: { amount: str(i.limit_amount), currency: (i.limit_currency as Currency) ?? currency },
      expires_on: str(i.expires_on),
    })),
    boilerplate: l.boilerplate.map((b) => ({ id: b.id, kind: b.kind, title: b.title, body: b.body })),
    files: l.files.map((f) => ({
      id: f.id,
      file_id: f.file_id,
      kind: f.kind,
      title: str(f.title),
      filename: str((f.meta as Record<string, unknown>)?.filename),
      outcome: str((f.meta as Record<string, unknown>)?.outcome),
    })),
  };
}

export function ProofStep({ profile, region, onBack, onComplete, onProfileChange, onPastPerformanceCount }: StepProps & { profile: Profile }) {
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);
  const [server, setServer] = React.useState<Lists>(EMPTY);
  const [uploading, setUploading] = React.useState<string | null>(null);
  const currency = CURRENCY_BY_REGION[region];

  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults(profile, EMPTY, currency), mode: "onBlur" });
  const { register, control, handleSubmit, formState, reset, setValue, watch } = form;
  const errors = formState.errors;
  const pp = useFieldArray({ control, name: "past_performance" });
  const people = useFieldArray({ control, name: "personnel" });
  const rates = useFieldArray({ control, name: "rate_card" });
  const certs = useFieldArray({ control, name: "security_certs" });
  const insurance = useFieldArray({ control, name: "insurance" });
  const boilerplate = useFieldArray({ control, name: "boilerplate" });
  const files = useFieldArray({ control, name: "files" });

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [a, b, c, d, e, f, g] = await Promise.all([
          listItems(profile.id, "past-performance"),
          listItems(profile.id, "personnel"),
          listItems(profile.id, "rate-card"),
          listItems(profile.id, "certifications"),
          listItems(profile.id, "insurance"),
          listItems(profile.id, "boilerplate"),
          listItems(profile.id, "files"),
        ]);
        if (cancelled) return;
        const next: Lists = { pp: a, people: b, rates: c, certs: d, insurance: e, boilerplate: f, files: g };
        setServer(next);
        onPastPerformanceCount?.(a.length);
        reset(defaults(profile, next, currency));
      } catch (err) {
        if (!cancelled) setError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.id]);

  const upload = async (key: string, picked: File | undefined, onDone: (file: { id: string; filename: string }) => void) => {
    if (!picked) return;
    setUploading(key);
    setError(null);
    try {
      const uploaded = await uploadFile(picked);
      onDone({ id: uploaded.id, filename: uploaded.filename });
    } catch (e) {
      setError(e);
    } finally {
      setUploading(null);
    }
  };

  const onSubmit = handleSubmit(async (values) => {
    setSaving(true);
    setError(null);
    try {
      let saved: Profile | null = null;
      if (isFieldAllowed("cleared_personnel_count", region)) {
        saved = await updateProfile(profile.id, { cleared_personnel_count: intOrNull(values.cleared_personnel_count) });
        onProfileChange(saved);
      }
      const ppRows = await syncCollection(
        profile.id,
        "past-performance",
        server.pp,
        values.past_performance.map((p) => {
          const amount = parseMoney(p.value.amount);
          return {
            id: p.id,
            title: p.title.trim(),
            customer: p.customer.trim(),
            customer_anonymized: p.customer_anonymized,
            agency_type: orNull(p.agency_type) as ItemOut<"past-performance">["agency_type"],
            value_amount: amount,
            value_currency: amount ? p.value.currency : null,
            period_start: orNull(p.period_start),
            period_end: orNull(p.period_end),
            role: p.role,
            naics: orNull(p.naics),
            contract_number: orNull(p.contract_number),
            scope: p.scope.trim(),
            outcomes: orNull(p.outcomes),
            technologies: p.technologies,
            reference_contact: { name: orNull(p.ref_name), title: orNull(p.ref_title), email: orNull(p.ref_email), phone: orNull(p.ref_phone) },
            cpars_rating: orNull(p.cpars_rating) as ItemOut<"past-performance">["cpars_rating"],
            is_public: p.is_public,
          };
        }),
      );
      onPastPerformanceCount?.(ppRows.length);
      const peopleRows = await syncCollection(
        profile.id,
        "personnel",
        server.people,
        values.personnel.map((p) => ({
          id: p.id,
          name: p.name.trim(),
          role: p.role.trim(),
          years_experience: intOrNull(p.years_experience),
          clearances: p.clearances,
          certifications: p.certifications,
          education: orNull(p.education),
          resume_file_id: orNull(p.resume_file_id),
          is_key_personnel: p.is_key_personnel,
        })),
      );
      const rateRows = await syncCollection(
        profile.id,
        "rate-card",
        server.rates,
        values.rate_card.map((r) => ({
          id: r.id,
          labor_category: r.labor_category.trim(),
          unit: r.unit,
          rate_amount: parseMoney(r.rate.amount) ?? "0",
          rate_currency: r.rate.currency,
          min_years_experience: intOrNull(r.min_years_experience),
        })),
      );
      const certRows = await syncCollection(
        profile.id,
        "certifications",
        server.certs.filter((c) => SECURITY_SET.has(c.kind)),
        values.security_certs.map((c) => ({
          id: c.id,
          kind: c.kind as ItemOut<"certifications">["kind"],
          level: orNull(c.level),
          cert_number: orNull(c.cert_number),
          expires_on: orNull(c.expires_on),
        })),
      );
      const insRows = await syncCollection(
        profile.id,
        "insurance",
        server.insurance,
        values.insurance.map((i) => {
          const amount = parseMoney(i.limit.amount);
          return {
            id: i.id,
            kind: i.kind as ItemOut<"insurance">["kind"],
            carrier: orNull(i.carrier),
            policy_number: orNull(i.policy_number),
            limit_amount: amount,
            limit_currency: amount ? i.limit.currency : null,
            expires_on: orNull(i.expires_on),
          };
        }),
      );
      const bpRows = await syncCollection(
        profile.id,
        "boilerplate",
        server.boilerplate,
        values.boilerplate.map((b) => ({ id: b.id, kind: b.kind as ItemOut<"boilerplate">["kind"], title: b.title.trim(), body: b.body, body_format: "markdown" })),
      );
      const fileRows = await syncCollection(
        profile.id,
        "files",
        server.files,
        values.files.map((f) => ({
          id: f.id,
          file_id: f.file_id,
          kind: f.kind as ItemOut<"files">["kind"],
          title: orNull(f.title) ?? orNull(f.filename),
          meta: {
            ...(f.filename ? { filename: f.filename } : {}),
            ...(f.kind === "past_proposal" && f.outcome ? { outcome: f.outcome } : {}),
          },
        })),
      );
      setServer({
        pp: ppRows,
        people: peopleRows,
        rates: rateRows,
        certs: [...server.certs.filter((c) => !SECURITY_SET.has(c.kind)), ...certRows],
        insurance: insRows,
        boilerplate: bpRows,
        files: fileRows,
      });
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
        Loading proof…
      </p>
    );
  }

  const ppCount = pp.fields.length;

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-6" aria-label="Proof">
      <Section
        title="Past performance"
        description="Drafting needs at least three records. Customers can be anonymized for confidential work."
        actions={<Badge variant={ppCount >= 3 ? "default" : "outline"}>{ppCount} of 3 recommended</Badge>}
      >
        <RowList
          title={fieldLabel("past_performance")}
          rows={pp.fields}
          rowLabel="past performance"
          addLabel="Add record"
          emptyText="No past performance yet."
          onAdd={() =>
            pp.append({
              title: "", customer: "", customer_anonymized: false, agency_type: "", value: { amount: "", currency }, period_start: "", period_end: "", role: "prime", naics: "", contract_number: "", scope: "", outcomes: "", technologies: [], ref_name: "", ref_title: "", ref_email: "", ref_phone: "", cpars_rating: "", is_public: false,
            })
          }
          onRemove={(i) => pp.remove(i)}
          renderRow={(_, i) => {
            const e = errors.past_performance?.[i];
            const id = (k: string) => `f-pp-${i}-${k}`;
            return (
              <div className="grid gap-4">
                <FieldGrid>
                  <Field id={id("title")} label="Title" required error={e?.title?.message}>
                    <Input {...controlProps(id("title"), e?.title?.message)} {...register(`past_performance.${i}.title`)} />
                  </Field>
                  <Field id={id("customer")} label="Customer" required error={e?.customer?.message}>
                    <Input {...controlProps(id("customer"), e?.customer?.message)} {...register(`past_performance.${i}.customer`)} />
                  </Field>
                  <Controller control={control} name={`past_performance.${i}.customer_anonymized`} render={({ field }) => <CheckboxField id={id("anon")} label="Anonymize customer in proposals" checked={field.value} onChange={field.onChange} />} />
                  <Controller control={control} name={`past_performance.${i}.is_public`} render={({ field }) => <CheckboxField id={id("public")} label="Public (may be cited by name)" checked={field.value} onChange={field.onChange} />} />
                  <Controller control={control} name={`past_performance.${i}.agency_type`} render={({ field }) => <SelectField id={id("agency")} label="Agency type" options={optionsForRegion(AGENCY_TYPES, region)} value={field.value} onChange={(v) => field.onChange(v ?? "")} />} />
                  <Controller control={control} name={`past_performance.${i}.role`} render={({ field }) => <SelectField id={id("role")} label="Role" required options={PERFORMANCE_ROLES} value={field.value} onChange={(v) => field.onChange(v ?? "prime")} placeholder="Prime" />} />
                  <Controller control={control} name={`past_performance.${i}.value`} render={({ field }) => <MoneyInput id={id("value")} label="Contract value" amount={field.value.amount} currency={field.value.currency} onChange={field.onChange} />} />
                  <Field id={id("naics")} label={region === "US" ? "NAICS" : "Category / NAICS"}>
                    <Input {...controlProps(id("naics"))} {...register(`past_performance.${i}.naics`)} className="font-mono" />
                  </Field>
                  <Field id={id("start")} label="Period start">
                    <Input {...controlProps(id("start"))} {...register(`past_performance.${i}.period_start`)} type="date" />
                  </Field>
                  <Field id={id("end")} label="Period end">
                    <Input {...controlProps(id("end"))} {...register(`past_performance.${i}.period_end`)} type="date" />
                  </Field>
                  <Field id={id("contract")} label="Contract number">
                    <Input {...controlProps(id("contract"))} {...register(`past_performance.${i}.contract_number`)} />
                  </Field>
                  <Controller control={control} name={`past_performance.${i}.cpars_rating`} render={({ field }) => <SelectField id={id("cpars")} label={region === "US" ? "CPARS rating" : "Performance rating"} options={CPARS_RATINGS} value={field.value} onChange={(v) => field.onChange(v ?? "")} />} />
                </FieldGrid>
                <Field id={id("scope")} label="Scope" required error={e?.scope?.message}>
                  <Textarea {...controlProps(id("scope"), e?.scope?.message)} {...register(`past_performance.${i}.scope`)} rows={3} />
                </Field>
                <Field id={id("outcomes")} label="Outcomes (with numbers)">
                  <Textarea {...controlProps(id("outcomes"))} {...register(`past_performance.${i}.outcomes`)} rows={2} />
                </Field>
                <FieldGrid>
                  <Controller control={control} name={`past_performance.${i}.technologies`} render={({ field }) => <TagsInput id={id("tech")} label="Technologies" value={field.value} onChange={field.onChange} />} />
                </FieldGrid>
                <fieldset className="grid gap-3">
                  <legend className="text-xs font-medium text-muted-foreground">Reference contact</legend>
                  <FieldGrid className="sm:grid-cols-4">
                    <Field id={id("refname")} label="Name"><Input {...controlProps(id("refname"))} {...register(`past_performance.${i}.ref_name`)} /></Field>
                    <Field id={id("reftitle")} label="Job title"><Input {...controlProps(id("reftitle"))} {...register(`past_performance.${i}.ref_title`)} /></Field>
                    <Field id={id("refemail")} label="Email"><Input {...controlProps(id("refemail"))} {...register(`past_performance.${i}.ref_email`)} type="email" /></Field>
                    <Field id={id("refphone")} label="Phone"><Input {...controlProps(id("refphone"))} {...register(`past_performance.${i}.ref_phone`)} type="tel" /></Field>
                  </FieldGrid>
                </fieldset>
              </div>
            );
          }}
        />
      </Section>

      <Section title="People" description="Key personnel for staffing plans, and your rate card for pricing placeholders.">
        <RowList
          title={fieldLabel("personnel")}
          rows={people.fields}
          rowLabel="person"
          addLabel="Add person"
          emptyText="No key personnel yet."
          onAdd={() => people.append({ name: "", role: "", years_experience: "", clearances: [], certifications: [], education: "", resume_file_id: "", resume_name: "", is_key_personnel: true })}
          onRemove={(i) => people.remove(i)}
          renderRow={(_, i) => {
            const e = errors.personnel?.[i];
            const id = (k: string) => `f-person-${i}-${k}`;
            const resumeName = watch(`personnel.${i}.resume_name`);
            return (
              <FieldGrid className="sm:grid-cols-3">
                <Field id={id("name")} label="Name" required error={e?.name?.message}><Input {...controlProps(id("name"), e?.name?.message)} {...register(`personnel.${i}.name`)} /></Field>
                <Field id={id("role")} label="Role" required error={e?.role?.message}><Input {...controlProps(id("role"), e?.role?.message)} {...register(`personnel.${i}.role`)} placeholder="Program manager" /></Field>
                <Field id={id("years")} label="Years of experience"><Input {...controlProps(id("years"))} {...register(`personnel.${i}.years_experience`)} type="number" min={0} /></Field>
                <Controller control={control} name={`personnel.${i}.clearances`} render={({ field }) => <TagsInput id={id("clear")} label="Clearances" value={field.value} onChange={field.onChange} placeholder={region === "US" ? "Secret, TS/SCI" : ""} />} />
                <Controller control={control} name={`personnel.${i}.certifications`} render={({ field }) => <TagsInput id={id("certs")} label="Certifications" value={field.value} onChange={field.onChange} placeholder="PMP, AWS SA, CISSP" />} />
                <Field id={id("edu")} label="Education"><Input {...controlProps(id("edu"))} {...register(`personnel.${i}.education`)} /></Field>
                <Field id={id("resume")} label="Résumé file" help={resumeName || undefined}>
                  <Input {...controlProps(id("resume"), undefined, resumeName || undefined)} type="file" accept=".pdf,.docx" disabled={uploading === id("resume")} onChange={(ev) => upload(id("resume"), ev.target.files?.[0], (f) => { setValue(`personnel.${i}.resume_file_id`, f.id); setValue(`personnel.${i}.resume_name`, f.filename); })} />
                </Field>
                <Controller control={control} name={`personnel.${i}.is_key_personnel`} render={({ field }) => <CheckboxField id={id("key")} label="Key personnel" checked={field.value} onChange={field.onChange} />} />
              </FieldGrid>
            );
          }}
        />
        <RowList
          title={fieldLabel("rate_card")}
          description={region === "US" ? "Labor categories with hourly rates." : "Man-month or daily rates per role."}
          rows={rates.fields}
          rowLabel="rate"
          addLabel="Add rate"
          emptyText="No rate card entries yet."
          onAdd={() => rates.append({ labor_category: "", unit: region === "IN" ? "month" : "hour", rate: { amount: "", currency }, min_years_experience: "" })}
          onRemove={(i) => rates.remove(i)}
          renderRow={(_, i) => {
            const e = errors.rate_card?.[i];
            const id = (k: string) => `f-rate-${i}-${k}`;
            return (
              <FieldGrid className="sm:grid-cols-4">
                <Field id={id("cat")} label="Labor category" required error={e?.labor_category?.message}><Input {...controlProps(id("cat"), e?.labor_category?.message)} {...register(`rate_card.${i}.labor_category`)} /></Field>
                <Controller control={control} name={`rate_card.${i}.unit`} render={({ field }) => <SelectField id={id("unit")} label="Unit" required options={optionsForRegion(RATE_UNITS, region)} value={field.value} onChange={(v) => field.onChange(v ?? "hour")} placeholder="Per hour" />} />
                <Controller control={control} name={`rate_card.${i}.rate`} render={({ field }) => <MoneyInput id={id("rate")} label="Rate" required amount={field.value.amount} currency={field.value.currency} onChange={field.onChange} error={e?.rate?.amount?.message} />} />
                <Field id={id("years")} label="Min. years"><Input {...controlProps(id("years"))} {...register(`rate_card.${i}.min_years_experience`)} type="number" min={0} /></Field>
              </FieldGrid>
            );
          }}
        />
      </Section>

      <Section title="Security and quality" description="Certifications and clearances feed eligibility checks and the compliance matrix.">
        <RowList
          title={fieldLabel("certifications.security")}
          rows={certs.fields}
          rowLabel="certification"
          addLabel="Add certification"
          emptyText="No security or quality certifications yet."
          onAdd={() => certs.append({ kind: "", level: "", cert_number: "", expires_on: "" })}
          onRemove={(i) => certs.remove(i)}
          renderRow={(_, i) => {
            const e = errors.security_certs?.[i];
            const id = (k: string) => `f-sec-${i}-${k}`;
            return (
              <FieldGrid className="sm:grid-cols-4">
                <Controller control={control} name={`security_certs.${i}.kind`} render={({ field }) => <SelectField id={id("kind")} label="Certification" required options={optionsForRegion(SECURITY_KINDS, region)} value={field.value} onChange={(v) => field.onChange(v ?? "")} error={e?.kind?.message} />} />
                <Field id={id("level")} label="Level" help="e.g. CMMC 2, CMMI 3, FCL Secret"><Input {...controlProps(id("level"), undefined, "x")} {...register(`security_certs.${i}.level`)} /></Field>
                <Field id={id("number")} label="Certificate number"><Input {...controlProps(id("number"))} {...register(`security_certs.${i}.cert_number`)} /></Field>
                <Field id={id("expires")} label="Expiry"><Input {...controlProps(id("expires"))} {...register(`security_certs.${i}.expires_on`)} type="date" /></Field>
              </FieldGrid>
            );
          }}
        />
        {isFieldAllowed("cleared_personnel_count", region) ? (
          <FieldGrid>
            <Field id="f-cleared" label={fieldLabel("cleared_personnel_count")}>
              <Input {...controlProps("f-cleared")} {...register("cleared_personnel_count")} type="number" min={0} />
            </Field>
          </FieldGrid>
        ) : null}
      </Section>

      <Section title="Insurance" description="General liability, professional liability, cyber and others with limits and expiry.">
        <RowList
          title={fieldLabel("insurance")}
          rows={insurance.fields}
          rowLabel="policy"
          addLabel="Add policy"
          emptyText="No insurance policies yet."
          onAdd={() => insurance.append({ kind: "general_liability", carrier: "", policy_number: "", limit: { amount: "", currency }, expires_on: "" })}
          onRemove={(i) => insurance.remove(i)}
          renderRow={(_, i) => {
            const e = errors.insurance?.[i];
            const id = (k: string) => `f-ins-${i}-${k}`;
            return (
              <FieldGrid className="sm:grid-cols-4">
                <Controller control={control} name={`insurance.${i}.kind`} render={({ field }) => <SelectField id={id("kind")} label="Kind" required options={INSURANCE_KINDS} value={field.value} onChange={(v) => field.onChange(v ?? "")} error={e?.kind?.message} />} />
                <Field id={id("carrier")} label="Carrier"><Input {...controlProps(id("carrier"))} {...register(`insurance.${i}.carrier`)} /></Field>
                <Controller control={control} name={`insurance.${i}.limit`} render={({ field }) => <MoneyInput id={id("limit")} label="Limit" amount={field.value.amount} currency={field.value.currency} onChange={field.onChange} />} />
                <Field id={id("expires")} label="Expiry"><Input {...controlProps(id("expires"))} {...register(`insurance.${i}.expires_on`)} type="date" /></Field>
              </FieldGrid>
            );
          }}
        />
      </Section>

      <Section title="Boilerplate library" description="Reusable narrative blocks: company overview, management approach, QA plan, transition, security, diversity, sustainability.">
        <RowList
          title={fieldLabel("boilerplate")}
          rows={boilerplate.fields}
          rowLabel="boilerplate block"
          addLabel="Add block"
          emptyText="No boilerplate yet."
          onAdd={() => boilerplate.append({ kind: "company_overview", title: "", body: "" })}
          onRemove={(i) => boilerplate.remove(i)}
          renderRow={(_, i) => {
            const e = errors.boilerplate?.[i];
            const id = (k: string) => `f-bp-${i}-${k}`;
            return (
              <div className="grid gap-4">
                <FieldGrid>
                  <Controller control={control} name={`boilerplate.${i}.kind`} render={({ field }) => <SelectField id={id("kind")} label="Kind" required options={BOILERPLATE_KINDS} value={field.value} onChange={(v) => field.onChange(v ?? "")} error={e?.kind?.message} />} />
                  <Field id={id("title")} label="Title" required error={e?.title?.message}><Input {...controlProps(id("title"), e?.title?.message)} {...register(`boilerplate.${i}.title`)} /></Field>
                </FieldGrid>
                <Field id={id("body")} label="Body (Markdown)" required error={e?.body?.message}>
                  <Textarea {...controlProps(id("body"), e?.body?.message)} {...register(`boilerplate.${i}.body`)} rows={6} />
                </Field>
              </div>
            );
          }}
        />
      </Section>

      <Section title="Files" description="Capability statement, brochures and case studies feed the knowledge base; past proposals teach style; brand files drive output formatting.">
        <RowList
          title={fieldLabel("files")}
          rows={files.fields}
          rowLabel="file"
          addLabel="Add file"
          emptyText="No files yet."
          onAdd={() => files.append({ file_id: "", kind: "capability_statement", title: "", filename: "", outcome: "" })}
          onRemove={(i) => files.remove(i)}
          renderRow={(_, i) => {
            const e = errors.files?.[i];
            const id = (k: string) => `f-file-${i}-${k}`;
            const kind = watch(`files.${i}.kind`);
            const filename = watch(`files.${i}.filename`);
            return (
              <FieldGrid className="sm:grid-cols-3">
                <Controller control={control} name={`files.${i}.kind`} render={({ field }) => <SelectField id={id("kind")} label="Kind" required options={PROFILE_FILE_KINDS} value={field.value} onChange={(v) => field.onChange(v ?? "")} error={e?.kind?.message} />} />
                <Field id={id("upload")} label="File" required error={e?.file_id?.message} help={filename ? `Uploaded: ${filename}` : "PDF, DOCX, PPTX, images."}>
                  <Input {...controlProps(id("upload"), e?.file_id?.message, "x")} type="file" disabled={uploading === id("upload")} onChange={(ev) => upload(id("upload"), ev.target.files?.[0], (f) => { setValue(`files.${i}.file_id`, f.id, { shouldValidate: true }); setValue(`files.${i}.filename`, f.filename); })} />
                </Field>
                <Field id={id("title")} label="Title"><Input {...controlProps(id("title"))} {...register(`files.${i}.title`)} /></Field>
                {kind === "past_proposal" ? (
                  <Controller control={control} name={`files.${i}.outcome`} render={({ field }) => <SelectField id={id("outcome")} label="Outcome" options={[{ value: "won", label: "Won" }, { value: "lost", label: "Lost" }, { value: "no_decision", label: "No decision" }]} value={field.value} onChange={(v) => field.onChange(v ?? "")} />} />
                ) : null}
              </FieldGrid>
            );
          }}
        />
        {uploading ? (
          <p role="status" className="text-xs text-muted-foreground">
            Uploading…
          </p>
        ) : null}
      </Section>

      <ErrorBanner error={error} />
      <StepFooter step={5} saving={saving || Boolean(uploading)} onBack={onBack} />
    </form>
  );
}

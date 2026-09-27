"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import * as React from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { z } from "zod";

import { Input } from "@/components/ui/input";
import type { Profile, ProfileCreate, ProfileUpdate } from "@/lib/api/browser";
import type { ApplyOutcome, AutofillSuggestion } from "@/lib/autofill";
import { parseSuggestionField } from "@/lib/autofill";
import {
  createProfile,
  listItems,
  syncCollection,
  updateProfile,
  type ItemOut,
} from "@/lib/onboarding/api";
import {
  ADDRESS_KINDS,
  LEGAL_STRUCTURES,
  LOCAL_SUPPLIER_CLASSES,
  REGISTRATION_KINDS,
  SAM_STATUSES,
  UDYAM_CATEGORIES,
  fieldLabel,
  fieldMeta,
  isFieldAllowed,
  isFieldRequired,
  isMaskedValue,
  optionsForRegion,
  stripRegionForeign,
  toApiRegion,
  type Region,
} from "@/lib/profile-fields";

import { AutofillPanel } from "../autofill-panel";
import {
  ErrorBanner,
  Field,
  FieldGrid,
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

/* ---------------------------------------------------------------- schema */

const optionalText = z.string().trim();
const addressSchema = z.object({
  kind: z.string().min(1, "Choose a kind"),
  line1: z.string().trim().min(1, "Address line is required"),
  line2: optionalText,
  city: z.string().trim().min(1, "City is required"),
  state: optionalText,
  postal_code: optionalText,
  country: z.string().trim().length(2, "Use the 2-letter ISO country code"),
});
const vehicleSchema = z.object({
  id: z.string().optional(),
  vehicle: z.string().trim().min(1, "Vehicle name is required"),
  number: optionalText,
  expires_on: optionalText,
});
const registrationSchema = z.object({
  id: z.string().optional(),
  kind: z.string().min(1, "Choose a kind"),
  identifier: optionalText,
  holder: optionalText,
  portal: optionalText,
  expires_on: optionalText,
});

const maskedOr = (pattern: RegExp, message: string) =>
  optionalText.refine((v) => v === "" || isMaskedValue(v) || pattern.test(v.toUpperCase()), message);

function buildSchema(region: Region) {
  const req = (key: string, message: string) =>
    isFieldRequired(key, region) ? z.string().trim().min(1, message) : optionalText;
  const currentYear = new Date().getFullYear();
  return z.object({
    legal_name: z.string().trim().min(1, "Legal name is required"),
    dba_names: z.array(z.string()),
    addresses: z.array(addressSchema).min(1, "Add at least one address"),
    website: req("website", "Website is required"),
    phone: req("phone", "Phone is required"),
    bid_inbox_email: req("bid_inbox_email", "Bid-inbox email is required").refine(
      (v) => v === "" || z.email().safeParse(v).success,
      "Enter a valid email",
    ),
    year_founded: req("year_founded", "Year founded is required").refine((v) => {
      if (v === "") return true;
      const n = Number(v);
      return Number.isInteger(n) && n >= 1800 && n <= currentYear;
    }, `Enter a year between 1800 and ${currentYear}`),
    legal_structure: req("legal_structure", "Choose a legal structure"),
    // US
    uei: req("uei", "UEI is required").refine((v) => v === "" || /^[A-Z0-9]{12}$/i.test(v), "UEI is 12 letters/digits"),
    cage_code: optionalText.refine((v) => v === "" || /^[A-Z0-9]{5}$/i.test(v), "CAGE is 5 characters"),
    sam_status: req("sam_status", "Choose the SAM status"),
    sam_expires_on: req("sam_expires_on", "SAM expiry date is required"),
    ein: maskedOr(/^\d{2}-?\d{7}$/, "EIN looks like 12-3456789"),
    vehicles: z.array(vehicleSchema),
    // IN
    pan: req("pan", "PAN is required").pipe(maskedOr(/^[A-Z]{5}\d{4}[A-Z]$/, "PAN looks like ABCDE1234F")),
    gstin: req("gstin", "GSTIN is required").pipe(maskedOr(/^[0-9A-Z]{15}$/, "GSTIN is 15 characters")),
    cin_llpin: req("cin_llpin", "CIN / LLPIN is required"),
    tan: req("tan", "TAN is required").pipe(maskedOr(/^[A-Z]{4}\d{5}[A-Z]$/, "TAN looks like ABCD12345E")),
    udyam_number: optionalText,
    udyam_category: optionalText,
    dpiit_number: optionalText,
    gem_seller_id: optionalText,
    local_supplier_class: optionalText,
    local_content_pct: optionalText.refine((v) => {
      if (v === "") return true;
      const n = Number(v);
      return Number.isFinite(n) && n >= 0 && n <= 100;
    }, "Enter a percentage between 0 and 100"),
    registrations: z.array(registrationSchema),
    // banking
    bank_name: optionalText,
    bank_account_number: optionalText,
    bank_routing_code: optionalText,
  });
}

type Values = z.infer<ReturnType<typeof buildSchema>>;

const SENSITIVE = ["ein", "pan", "gstin", "tan", "bank_account_number", "bank_routing_code"] as const;

function defaults(profile: Profile | null, vehicles: ItemOut<"vehicles">[], registrations: ItemOut<"registrations">[]): Values {
  return {
    legal_name: str(profile?.legal_name),
    dba_names: profile?.dba_names ?? [],
    addresses: (profile?.addresses ?? []).map((a) => ({
      kind: a.kind,
      line1: a.line1,
      line2: str(a.line2),
      city: a.city,
      state: str(a.state),
      postal_code: str(a.postal_code),
      country: a.country,
    })),
    website: str(profile?.website),
    phone: str(profile?.phone),
    bid_inbox_email: str(profile?.bid_inbox_email),
    year_founded: str(profile?.year_founded),
    legal_structure: str(profile?.legal_structure),
    uei: str(profile?.uei),
    cage_code: str(profile?.cage_code),
    sam_status: str(profile?.sam_status),
    sam_expires_on: str(profile?.sam_expires_on),
    ein: str(profile?.ein),
    vehicles: vehicles.map((v) => ({ id: v.id, vehicle: v.vehicle, number: str(v.number), expires_on: str(v.expires_on) })),
    pan: str(profile?.pan),
    gstin: str(profile?.gstin),
    cin_llpin: str(profile?.cin_llpin),
    tan: str(profile?.tan),
    udyam_number: str(profile?.udyam_number),
    udyam_category: str(profile?.udyam_category),
    dpiit_number: str(profile?.dpiit_number),
    gem_seller_id: str(profile?.gem_seller_id),
    local_supplier_class: str(profile?.local_supplier_class),
    local_content_pct: str(profile?.local_content_pct),
    registrations: registrations.map((r) => ({
      id: r.id,
      kind: r.kind,
      identifier: str(r.identifier),
      holder: str(r.holder),
      portal: str(r.portal),
      expires_on: str(r.expires_on),
    })),
    bank_name: str(profile?.bank_name),
    bank_account_number: str(profile?.bank_account_number),
    bank_routing_code: str(profile?.bank_routing_code),
  };
}

function toPayload(v: Values, region: Region): ProfileUpdate {
  const payload: Record<string, unknown> = {
    legal_name: v.legal_name.trim(),
    dba_names: v.dba_names,
    addresses: v.addresses.map((a) => ({
      kind: a.kind,
      line1: a.line1.trim(),
      line2: orNull(a.line2),
      city: a.city.trim(),
      state: orNull(a.state),
      postal_code: orNull(a.postal_code),
      country: a.country.trim().toUpperCase(),
    })),
    website: orNull(v.website),
    phone: orNull(v.phone),
    bid_inbox_email: orNull(v.bid_inbox_email),
    year_founded: intOrNull(v.year_founded),
    legal_structure: orNull(v.legal_structure),
    uei: orNull(v.uei)?.toUpperCase() ?? null,
    cage_code: orNull(v.cage_code)?.toUpperCase() ?? null,
    sam_status: orNull(v.sam_status),
    sam_expires_on: orNull(v.sam_expires_on),
    cin_llpin: orNull(v.cin_llpin)?.toUpperCase() ?? null,
    udyam_number: orNull(v.udyam_number),
    udyam_category: orNull(v.udyam_category),
    dpiit_number: orNull(v.dpiit_number),
    gem_seller_id: orNull(v.gem_seller_id),
    local_supplier_class: orNull(v.local_supplier_class),
    local_content_pct: v.local_content_pct.trim() === "" ? null : Number(v.local_content_pct),
    bank_name: orNull(v.bank_name),
  };
  // Encrypted identifiers: send only real new values (masked echoes are no-ops; empty means untouched).
  for (const key of SENSITIVE) {
    const value = v[key].trim();
    if (value && !isMaskedValue(value)) payload[key] = key === "bank_account_number" ? value : value.toUpperCase();
  }
  return stripRegionForeign(payload, region) as ProfileUpdate;
}

/* ------------------------------------------------------------- component */

export function IdentityStep({ profile, region, onBack, onComplete, onProfileChange }: StepProps) {
  const schema = React.useMemo(() => buildSchema(region), [region]);
  const [loading, setLoading] = React.useState(Boolean(profile));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);
  const [serverVehicles, setServerVehicles] = React.useState<ItemOut<"vehicles">[]>([]);
  const [serverRegistrations, setServerRegistrations] = React.useState<ItemOut<"registrations">[]>([]);
  const profileRef = React.useRef<Profile | null>(profile);
  profileRef.current = profile;

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: defaults(profile, [], []),
    mode: "onBlur",
  });
  const { register, control, handleSubmit, formState, reset, setValue, getValues, watch } = form;
  const errors = formState.errors;
  const addresses = useFieldArray({ control, name: "addresses" });
  const vehicles = useFieldArray({ control, name: "vehicles" });
  const registrations = useFieldArray({ control, name: "registrations" });

  React.useEffect(() => {
    if (!profile) return;
    let cancelled = false;
    (async () => {
      try {
        const [v, r] = await Promise.all([
          region === "US" ? listItems(profile.id, "vehicles") : Promise.resolve([]),
          region === "IN" ? listItems(profile.id, "registrations") : Promise.resolve([]),
        ]);
        if (cancelled) return;
        setServerVehicles(v);
        setServerRegistrations(r);
        reset(defaults(profile, v, r));
      } catch (e) {
        if (!cancelled) setError(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // Only on first mount with an existing profile; autofill updates are merged via onApplied.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const show = (key: string) => isFieldAllowed(key, region);
  const required = (key: string) => isFieldRequired(key, region);
  const label = (key: string) => fieldLabel(key);
  const help = (key: string) => fieldMeta(key)?.help;

  /** Creates the draft profile for autofill when it does not exist yet. */
  const ensureProfile = React.useCallback(async (): Promise<Profile> => {
    if (profileRef.current) return profileRef.current;
    const legalName = getValues("legal_name").trim();
    if (!legalName) {
      form.setError("legal_name", { message: "Enter the legal name before running autofill" });
      throw new Error("Enter the legal name first.");
    }
    const created = await createProfile({ legal_name: legalName, region: toApiRegion(region) });
    profileRef.current = created;
    onProfileChange(created);
    return created;
  }, [form, getValues, onProfileChange, region]);

  const onApplied = (s: AutofillSuggestion, outcome: ApplyOutcome) => {
    if (outcome.profile) {
      profileRef.current = outcome.profile;
      onProfileChange(outcome.profile);
      const { root, isList } = parseSuggestionField(s.field);
      const value = (outcome.profile as unknown as Record<string, unknown>)[root];
      if (root === "addresses" && isList) {
        reset({ ...getValues(), addresses: defaults(outcome.profile, [], []).addresses });
      } else if (root === "dba_names") {
        setValue("dba_names", Array.isArray(value) ? (value as string[]) : []);
      } else if (root in getValues() && typeof value !== "object") {
        setValue(root as keyof Values, str(value) as never, { shouldDirty: true });
      }
    }
    if (outcome.resource === "vehicles" && profileRef.current) {
      listItems(profileRef.current.id, "vehicles").then((v) => {
        setServerVehicles(v);
        setValue("vehicles", defaults(null, v, []).vehicles);
      });
    }
    if (outcome.resource === "registrations" && profileRef.current) {
      listItems(profileRef.current.id, "registrations").then((r) => {
        setServerRegistrations(r);
        setValue("registrations", defaults(null, [], r).registrations);
      });
    }
  };

  const onSubmit = handleSubmit(async (values) => {
    setSaving(true);
    setError(null);
    try {
      const payload = toPayload(values, region);
      let saved: Profile;
      if (profileRef.current) {
        saved = await updateProfile(profileRef.current.id, payload);
      } else {
        saved = await createProfile({ ...(payload as ProfileCreate), region: toApiRegion(region) });
      }
      profileRef.current = saved;
      onProfileChange(saved);
      if (region === "US") {
        const fresh = await syncCollection(
          saved.id,
          "vehicles",
          serverVehicles,
          values.vehicles.map((v) => ({ id: v.id, vehicle: v.vehicle.trim(), number: orNull(v.number), expires_on: orNull(v.expires_on) })),
        );
        setServerVehicles(fresh);
      } else {
        const fresh = await syncCollection(
          saved.id,
          "registrations",
          serverRegistrations,
          values.registrations.map((r) => ({
            id: r.id,
            kind: r.kind as ItemOut<"registrations">["kind"],
            identifier: orNull(r.identifier),
            holder: orNull(r.holder),
            portal: orNull(r.portal),
            expires_on: orNull(r.expires_on),
          })),
        );
        setServerRegistrations(fresh);
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
        Loading identity…
      </p>
    );
  }

  const text = (key: keyof Values & string, opts: { type?: string; placeholder?: string; mono?: boolean } = {}) => (
    <Field id={`f-${key}`} label={label(key)} required={required(key)} help={help(key)} error={errors[key]?.message as string | undefined}>
      <Input
        {...controlProps(`f-${key}`, errors[key]?.message as string | undefined, help(key))}
        {...register(key)}
        type={opts.type ?? "text"}
        placeholder={opts.placeholder}
        className={opts.mono ? "font-mono" : undefined}
      />
    </Field>
  );
  const masked = (key: keyof Values & string, placeholder: string) => (
    <Field
      id={`f-${key}`}
      label={label(key)}
      required={required(key)}
      help="Stored encrypted; only the last 4 characters are shown after saving."
      error={errors[key]?.message as string | undefined}
    >
      <Input
        {...controlProps(`f-${key}`, errors[key]?.message as string | undefined, "x")}
        {...register(key)}
        placeholder={placeholder}
        autoComplete="off"
        className="font-mono"
      />
    </Field>
  );

  const websiteValue = watch("website");
  const ueiValue = watch("uei");

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-6" aria-label="Identity and registrations">
      <AutofillPanel
        region={region}
        profile={profile}
        ensureProfile={ensureProfile}
        onApplied={onApplied}
        websiteDefault={websiteValue}
        ueiDefault={region === "US" ? ueiValue : undefined}
      />

      <Section title="Identity" description="Legal identity used on cover letters and forms.">
        <FieldGrid>
          {text("legal_name", { placeholder: "Acme Federal Services LLC" })}
          <Controller
            control={control}
            name="dba_names"
            render={({ field }) => (
              <TagsInput id="f-dba_names" label={label("dba_names")} value={field.value} onChange={field.onChange} />
            )}
          />
          {text("website", { type: "url", placeholder: "https://www.example.com" })}
          {text("phone", { type: "tel" })}
          {text("bid_inbox_email", { type: "email", placeholder: "bids@example.com" })}
          {text("year_founded", { type: "number", placeholder: "2010" })}
          <Controller
            control={control}
            name="legal_structure"
            render={({ field }) => (
              <SelectField
                id="f-legal_structure"
                label={label("legal_structure")}
                required={required("legal_structure")}
                options={optionsForRegion(LEGAL_STRUCTURES, region)}
                value={field.value}
                onChange={(v) => field.onChange(v ?? "")}
                error={errors.legal_structure?.message}
              />
            )}
          />
        </FieldGrid>
        <RowList
          title={label("addresses")}
          description="Registered address, headquarters and branch offices."
          rows={addresses.fields}
          rowLabel="address"
          addLabel="Add address"
          onAdd={() => addresses.append({ kind: addresses.fields.length ? "branch" : "registered", line1: "", line2: "", city: "", state: "", postal_code: "", country: region })}
          onRemove={(i) => addresses.remove(i)}
          renderRow={(_, i) => {
            const e = errors.addresses?.[i];
            const id = (k: string) => `f-addresses-${i}-${k}`;
            return (
              <FieldGrid className="sm:grid-cols-3">
                <Controller
                  control={control}
                  name={`addresses.${i}.kind`}
                  render={({ field }) => (
                    <SelectField id={id("kind")} label="Kind" options={ADDRESS_KINDS} value={field.value} onChange={(v) => field.onChange(v ?? "")} error={e?.kind?.message} required />
                  )}
                />
                <Field id={id("line1")} label="Address line 1" required error={e?.line1?.message} className="sm:col-span-2">
                  <Input {...controlProps(id("line1"), e?.line1?.message)} {...register(`addresses.${i}.line1`)} />
                </Field>
                <Field id={id("line2")} label="Address line 2" error={e?.line2?.message}>
                  <Input {...controlProps(id("line2"))} {...register(`addresses.${i}.line2`)} />
                </Field>
                <Field id={id("city")} label="City" required error={e?.city?.message}>
                  <Input {...controlProps(id("city"), e?.city?.message)} {...register(`addresses.${i}.city`)} />
                </Field>
                <Field id={id("state")} label={region === "IN" ? "State / UT" : "State"} error={e?.state?.message}>
                  <Input {...controlProps(id("state"))} {...register(`addresses.${i}.state`)} />
                </Field>
                <Field id={id("postal_code")} label={region === "IN" ? "PIN code" : "ZIP code"} error={e?.postal_code?.message}>
                  <Input {...controlProps(id("postal_code"))} {...register(`addresses.${i}.postal_code`)} />
                </Field>
                <Field id={id("country")} label="Country (ISO-2)" required error={e?.country?.message}>
                  <Input {...controlProps(id("country"), e?.country?.message)} {...register(`addresses.${i}.country`)} maxLength={2} className="uppercase" />
                </Field>
              </FieldGrid>
            );
          }}
        />
        {errors.addresses?.root?.message || (typeof errors.addresses?.message === "string" ? errors.addresses.message : null) ? (
          <p role="alert" className="text-xs text-destructive">
            {errors.addresses?.root?.message ?? (errors.addresses?.message as string)}
          </p>
        ) : null}
      </Section>

      {show("uei") ? (
        <Section title="US registrations" description="SAM.gov identity and contract vehicles.">
          <FieldGrid>
            {text("uei", { mono: true, placeholder: "ABC123DEF456" })}
            {text("cage_code", { mono: true, placeholder: "1ABC2" })}
            <Controller
              control={control}
              name="sam_status"
              render={({ field }) => (
                <SelectField id="f-sam_status" label={label("sam_status")} required={required("sam_status")} options={SAM_STATUSES} value={field.value} onChange={(v) => field.onChange(v ?? "")} error={errors.sam_status?.message} />
              )}
            />
            {text("sam_expires_on", { type: "date" })}
            {masked("ein", "12-3456789")}
          </FieldGrid>
          <RowList
            title={label("vehicles")}
            rows={vehicles.fields}
            rowLabel="vehicle"
            addLabel="Add vehicle"
            emptyText="No contract vehicles yet."
            onAdd={() => vehicles.append({ vehicle: "", number: "", expires_on: "" })}
            onRemove={(i) => vehicles.remove(i)}
            renderRow={(_, i) => {
              const e = errors.vehicles?.[i];
              const id = (k: string) => `f-vehicles-${i}-${k}`;
              return (
                <FieldGrid className="sm:grid-cols-3">
                  <Field id={id("vehicle")} label="Vehicle" required error={e?.vehicle?.message}>
                    <Input {...controlProps(id("vehicle"), e?.vehicle?.message)} {...register(`vehicles.${i}.vehicle`)} placeholder="GSA MAS" />
                  </Field>
                  <Field id={id("number")} label="Contract number">
                    <Input {...controlProps(id("number"))} {...register(`vehicles.${i}.number`)} />
                  </Field>
                  <Field id={id("expires_on")} label="Expiry">
                    <Input {...controlProps(id("expires_on"))} {...register(`vehicles.${i}.expires_on`)} type="date" />
                  </Field>
                </FieldGrid>
              );
            }}
          />
        </Section>
      ) : null}

      {show("pan") ? (
        <Section title="India registrations" description="Statutory identifiers, MSME status and portal enrolments.">
          <FieldGrid>
            {masked("pan", "ABCDE1234F")}
            {masked("gstin", "22ABCDE1234F1Z5")}
            {text("cin_llpin", { mono: true })}
            {masked("tan", "ABCD12345E")}
            {text("udyam_number", { mono: true, placeholder: "UDYAM-XX-00-0000000" })}
            <Controller
              control={control}
              name="udyam_category"
              render={({ field }) => (
                <SelectField id="f-udyam_category" label={label("udyam_category")} options={UDYAM_CATEGORIES} value={field.value} onChange={(v) => field.onChange(v ?? "")} />
              )}
            />
            {text("dpiit_number", { mono: true })}
            {text("gem_seller_id", { mono: true })}
            <Controller
              control={control}
              name="local_supplier_class"
              render={({ field }) => (
                <SelectField id="f-local_supplier_class" label={label("local_supplier_class")} options={LOCAL_SUPPLIER_CLASSES} value={field.value} onChange={(v) => field.onChange(v ?? "")} />
              )}
            />
            {text("local_content_pct", { type: "number", placeholder: "50" })}
          </FieldGrid>
          <RowList
            title={label("registrations")}
            description={help("registrations")}
            rows={registrations.fields}
            rowLabel="registration"
            addLabel="Add enrolment"
            emptyText="No portal enrolments or DSC yet."
            onAdd={() => registrations.append({ kind: "gem", identifier: "", holder: "", portal: "", expires_on: "" })}
            onRemove={(i) => registrations.remove(i)}
            renderRow={(_, i) => {
              const e = errors.registrations?.[i];
              const id = (k: string) => `f-registrations-${i}-${k}`;
              const kind = watch(`registrations.${i}.kind`);
              return (
                <FieldGrid className="sm:grid-cols-3">
                  <Controller
                    control={control}
                    name={`registrations.${i}.kind`}
                    render={({ field }) => (
                      <SelectField id={id("kind")} label="Kind" required options={optionsForRegion(REGISTRATION_KINDS, region)} value={field.value} onChange={(v) => field.onChange(v ?? "")} error={e?.kind?.message} />
                    )}
                  />
                  <Field id={id("identifier")} label={kind === "dsc" ? "Certificate serial" : "Enrolment ID"}>
                    <Input {...controlProps(id("identifier"))} {...register(`registrations.${i}.identifier`)} />
                  </Field>
                  <Field id={id("holder")} label={kind === "dsc" ? "DSC holder name" : "Registered holder"}>
                    <Input {...controlProps(id("holder"))} {...register(`registrations.${i}.holder`)} />
                  </Field>
                  <Field id={id("portal")} label="Portal">
                    <Input {...controlProps(id("portal"))} {...register(`registrations.${i}.portal`)} placeholder={kind === "state_portal" ? "e.g. Telangana eProcurement" : ""} />
                  </Field>
                  <Field id={id("expires_on")} label="Expiry" help={kind === "dsc" ? "An expired DSC blocks submission." : undefined}>
                    <Input {...controlProps(id("expires_on"), undefined, kind === "dsc" ? "x" : undefined)} {...register(`registrations.${i}.expires_on`)} type="date" />
                  </Field>
                </FieldGrid>
              );
            }}
          />
        </Section>
      ) : null}

      <Section title="Banking (optional)" description="Used for EMD / bank guarantee forms. Stored encrypted and masked.">
        <FieldGrid className="sm:grid-cols-3">
          {text("bank_name")}
          {masked("bank_account_number", "")}
          {masked("bank_routing_code", region === "IN" ? "IFSC" : "Routing number")}
        </FieldGrid>
      </Section>

      <ErrorBanner error={error} />
      <StepFooter step={1} saving={saving} onBack={onBack} nextLabel={profile ? "Save and continue" : "Create profile and continue"} />
    </form>
  );
}

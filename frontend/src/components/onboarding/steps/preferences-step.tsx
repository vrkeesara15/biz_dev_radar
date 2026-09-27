"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import * as React from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Profile, ProfileUpdate } from "@/lib/api/browser";
import {
  getNotificationPrefs,
  putNotificationPrefs,
  updateProfile,
  type NotificationPrefs,
} from "@/lib/onboarding/api";
import {
  APPROVER_ROLES,
  BID_NO_BID_WEIGHT_KEYS,
  NOTIFICATION_CHANNELS,
  NOTIFICATION_EVENTS,
  OUTPUT_LANGUAGES,
  SCORING_WEIGHT_KEYS,
  fieldLabel,
  optionsForRegion,
} from "@/lib/profile-fields";

import {
  CheckboxGroup,
  ErrorBanner,
  Field,
  FieldGrid,
  Section,
  StepFooter,
  controlProps,
  orNull,
  str,
} from "../form";
import type { StepProps } from "../types";

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
const weights = (keys: readonly { value: string }[]) =>
  z
    .record(z.string(), z.string())
    .refine((w) => keys.every((k) => Number.isInteger(Number(w[k.value])) && Number(w[k.value]) >= 0), "Whole numbers, 0 or more")
    .refine((w) => keys.reduce((sum, k) => sum + Number(w[k.value] || 0), 0) === 100, "Weights must add up to 100");

const schema = z
  .object({
    channels_by_event: z.record(z.string(), z.array(z.string())),
    quiet_hours_start: z.string().trim().refine((v) => v === "" || HHMM.test(v), "Use HH:MM"),
    quiet_hours_end: z.string().trim().refine((v) => v === "" || HHMM.test(v), "Use HH:MM"),
    tz: z.string().trim().min(1, "Time zone is required"),
    digest_time: z.string().trim().regex(HHMM, "Use HH:MM"),
    min_score_instant: z.string().refine((v) => Number.isInteger(Number(v)) && Number(v) >= 0 && Number(v) <= 100, "0–100"),
    min_score_digest: z.string().refine((v) => Number.isInteger(Number(v)) && Number(v) >= 0 && Number(v) <= 100, "0–100"),
    scoring_weights: weights(SCORING_WEIGHT_KEYS),
    bid_no_bid_weights: weights(BID_NO_BID_WEIGHT_KEYS),
    required_approver_roles: z.array(z.string()).min(1, "Choose at least one approver role"),
    output_languages: z.array(z.string()).min(1, "Choose at least one language"),
  })
  .refine((v) => Number(v.min_score_digest) <= Number(v.min_score_instant), {
    message: "Digest threshold must not exceed the instant threshold",
    path: ["min_score_digest"],
  })
  .refine((v) => (v.quiet_hours_start === "") === (v.quiet_hours_end === ""), {
    message: "Set both quiet-hour bounds or neither",
    path: ["quiet_hours_end"],
  });
type Values = z.infer<typeof schema>;

const toStrings = (o: Record<string, unknown>) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, str(v)]));

function defaults(profile: Profile, prefs: NotificationPrefs | null): Values {
  const channels: Record<string, string[]> = {};
  for (const ev of NOTIFICATION_EVENTS) {
    const fromApi = (prefs?.channels_by_event as Record<string, string[]> | undefined)?.[ev.value];
    channels[ev.value] = fromApi ?? ["email"];
  }
  return {
    channels_by_event: channels,
    quiet_hours_start: str(prefs?.quiet_hours_start),
    quiet_hours_end: str(prefs?.quiet_hours_end),
    tz: prefs?.tz ?? (Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"),
    digest_time: prefs?.digest_time ?? "08:00",
    min_score_instant: str(prefs?.min_score_instant ?? 70),
    min_score_digest: str(prefs?.min_score_digest ?? 50),
    scoring_weights: toStrings(profile.scoring_weights as Record<string, unknown>),
    bid_no_bid_weights: toStrings(profile.bid_no_bid_weights as Record<string, unknown>),
    required_approver_roles: profile.required_approver_roles ?? ["bid_manager"],
    output_languages: profile.output_languages?.length ? profile.output_languages : ["en"],
  };
}

function WeightsTable({
  id,
  legend,
  keys,
  value,
  onChange,
  error,
}: {
  id: string;
  legend: string;
  keys: readonly { value: string; label: string }[];
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  error?: string;
}) {
  const total = keys.reduce((sum, k) => sum + (Number(value[k.value]) || 0), 0);
  return (
    <fieldset className="grid gap-2" aria-describedby={`${id}-total`}>
      <legend className="text-sm font-medium">{legend}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {keys.map((k) => (
          <div key={k.value} className="flex items-center justify-between gap-3">
            <Label htmlFor={`${id}-${k.value}`} className="font-normal">
              {k.label}
            </Label>
            <Input
              id={`${id}-${k.value}`}
              type="number"
              min={0}
              max={100}
              step={1}
              className="w-20 text-right tabular-nums"
              value={value[k.value] ?? ""}
              onChange={(e) => onChange({ ...value, [k.value]: e.target.value })}
            />
          </div>
        ))}
      </div>
      <p id={`${id}-total`} role={error ? "alert" : undefined} className={error ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
        {error ?? `Total ${total} of 100`}
      </p>
    </fieldset>
  );
}

export function PreferencesStep({ profile, region, onBack, onComplete, onProfileChange }: StepProps & { profile: Profile }) {
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);

  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults(profile, null), mode: "onBlur" });
  const { register, control, handleSubmit, formState, reset, watch, setValue } = form;
  const errors = formState.errors;

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const prefs = await getNotificationPrefs();
        if (!cancelled) reset(defaults(profile, prefs));
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
      await putNotificationPrefs({
        channels_by_event: values.channels_by_event,
        quiet_hours_start: orNull(values.quiet_hours_start),
        quiet_hours_end: orNull(values.quiet_hours_end),
        tz: values.tz,
        digest_time: values.digest_time,
        min_score_instant: Number(values.min_score_instant),
        min_score_digest: Number(values.min_score_digest),
      });
      const toInts = (o: Record<string, string>) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, Number(v)]));
      const payload: ProfileUpdate = {
        scoring_weights: toInts(values.scoring_weights),
        bid_no_bid_weights: toInts(values.bid_no_bid_weights),
        required_approver_roles: values.required_approver_roles as ProfileUpdate["required_approver_roles"],
        output_languages: values.output_languages,
      };
      const saved = await updateProfile(profile.id, payload);
      onProfileChange(saved);
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
        Loading preferences…
      </p>
    );
  }

  const channels = watch("channels_by_event");

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-6" aria-label="Preferences">
      <Section title="Alerts" description="Channels per event, quiet hours and the digest time. These are your personal settings.">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Alert channels per event</caption>
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th scope="col" className="py-1 pr-3 font-medium">Event</th>
                {NOTIFICATION_CHANNELS.map((c) => (
                  <th key={c.value} scope="col" className="px-2 py-1 text-center font-medium">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {NOTIFICATION_EVENTS.map((ev) => (
                <tr key={ev.value} className="border-t">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                    {ev.label}
                  </th>
                  {NOTIFICATION_CHANNELS.map((c) => {
                    const id = `f-ch-${ev.value}-${c.value}`;
                    const on = channels[ev.value]?.includes(c.value) ?? false;
                    return (
                      <td key={c.value} className="px-2 py-1.5 text-center">
                        <Checkbox
                          id={id}
                          aria-label={`${ev.label} via ${c.label}`}
                          checked={on}
                          onChange={(e) => {
                            const current = channels[ev.value] ?? [];
                            setValue(
                              `channels_by_event.${ev.value}`,
                              e.target.checked ? [...new Set([...current, c.value])] : current.filter((x) => x !== c.value),
                              { shouldDirty: true },
                            );
                          }}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <FieldGrid className="sm:grid-cols-4">
          <Field id="f-quiet_start" label="Quiet hours start" error={errors.quiet_hours_start?.message}>
            <Input {...controlProps("f-quiet_start", errors.quiet_hours_start?.message)} {...register("quiet_hours_start")} type="time" />
          </Field>
          <Field id="f-quiet_end" label="Quiet hours end" error={errors.quiet_hours_end?.message}>
            <Input {...controlProps("f-quiet_end", errors.quiet_hours_end?.message)} {...register("quiet_hours_end")} type="time" />
          </Field>
          <Field id="f-tz" label="Time zone" required error={errors.tz?.message} help="IANA name, e.g. America/New_York or Asia/Kolkata">
            <Input {...controlProps("f-tz", errors.tz?.message, "x")} {...register("tz")} />
          </Field>
          <Field id="f-digest_time" label="Digest time" required error={errors.digest_time?.message}>
            <Input {...controlProps("f-digest_time", errors.digest_time?.message)} {...register("digest_time")} type="time" />
          </Field>
        </FieldGrid>
      </Section>

      <Section title="Thresholds" description="Minimum fit score for an instant alert (default 70) and for the digest (default 50).">
        <FieldGrid>
          <Field id="f-min_instant" label="Instant alert minimum score" error={errors.min_score_instant?.message}>
            <Input {...controlProps("f-min_instant", errors.min_score_instant?.message)} {...register("min_score_instant")} type="number" min={0} max={100} />
          </Field>
          <Field id="f-min_digest" label="Digest minimum score" error={errors.min_score_digest?.message}>
            <Input {...controlProps("f-min_digest", errors.min_score_digest?.message)} {...register("min_score_digest")} type="number" min={0} max={100} />
          </Field>
        </FieldGrid>
      </Section>

      <Section title="Weights" description="How opportunities are scored and how bid/no-bid is decided. Each set must total 100.">
        <Controller
          control={control}
          name="scoring_weights"
          render={({ field }) => <WeightsTable id="f-sw" legend={fieldLabel("scoring_weights")} keys={SCORING_WEIGHT_KEYS} value={field.value} onChange={field.onChange} error={errors.scoring_weights?.message as string | undefined} />}
        />
        <Controller
          control={control}
          name="bid_no_bid_weights"
          render={({ field }) => <WeightsTable id="f-bw" legend={fieldLabel("bid_no_bid_weights")} keys={BID_NO_BID_WEIGHT_KEYS} value={field.value} onChange={field.onChange} error={errors.bid_no_bid_weights?.message as string | undefined} />}
        />
      </Section>

      <Section title="Approvals and languages">
        <Controller
          control={control}
          name="required_approver_roles"
          render={({ field }) => <CheckboxGroup idPrefix="f-approver" legend={fieldLabel("required_approver_roles")} options={APPROVER_ROLES} value={field.value} onChange={field.onChange} error={errors.required_approver_roles?.message} help="Roles that must approve before submission." />}
        />
        <Controller
          control={control}
          name="output_languages"
          render={({ field }) => <CheckboxGroup idPrefix="f-lang" legend={fieldLabel("output_languages")} options={optionsForRegion(OUTPUT_LANGUAGES, region)} value={field.value} onChange={field.onChange} error={errors.output_languages?.message} />}
        />
      </Section>

      <ErrorBanner error={error} />
      <StepFooter step={6} saving={saving} onBack={onBack} />
    </form>
  );
}

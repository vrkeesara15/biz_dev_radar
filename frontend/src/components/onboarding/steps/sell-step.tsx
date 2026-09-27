"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import * as React from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { z } from "zod";

import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Profile } from "@/lib/api/browser";
import { listItems, syncCollection, type ItemOut } from "@/lib/onboarding/api";
import {
  CODE_SCHEMES,
  DELIVERY_MODELS,
  KEYWORD_KINDS,
  fieldLabel,
  optionsForRegion,
} from "@/lib/profile-fields";

import {
  CheckboxField,
  ErrorBanner,
  Field,
  FieldGrid,
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

const wordCount = (text: string) => text.trim().split(/\s+/).filter(Boolean).length;

const schema = z.object({
  codes: z.array(
    z.object({
      id: z.string().optional(),
      scheme: z.string().min(1, "Choose a scheme"),
      code: z.string().trim().min(1, "Enter a code"),
      is_primary: z.boolean(),
    }),
  ),
  keywords: z.array(
    z.object({
      id: z.string().optional(),
      kind: z.enum(["include", "exclude"]),
      term: z.string().trim().min(1, "Enter a term").max(100),
      weight: z.string().refine((v) => {
        const n = Number(v);
        return Number.isFinite(n) && n >= 0.1 && n <= 5;
      }, "Weight between 0.1 and 5.0"),
    }),
  ),
  service_lines: z.array(
    z.object({
      id: z.string().optional(),
      name: z.string().trim().min(1, "Name is required").max(200),
      description: z
        .string()
        .trim()
        .min(1, "Description is required")
        .refine((v) => wordCount(v) <= 150, "Keep the description to 150 words"),
      differentiators: z.array(z.string()),
      tools: z.array(z.string()),
      delivery_model: z.string(),
    }),
  ),
});
type Values = z.infer<typeof schema>;

type Lists = {
  codes: ItemOut<"codes">[];
  keywords: ItemOut<"keywords">[];
  lines: ItemOut<"service-lines">[];
};

function defaults(l: Lists): Values {
  return {
    codes: l.codes.map((c) => ({ id: c.id, scheme: c.scheme, code: c.code, is_primary: c.is_primary })),
    keywords: l.keywords.map((k) => ({ id: k.id, kind: k.kind, term: k.term, weight: str(k.weight) })),
    service_lines: l.lines.map((s) => ({
      id: s.id,
      name: s.name,
      description: s.description,
      differentiators: s.differentiators,
      tools: s.tools,
      delivery_model: str(s.delivery_model),
    })),
  };
}

export function SellStep({ profile, region, onBack, onComplete }: StepProps & { profile: Profile }) {
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);
  const [server, setServer] = React.useState<Lists>({ codes: [], keywords: [], lines: [] });
  const schemes = optionsForRegion(CODE_SCHEMES, region);

  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults(server), mode: "onBlur" });
  const { register, control, handleSubmit, formState, reset, watch } = form;
  const errors = formState.errors;
  const codes = useFieldArray({ control, name: "codes" });
  const keywords = useFieldArray({ control, name: "keywords" });
  const lines = useFieldArray({ control, name: "service_lines" });

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [c, k, s] = await Promise.all([
          listItems(profile.id, "codes"),
          listItems(profile.id, "keywords"),
          listItems(profile.id, "service-lines"),
        ]);
        if (cancelled) return;
        const next = { codes: c, keywords: k, lines: s };
        setServer(next);
        reset(defaults(next));
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
      // Codes and keywords can only update `is_primary` / `weight` in place; a
      // changed scheme/code or kind/term becomes delete + create.
      const codeById = new Map(server.codes.map((c) => [c.id, c]));
      const codeDrafts = values.codes.map((c) => {
        const prev = c.id ? codeById.get(c.id) : undefined;
        const same = prev && prev.scheme === c.scheme && prev.code === c.code.trim();
        return { id: same ? c.id : undefined, scheme: c.scheme as ItemOut<"codes">["scheme"], code: c.code.trim(), is_primary: c.is_primary };
      });
      const kwById = new Map(server.keywords.map((k) => [k.id, k]));
      const kwDrafts = values.keywords.map((k) => {
        const prev = k.id ? kwById.get(k.id) : undefined;
        const same = prev && prev.kind === k.kind && prev.term === k.term.trim();
        return { id: same ? k.id : undefined, kind: k.kind, term: k.term.trim(), weight: Number(k.weight) };
      });
      const lineDrafts = values.service_lines.map((s) => ({
        id: s.id,
        name: s.name.trim(),
        description: s.description.trim(),
        differentiators: s.differentiators,
        tools: s.tools,
        delivery_model: orNull(s.delivery_model) as ItemOut<"service-lines">["delivery_model"],
      }));
      const [c, k, s] = [
        await syncCollection(profile.id, "codes", server.codes, codeDrafts),
        await syncCollection(profile.id, "keywords", server.keywords, kwDrafts),
        await syncCollection(profile.id, "service-lines", server.lines, lineDrafts),
      ];
      setServer({ codes: c, keywords: k, lines: s });
      await onComplete(null);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  });

  if (loading) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading offerings…
      </p>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-6" aria-label="What we sell">
      <Section
        title="Codes"
        description={region === "US" ? "NAICS (primary + secondary), PSC and ALN/CFDA programs for SAM and Grants.gov matching." : "GeM categories and Indian product categories for GeM and CPPP matching."}
      >
        <RowList
          title="Classification codes"
          rows={codes.fields}
          rowLabel="code"
          addLabel="Add code"
          emptyText="No codes yet. Matching needs at least one."
          onAdd={() => codes.append({ scheme: schemes[0]?.value ?? "", code: "", is_primary: codes.fields.length === 0 })}
          onRemove={(i) => codes.remove(i)}
          renderRow={(_, i) => {
            const e = errors.codes?.[i];
            return (
              <FieldGrid className="sm:grid-cols-3">
                <Controller
                  control={control}
                  name={`codes.${i}.scheme`}
                  render={({ field }) => (
                    <SelectField id={`f-code-${i}-scheme`} label="Scheme" required options={schemes} value={field.value} onChange={(v) => field.onChange(v ?? "")} error={e?.scheme?.message} />
                  )}
                />
                <Field id={`f-code-${i}-code`} label="Code" required error={e?.code?.message} help={watch(`codes.${i}.scheme`) === "naics" ? "Six digits, 2022 NAICS" : undefined}>
                  <Input {...controlProps(`f-code-${i}-code`, e?.code?.message, watch(`codes.${i}.scheme`) === "naics" ? "x" : undefined)} {...register(`codes.${i}.code`)} className="font-mono" placeholder={watch(`codes.${i}.scheme`) === "naics" ? "541511" : ""} />
                </Field>
                <Controller
                  control={control}
                  name={`codes.${i}.is_primary`}
                  render={({ field }) => (
                    <CheckboxField id={`f-code-${i}-primary`} label="Primary for this scheme" checked={field.value} onChange={field.onChange} />
                  )}
                />
              </FieldGrid>
            );
          }}
        />
      </Section>

      <Section title="Keywords" description="Include keywords boost scoring; exclude keywords filter noise. Weight 0.1–5.0 (1.0 = normal).">
        <RowList
          title={fieldLabel("keywords")}
          rows={keywords.fields}
          rowLabel="keyword"
          addLabel="Add keyword"
          emptyText="No keywords yet."
          onAdd={() => keywords.append({ kind: "include", term: "", weight: "1.0" })}
          onRemove={(i) => keywords.remove(i)}
          renderRow={(_, i) => {
            const e = errors.keywords?.[i];
            return (
              <FieldGrid className="sm:grid-cols-3">
                <Controller
                  control={control}
                  name={`keywords.${i}.kind`}
                  render={({ field }) => (
                    <SelectField id={`f-kw-${i}-kind`} label="Kind" required options={KEYWORD_KINDS} value={field.value} onChange={(v) => field.onChange(v ?? "include")} placeholder="Include" />
                  )}
                />
                <Field id={`f-kw-${i}-term`} label="Term" required error={e?.term?.message}>
                  <Input {...controlProps(`f-kw-${i}-term`, e?.term?.message)} {...register(`keywords.${i}.term`)} maxLength={100} />
                </Field>
                <Field id={`f-kw-${i}-weight`} label="Weight" error={e?.weight?.message}>
                  <Input {...controlProps(`f-kw-${i}-weight`, e?.weight?.message)} {...register(`keywords.${i}.weight`)} type="number" min={0.1} max={5} step={0.1} />
                </Field>
              </FieldGrid>
            );
          }}
        />
      </Section>

      <Section title="Service lines" description="Each line: name, a description of up to 150 words, differentiators, tools/platforms and delivery model. Used for matching and drafting.">
        <RowList
          title={fieldLabel("service_lines")}
          rows={lines.fields}
          rowLabel="service line"
          addLabel="Add service line"
          emptyText="No service lines yet. Three or more score best."
          onAdd={() => lines.append({ name: "", description: "", differentiators: [], tools: [], delivery_model: "" })}
          onRemove={(i) => lines.remove(i)}
          renderRow={(_, i) => {
            const e = errors.service_lines?.[i];
            const words = wordCount(watch(`service_lines.${i}.description`) ?? "");
            return (
              <div className="grid gap-4">
                <FieldGrid>
                  <Field id={`f-sl-${i}-name`} label="Name" required error={e?.name?.message}>
                    <Input {...controlProps(`f-sl-${i}-name`, e?.name?.message)} {...register(`service_lines.${i}.name`)} />
                  </Field>
                  <Controller
                    control={control}
                    name={`service_lines.${i}.delivery_model`}
                    render={({ field }) => (
                      <SelectField id={`f-sl-${i}-delivery`} label="Delivery model" options={DELIVERY_MODELS} value={field.value} onChange={(v) => field.onChange(v ?? "")} />
                    )}
                  />
                </FieldGrid>
                <Field id={`f-sl-${i}-description`} label="Description" required error={e?.description?.message} help={`${words}/150 words`}>
                  <Textarea {...controlProps(`f-sl-${i}-description`, e?.description?.message, "x")} {...register(`service_lines.${i}.description`)} rows={4} />
                </Field>
                <FieldGrid>
                  <Controller
                    control={control}
                    name={`service_lines.${i}.differentiators`}
                    render={({ field }) => <TagsInput id={`f-sl-${i}-diff`} label="Differentiators" value={field.value} onChange={field.onChange} />}
                  />
                  <Controller
                    control={control}
                    name={`service_lines.${i}.tools`}
                    render={({ field }) => <TagsInput id={`f-sl-${i}-tools`} label="Tools / platforms" value={field.value} onChange={field.onChange} />}
                  />
                </FieldGrid>
              </div>
            );
          }}
        />
      </Section>

      <ErrorBanner error={error} />
      <StepFooter step={3} saving={saving} onBack={onBack} />
    </form>
  );
}

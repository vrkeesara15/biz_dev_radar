"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { ApiError } from "@/lib/onboarding/api";
import { CURRENCIES, formatMoney, type Currency } from "@/lib/money";
import type { Option } from "@/lib/profile-fields";
import { cn } from "@/lib/utils";

/* ----------------------------------------------------------------------------
 * Layout
 * ------------------------------------------------------------------------- */

export function Section({
  title,
  description,
  children,
  actions,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <section className="grid gap-4 rounded-xl bg-card p-5 ring-1 ring-foreground/10">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function FieldGrid({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("grid gap-4 sm:grid-cols-2", className)}>{children}</div>;
}

/* ----------------------------------------------------------------------------
 * Field wrapper
 * ------------------------------------------------------------------------- */

/** Visual-only required marker (a pseudo-element, so it never joins the label text). */
const REQUIRED_MARK = "after:ml-0.5 after:text-muted-foreground after:content-['*']";

export type FieldProps = {
  id: string;
  label: string;
  help?: string;
  error?: string;
  required?: boolean;
  className?: string;
  children: React.ReactNode;
};

/** Label + control + help/error. The control must carry `{...controlProps(id, error, help)}`. */
export function Field({ id, label, help, error, required, className, children }: FieldProps) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      <Label htmlFor={id} className={cn(required && REQUIRED_MARK)}>
        {label}
      </Label>
      {children}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : help ? (
        <p id={`${id}-help`} className="text-xs text-muted-foreground">
          {help}
        </p>
      ) : null}
    </div>
  );
}

export function controlProps(id: string, error?: string, help?: string) {
  return {
    id,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": error ? `${id}-error` : help ? `${id}-help` : undefined,
  } as const;
}

/* ----------------------------------------------------------------------------
 * Controls
 * ------------------------------------------------------------------------- */

export function SelectField({
  id,
  label,
  options,
  value,
  onChange,
  placeholder = "Select…",
  error,
  help,
  required,
  disabled,
}: {
  id: string;
  label: string;
  options: readonly Option[];
  value: string | null | undefined;
  onChange: (value: string | null) => void;
  placeholder?: string;
  error?: string;
  help?: string;
  required?: boolean;
  disabled?: boolean;
}) {
  return (
    <Field id={id} label={label} error={error} help={help} required={required}>
      <NativeSelect
        {...controlProps(id, error, help)}
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </NativeSelect>
    </Field>
  );
}

export function CheckboxField({
  id,
  label,
  checked,
  onChange,
  help,
}: {
  id: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  help?: string;
}) {
  return (
    <div className="flex items-start gap-2">
      <Checkbox
        id={id}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        aria-describedby={help ? `${id}-help` : undefined}
        className="mt-0.5"
      />
      <div className="grid gap-0.5">
        <Label htmlFor={id} className="font-normal">
          {label}
        </Label>
        {help ? (
          <p id={`${id}-help`} className="text-xs text-muted-foreground">
            {help}
          </p>
        ) : null}
      </div>
    </div>
  );
}

export function CheckboxGroup({
  idPrefix,
  legend,
  options,
  value,
  onChange,
  help,
  error,
  columns = 2,
}: {
  idPrefix: string;
  legend: string;
  options: readonly Option[];
  value: readonly string[];
  onChange: (value: string[]) => void;
  help?: string;
  error?: string;
  columns?: 1 | 2 | 3;
}) {
  const toggle = (v: string, on: boolean) =>
    onChange(on ? [...new Set([...value, v])] : value.filter((x) => x !== v));
  return (
    <fieldset className="grid gap-2" aria-describedby={error ? `${idPrefix}-error` : help ? `${idPrefix}-help` : undefined}>
      <legend className="text-sm font-medium">{legend}</legend>
      <div className={cn("grid gap-2", columns === 2 && "sm:grid-cols-2", columns === 3 && "sm:grid-cols-3")}>
        {options.map((o) => {
          const id = `${idPrefix}-${o.value}`;
          return (
            <div key={o.value} className="flex items-center gap-2">
              <Checkbox id={id} checked={value.includes(o.value)} onChange={(e) => toggle(o.value, e.target.checked)} />
              <Label htmlFor={id} className="font-normal">
                {o.label}
              </Label>
            </div>
          );
        })}
      </div>
      {error ? (
        <p id={`${idPrefix}-error`} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : help ? (
        <p id={`${idPrefix}-help`} className="text-xs text-muted-foreground">
          {help}
        </p>
      ) : null}
    </fieldset>
  );
}

export function splitTags(text: string): string[] {
  return [...new Set(text.split(/[,\n]/).map((t) => t.trim()).filter(Boolean))];
}

/** Comma-separated tags in a plain text input (accessible, no custom widget). */
export function TagsInput({
  id,
  label,
  value,
  onChange,
  placeholder,
  help,
  error,
  required,
  transform,
}: {
  id: string;
  label: string;
  value: readonly string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  help?: string;
  error?: string;
  required?: boolean;
  transform?: (tag: string) => string;
}) {
  const [text, setText] = React.useState(value.join(", "));
  const lastValue = React.useRef(value);
  React.useEffect(() => {
    if (lastValue.current !== value && value.join(", ") !== splitTags(text).join(", ")) {
      setText(value.join(", "));
    }
    lastValue.current = value;
  }, [value, text]);
  const hint = help ?? "Separate entries with commas.";
  return (
    <Field id={id} label={label} help={hint} error={error} required={required}>
      <Input
        {...controlProps(id, error, hint)}
        value={text}
        placeholder={placeholder}
        onChange={(e) => {
          setText(e.target.value);
          const tags = splitTags(e.target.value).map((t) => (transform ? transform(t) : t));
          onChange(tags);
        }}
        onBlur={() => setText(value.join(", "))}
      />
    </Field>
  );
}

/** Amount + currency, with the grouped preview (lakh/crore for INR). */
export function MoneyInput({
  id,
  label,
  amount,
  currency,
  onChange,
  currencies = CURRENCIES,
  help,
  error,
  required,
}: {
  id: string;
  label: string;
  amount: string;
  currency: Currency;
  onChange: (next: { amount: string; currency: Currency }) => void;
  currencies?: readonly Currency[];
  help?: string;
  error?: string;
  required?: boolean;
}) {
  const preview = amount.trim() ? formatMoney(amount, currency) : null;
  const hint = preview ?? help;
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id} className={cn(required && REQUIRED_MARK)}>
        {label}
      </Label>
      <div className="flex gap-2">
        <Input
          {...controlProps(id, error, hint)}
          inputMode="decimal"
          value={amount}
          placeholder="0.00"
          onChange={(e) => onChange({ amount: e.target.value, currency })}
        />
        <NativeSelect
          aria-label={`${label} currency`}
          className="w-24 shrink-0"
          value={currency}
          onChange={(e) => onChange({ amount, currency: e.target.value as Currency })}
        >
          {currencies.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </NativeSelect>
      </div>
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-help`} className="text-xs tabular-nums text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/* ----------------------------------------------------------------------------
 * Row list editor
 * ------------------------------------------------------------------------- */

export function RowList<T>({
  title,
  description,
  rows,
  renderRow,
  onAdd,
  onRemove,
  addLabel = "Add row",
  emptyText = "Nothing added yet.",
  rowLabel,
  hint,
}: {
  title: string;
  description?: string;
  rows: readonly T[];
  renderRow: (row: T, index: number) => React.ReactNode;
  onAdd: () => void;
  onRemove: (index: number) => void;
  addLabel?: string;
  emptyText?: string;
  /** Accessible name for each row's remove button, e.g. "address". */
  rowLabel: string;
  hint?: string;
}) {
  return (
    <div className="grid gap-3" role="group" aria-label={title}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium">{title}</h3>
          {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
        </div>
        <Button type="button" variant="outline" size="sm" onClick={onAdd}>
          {addLabel}
        </Button>
      </div>
      {rows.length === 0 ? (
        <p className="rounded-lg border border-dashed px-3 py-4 text-center text-sm text-muted-foreground">{emptyText}</p>
      ) : (
        <ol className="grid gap-3">
          {rows.map((row, index) => (
            <li key={index} className="relative grid gap-3 rounded-lg border p-3 pr-12">
              {renderRow(row, index)}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="absolute top-2 right-2"
                aria-label={`Remove ${rowLabel} ${index + 1}`}
                onClick={() => onRemove(index)}
              >
                Remove
              </Button>
            </li>
          ))}
        </ol>
      )}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/* ----------------------------------------------------------------------------
 * Errors and footer
 * ------------------------------------------------------------------------- */

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  const mismatch = error instanceof ApiError ? error.regionMismatch : null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
      {mismatch ? (
        <>
          <p className="font-medium">Some fields are not available for region {mismatch.region.toUpperCase()}.</p>
          <p>
            Region mismatch on: <span className="font-mono">{mismatch.fields.join(", ")}</span>
          </p>
        </>
      ) : (
        <p>{message}</p>
      )}
    </div>
  );
}

export function StepFooter({
  step,
  saving,
  onBack,
  nextLabel = "Save and continue",
}: {
  step: number;
  saving: boolean;
  onBack?: () => void;
  nextLabel?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-t pt-4">
      <Button type="button" variant="outline" onClick={onBack} disabled={!onBack || saving}>
        Back
      </Button>
      <div className="flex items-center gap-3">
        <span className="text-xs text-muted-foreground">Step {step} of 7</span>
        <Button type="submit" disabled={saving}>
          {saving ? "Saving…" : nextLabel}
        </Button>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------------------
 * Small helpers shared by steps
 * ------------------------------------------------------------------------- */

export const str = (v: unknown): string => (v === null || v === undefined ? "" : String(v));
export const orNull = (v: string): string | null => (v.trim() === "" ? null : v.trim());
export const intOrNull = (v: string): number | null => {
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) ? n : null;
};

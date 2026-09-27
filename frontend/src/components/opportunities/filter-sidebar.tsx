"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  NOTICE_TYPES,
  NOTICE_TYPE_LABELS,
  REGIONS,
  REGION_LABELS,
  STATUSES,
  STATUS_LABELS,
  activeFilterCount,
  parseNaicsList,
  type FilterRegion,
  type NoticeType,
  type OpportunityFilters,
  type OpportunityStatus,
} from "@/lib/opportunities/filters";

export type FilterPatch = Partial<Omit<OpportunityFilters, "page" | "page_size">>;

export type FilterSidebarProps = {
  filters: OpportunityFilters;
  /** Applies a change; the caller resets the page to 1. */
  onChange: (patch: FilterPatch) => void;
  onReset: () => void;
};

function toggle<T>(list: T[], value: T, on: boolean): T[] {
  if (on) return list.includes(value) ? list : [...list, value];
  return list.filter((v) => v !== value);
}

export function FilterSidebar({ filters, onChange, onReset }: FilterSidebarProps) {
  // Controls render from an optimistic local copy so a click flips at once;
  // the URL (the source of truth) catches up on the next render and re-syncs it.
  const [local, setLocal] = React.useState(filters);
  // Text fields are committed on submit / blur so typing does not spam the API.
  const [q, setQ] = React.useState(filters.q);
  const [naics, setNaics] = React.useState(filters.naics.join(", "));
  const [score, setScore] = React.useState(filters.min_score ?? 0);
  const scoreTimer = React.useRef<number | null>(null);

  React.useEffect(() => setLocal(filters), [filters]);
  React.useEffect(() => setQ(filters.q), [filters.q]);
  React.useEffect(() => setNaics(filters.naics.join(", ")), [filters.naics]);
  React.useEffect(() => setScore(filters.min_score ?? 0), [filters.min_score]);
  React.useEffect(() => () => {
    if (scoreTimer.current) window.clearTimeout(scoreTimer.current);
  }, []);

  const apply = (change: FilterPatch) => {
    setLocal((prev) => ({ ...prev, ...change }));
    onChange(change);
  };

  const commitText = () => {
    const patch: FilterPatch = {};
    const cleanQ = q.trim();
    if (cleanQ !== filters.q) patch.q = cleanQ;
    const codes = parseNaicsList(naics);
    if (codes.join(",") !== filters.naics.join(",")) patch.naics = codes;
    if (Object.keys(patch).length) apply(patch);
  };

  const onScore = (value: number) => {
    setScore(value);
    if (scoreTimer.current) window.clearTimeout(scoreTimer.current);
    scoreTimer.current = window.setTimeout(() => apply({ min_score: value === 0 ? null : value }), 250);
  };

  const active = activeFilterCount(filters);

  return (
    <aside aria-label="Filters" className="w-full lg:w-64 lg:shrink-0">
      <form
        className="grid gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          commitText();
        }}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Filters</h2>
          {active > 0 ? (
            <Button type="button" variant="ghost" size="xs" onClick={onReset}>
              Clear ({active})
            </Button>
          ) : null}
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="filter-q">Search</Label>
          <Input
            id="filter-q"
            type="search"
            name="q"
            placeholder="Title or description"
            value={q}
            onChange={(event) => setQ(event.target.value)}
            onBlur={commitText}
            autoComplete="off"
          />
          <p className="text-xs text-muted-foreground">Supports quotes, OR and -excluded words.</p>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="filter-region">Region</Label>
          <NativeSelect
            id="filter-region"
            name="region"
            value={local.region ?? ""}
            onChange={(event) => apply({ region: (event.target.value || null) as FilterRegion | null })}
          >
            <option value="">Both regions</option>
            {REGIONS.map((region) => (
              <option key={region} value={region}>
                {REGION_LABELS[region]}
              </option>
            ))}
          </NativeSelect>
        </div>

        <fieldset className="grid gap-1.5">
          <legend className="mb-1 text-sm font-medium">Notice type</legend>
          <div className="grid max-h-56 gap-1 overflow-y-auto pr-1">
            {NOTICE_TYPES.map((type) => {
              const id = `filter-type-${type}`;
              return (
                <div key={type} className="flex items-center gap-2">
                  <Checkbox
                    id={id}
                    name="type"
                    value={type}
                    checked={local.type.includes(type)}
                    onChange={(event) => apply({ type: toggle<NoticeType>(local.type, type, event.target.checked) })}
                  />
                  <Label htmlFor={id} className="font-normal">
                    {NOTICE_TYPE_LABELS[type]}
                  </Label>
                </div>
              );
            })}
          </div>
        </fieldset>

        <div className="grid gap-1.5">
          <Label htmlFor="filter-naics">NAICS codes</Label>
          <Input
            id="filter-naics"
            name="naics"
            placeholder="541511, 541512"
            value={naics}
            onChange={(event) => setNaics(event.target.value)}
            onBlur={commitText}
            inputMode="numeric"
            autoComplete="off"
          />
          <p className="text-xs text-muted-foreground">Comma or space separated; press Enter to apply.</p>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="filter-due-before">Due before</Label>
          <Input
            id="filter-due-before"
            type="date"
            name="due_before"
            value={local.due_before ?? ""}
            onChange={(event) => apply({ due_before: event.target.value || null })}
          />
        </div>

        <fieldset className="grid gap-1.5">
          <legend className="mb-1 text-sm font-medium">Status</legend>
          {STATUSES.map((status) => {
            const id = `filter-status-${status}`;
            return (
              <div key={status} className="flex items-center gap-2">
                <Checkbox
                  id={id}
                  name="status"
                  value={status}
                  checked={local.status.includes(status)}
                  onChange={(event) => apply({ status: toggle<OpportunityStatus>(local.status, status, event.target.checked) })}
                />
                <Label htmlFor={id} className="font-normal">
                  {STATUS_LABELS[status]}
                </Label>
              </div>
            );
          })}
        </fieldset>

        <div className="grid gap-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="filter-min-score">Minimum fit score</Label>
            <output htmlFor="filter-min-score" className="text-xs tabular-nums text-muted-foreground">
              {score === 0 ? "Any" : `≥ ${score}`}
            </output>
          </div>
          <input
            id="filter-min-score"
            name="min_score"
            type="range"
            min={0}
            max={100}
            step={5}
            value={score}
            onChange={(event) => onScore(Number(event.target.value))}
            className="w-full accent-primary focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            aria-valuetext={score === 0 ? "Any score" : `At least ${score}`}
          />
          <p className="text-xs text-muted-foreground">High-fit is 70 and above; applies once matching runs.</p>
        </div>

        <Button type="submit" variant="outline" size="sm" className="justify-self-start">
          Apply
        </Button>
      </form>
    </aside>
  );
}

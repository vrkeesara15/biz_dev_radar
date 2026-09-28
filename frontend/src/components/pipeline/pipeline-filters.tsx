"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import type { Member } from "@/lib/settings/api";
import {
  REGIONS,
  REGION_LABELS,
  UNASSIGNED,
  activePipelineFilterCount,
  type PipelineFilters,
} from "@/lib/pursuits/filters";
import { STAGES, STAGE_LABELS } from "@/lib/pursuits/stages";

export type FilterPatch = Partial<PipelineFilters>;

/**
 * The filter bar both views share (SPEC 9: owner, due date, value, region,
 * stage — plus the watch flag M6-01 added). Every control writes straight to
 * the URL through `onChange`, so the board and the table can never disagree
 * about what is being shown.
 */
export function PipelineFilterBar({
  filters,
  members,
  onChange,
  onReset,
}: {
  filters: PipelineFilters;
  members: Member[];
  onChange: (patch: FilterPatch) => void;
  onReset: () => void;
}) {
  const active = activePipelineFilterCount(filters);
  const [minValue, setMinValue] = React.useState(filters.min_value === null ? "" : String(filters.min_value));

  React.useEffect(() => {
    setMinValue(filters.min_value === null ? "" : String(filters.min_value));
  }, [filters.min_value]);

  const commitMinValue = () => {
    const trimmed = minValue.trim();
    if (!trimmed) {
      if (filters.min_value !== null) onChange({ min_value: null });
      return;
    }
    const parsed = Number(trimmed.replace(/[,\s$]/g, ""));
    const next = Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : null;
    if (next !== filters.min_value) onChange({ min_value: next });
  };

  return (
    <form
      aria-label="Pipeline filters"
      data-testid="pipeline-filters"
      onSubmit={(event) => event.preventDefault()}
      className="grid gap-3 rounded-xl border p-3 sm:grid-cols-2 lg:grid-cols-6"
    >
      <div className="grid gap-1.5">
        <Label htmlFor="pipeline-owner">Owner</Label>
        <NativeSelect
          id="pipeline-owner"
          value={filters.owner ?? ""}
          onChange={(event) => onChange({ owner: event.target.value || null })}
        >
          <option value="">Anyone</option>
          <option value={UNASSIGNED}>Unassigned</option>
          {members.map((member) => (
            <option key={member.user_id} value={member.user_id}>
              {member.name ?? member.email}
            </option>
          ))}
        </NativeSelect>
      </div>

      <div className="grid gap-1.5">
        <Label htmlFor="pipeline-due-before">Due before</Label>
        <Input
          id="pipeline-due-before"
          type="date"
          value={filters.due_before ?? ""}
          onChange={(event) => onChange({ due_before: event.target.value || null })}
        />
      </div>

      <div className="grid gap-1.5">
        <Label htmlFor="pipeline-min-value">Min value (USD)</Label>
        <Input
          id="pipeline-min-value"
          inputMode="numeric"
          placeholder="Any"
          value={minValue}
          onChange={(event) => setMinValue(event.target.value)}
          onBlur={commitMinValue}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commitMinValue();
            }
          }}
        />
      </div>

      <div className="grid gap-1.5">
        <Label htmlFor="pipeline-region">Region</Label>
        <NativeSelect
          id="pipeline-region"
          value={filters.region ?? ""}
          onChange={(event) =>
            onChange({ region: (event.target.value || null) as PipelineFilters["region"] })
          }
        >
          <option value="">Both</option>
          {REGIONS.map((region) => (
            <option key={region} value={region}>
              {REGION_LABELS[region]}
            </option>
          ))}
        </NativeSelect>
      </div>

      <div className="grid gap-1.5">
        <Label htmlFor="pipeline-stage">Stage</Label>
        <NativeSelect
          id="pipeline-stage"
          value={filters.stage[0] ?? ""}
          onChange={(event) =>
            onChange({ stage: event.target.value ? [event.target.value as PipelineFilters["stage"][number]] : [] })
          }
        >
          <option value="">Every stage</option>
          {STAGES.map((stage) => (
            <option key={stage} value={stage}>
              {STAGE_LABELS[stage]}
            </option>
          ))}
        </NativeSelect>
      </div>

      <div className="grid gap-1.5">
        <Label htmlFor="pipeline-watch">Watch</Label>
        <NativeSelect
          id="pipeline-watch"
          value={filters.watch === null ? "" : filters.watch ? "true" : "false"}
          onChange={(event) =>
            onChange({ watch: event.target.value === "" ? null : event.target.value === "true" })
          }
        >
          <option value="">Any</option>
          <option value="true">Watched only</option>
          <option value="false">Not watched</option>
        </NativeSelect>
      </div>

      <div className="flex items-end lg:col-span-6">
        <Button type="button" variant="ghost" size="sm" onClick={onReset} disabled={active === 0}>
          Clear {active ? `(${active})` : ""}
        </Button>
      </div>
    </form>
  );
}

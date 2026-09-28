"use client";

import * as React from "react";
import { cn } from "cn";

/**
 * A manual-activation WAI-ARIA tab list with a roving tabindex: exactly one
 * tab is in the tab order, Arrow keys move focus and select, Home/End jump to
 * the ends, and each panel is labelled by its tab. No dependency — the shadcn
 * tabs primitive is not installed and this is the whole behaviour.
 */
export type TabItem = { id: string; label: string; badge?: React.ReactNode };

export function Tabs({
  items,
  value,
  onValueChange,
  idPrefix = "tab",
  className,
  label,
}: {
  items: readonly TabItem[];
  value: string;
  onValueChange: (id: string) => void;
  idPrefix?: string;
  className?: string;
  label: string;
}) {
  const listRef = React.useRef<HTMLDivElement>(null);

  const move = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const index = items.findIndex((item) => item.id === value);
    const last = items.length - 1;
    let next = index;
    if (event.key === "ArrowRight") next = index >= last ? 0 : index + 1;
    if (event.key === "ArrowLeft") next = index <= 0 ? last : index - 1;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = last;
    const target = items[next];
    if (!target) return;
    onValueChange(target.id);
    const button = listRef.current?.querySelector<HTMLButtonElement>(
      `#${CSS.escape(`${idPrefix}-${target.id}`)}`,
    );
    button?.focus();
  };

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={label}
      onKeyDown={move}
      data-testid="workspace-tabs"
      className={cn("flex flex-wrap items-center gap-1 border-b", className)}
    >
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`${idPrefix}-${item.id}`}
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel-${item.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onValueChange(item.id)}
            className={cn(
              "-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
              selected
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {item.label}
            {item.badge}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  id,
  active,
  idPrefix = "tab",
  children,
  className,
}: {
  id: string;
  active: boolean;
  idPrefix?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      role="tabpanel"
      id={`${idPrefix}-panel-${id}`}
      aria-labelledby={`${idPrefix}-${id}`}
      hidden={!active}
      tabIndex={0}
      data-testid={`panel-${id}`}
      className={cn("pt-4 outline-none focus-visible:ring-3 focus-visible:ring-ring/50", className)}
    >
      {active ? children : null}
    </div>
  );
}

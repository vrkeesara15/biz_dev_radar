"use client";

import * as React from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MeasuringStrategy,
  PointerSensor,
  closestCorners,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ChevronRightIcon } from "lucide-react";

import { PursuitCard, type OwnerLookup } from "@/components/pipeline/pursuit-card";
import type { PursuitListItem } from "@/lib/pursuits/api";
import { CLOSED_SECTION_LABEL, boardColumns, stageLabel, type BoardColumn } from "@/lib/pursuits/stages";
import { cn } from "@/lib/utils";

const DROPPABLE_PREFIX = "stage:";

/** The stage a drop landed on: a column id, or the stage of the card under the cursor. */
export function stageFromDropTarget(
  overId: string | number | null | undefined,
  overStage: unknown,
): string | null {
  if (typeof overStage === "string" && overStage) return overStage;
  const id = typeof overId === "string" ? overId : null;
  if (id && id.startsWith(DROPPABLE_PREFIX)) return id.slice(DROPPABLE_PREFIX.length);
  return null;
}

type ColumnProps = {
  column: BoardColumn;
  items: PursuitListItem[];
  now: Date;
  userTz: string | null;
  lookup?: OwnerLookup;
  draggable: boolean;
};

function SortableCard({
  item,
  now,
  userTz,
  lookup,
  draggable,
}: {
  item: PursuitListItem;
  now: Date;
  userTz: string | null;
  lookup?: OwnerLookup;
  draggable: boolean;
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({
    id: item.id,
    data: { stage: item.stage, title: item.title },
    disabled: !draggable,
  });
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className="list-none"
    >
      <PursuitCard
        item={item}
        now={now}
        userTz={userTz}
        lookup={lookup}
        dragging={isDragging}
        dragHandleProps={
          draggable
            ? { ...attributes, ...listeners, ref: setActivatorNodeRef as unknown as React.Ref<HTMLElement> }
            : undefined
        }
      />
    </li>
  );
}

function Column({ column, items, now, userTz, lookup, draggable }: ColumnProps) {
  const { setNodeRef, isOver } = useDroppable({
    id: `${DROPPABLE_PREFIX}${column.stage}`,
    data: { stage: column.stage },
  });
  const ids = React.useMemo(() => items.map((item) => item.id), [items]);
  const label = `${column.label}, ${column.count} ${column.count === 1 ? "pursuit" : "pursuits"}`;

  return (
    <section
      data-testid="board-column"
      data-stage={column.stage}
      aria-label={label}
      className="flex w-72 shrink-0 flex-col rounded-xl border bg-muted/30"
    >
      <h3 className="flex items-center justify-between gap-2 border-b px-3 py-2 text-xs font-semibold tracking-wide uppercase">
        <span>{column.label}</span>
        <span
          data-testid="column-count"
          className="rounded-full bg-background px-2 py-0.5 text-[11px] tabular-nums text-muted-foreground"
        >
          {column.count}
        </span>
      </h3>
      <ul
        ref={setNodeRef}
        data-testid="column-list"
        aria-label={`${column.label} pursuits`}
        className={cn(
          "grid min-h-24 content-start gap-2 p-2 transition-colors",
          isOver && "bg-primary/5 ring-2 ring-inset ring-primary/40",
        )}
      >
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          {items.map((item) => (
            <SortableCard
              key={item.id}
              item={item}
              now={now}
              userTz={userTz}
              lookup={lookup}
              draggable={draggable}
            />
          ))}
        </SortableContext>
        {items.length === 0 ? (
          <li className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
            Nothing here
          </li>
        ) : null}
      </ul>
    </section>
  );
}

export type BoardProps = {
  items: PursuitListItem[];
  byStage: Record<string, number>;
  now: Date;
  userTz: string | null;
  lookup?: OwnerLookup;
  /** Moves a card; resolves when the server has accepted (or rejected) it. */
  onMove: (pursuitId: string, toStage: string) => void | Promise<void>;
  /** False while a page is loading, so a card cannot be dragged twice. */
  draggable?: boolean;
};

/**
 * The Kanban board (SPEC 9): one column per stage in SPEC 9 order, the four
 * terminal stages folded into a collapsible "Closed" section. Columns are
 * lists with aria-labels, cards carry a keyboard-operable drag handle
 * (space to lift, arrows to move, space to drop) and every lift/move/drop is
 * announced through dnd-kit's live region.
 */
export function Board({ items, byStage, now, userTz, lookup, onMove, draggable = true }: BoardProps) {
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [closedOpen, setClosedOpen] = React.useState(false);
  const columns = React.useMemo(() => boardColumns(byStage), [byStage]);

  const grouped = React.useMemo(() => {
    const map = new Map<string, PursuitListItem[]>();
    for (const item of items) {
      const bucket = map.get(item.stage);
      if (bucket) bucket.push(item);
      else map.set(item.stage, [item]);
    }
    return map;
  }, [items]);

  const byId = React.useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);
  const active = activeId ? byId.get(activeId) ?? null : null;

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const announcements = React.useMemo<Announcements>(
    () => ({
      onDragStart: ({ active: dragged }) => {
        const item = byId.get(String(dragged.id));
        return item ? `Picked up ${item.title} from ${stageLabel(item.stage)}.` : "Picked up a pursuit.";
      },
      onDragOver: ({ active: dragged, over }) => {
        const item = byId.get(String(dragged.id));
        const stage = stageFromDropTarget(over?.id, over?.data.current?.stage);
        if (!item || !stage) return undefined;
        return `${item.title} is over ${stageLabel(stage)}.`;
      },
      onDragEnd: ({ active: dragged, over }) => {
        const item = byId.get(String(dragged.id));
        const stage = stageFromDropTarget(over?.id, over?.data.current?.stage);
        if (!item) return "Dropped.";
        if (!stage) return `${item.title} was returned to ${stageLabel(item.stage)}.`;
        return `${item.title} was dropped on ${stageLabel(stage)}.`;
      },
      onDragCancel: ({ active: dragged }) => {
        const item = byId.get(String(dragged.id));
        return item ? `Move of ${item.title} cancelled.` : "Move cancelled.";
      },
    }),
    [byId],
  );

  const handleStart = (event: DragStartEvent) => setActiveId(String(event.active.id));

  const handleEnd = (event: DragEndEvent) => {
    setActiveId(null);
    const { active: dragged, over } = event;
    if (!over) return;
    const item = byId.get(String(dragged.id));
    const target = stageFromDropTarget(over.id, over.data.current?.stage);
    if (!item || !target || target === item.stage) return;
    void onMove(item.id, target);
  };

  const renderColumn = (column: BoardColumn) => (
    <Column
      key={column.stage}
      column={column}
      items={grouped.get(column.stage) ?? []}
      now={now}
      userTz={userTz}
      lookup={lookup}
      draggable={draggable}
    />
  );

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCorners}
      accessibility={{ announcements }}
      measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
      onDragStart={handleStart}
      onDragEnd={handleEnd}
      onDragCancel={() => setActiveId(null)}
    >
      <div className="grid gap-4">
        <div
          data-testid="board"
          aria-label="Pipeline board"
          className="flex gap-3 overflow-x-auto pb-2"
        >
          {columns.open.map(renderColumn)}
        </div>

        <section data-testid="closed-section" className="rounded-xl border">
          <h2>
            <button
              type="button"
              aria-expanded={closedOpen}
              aria-controls="closed-columns"
              onClick={() => setClosedOpen((open) => !open)}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <ChevronRightIcon
                aria-hidden="true"
                className={cn("size-4 transition-transform", closedOpen && "rotate-90")}
              />
              {CLOSED_SECTION_LABEL}
              <span className="rounded-full bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">
                {columns.closedCount}
              </span>
              <span className="text-xs font-normal text-muted-foreground">
                {columns.closed.map((column) => `${column.label} ${column.count}`).join(" · ")}
              </span>
            </button>
          </h2>
          <div id="closed-columns" hidden={!closedOpen} className="flex gap-3 overflow-x-auto border-t p-3">
            {columns.closed.map(renderColumn)}
          </div>
        </section>
      </div>

      <DragOverlay>
        {active ? (
          <PursuitCard item={active} now={now} userTz={userTz} lookup={lookup} overlay className="shadow-lg" />
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}

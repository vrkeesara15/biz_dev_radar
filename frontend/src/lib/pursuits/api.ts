/**
 * Typed wrappers over the pursuit routes: the board/table listing and stage
 * moves (M6-01), key dates (M6-02), tasks and comments (M6-07) and the
 * per-user calendar feed (M6-04). Everything here is in the generated
 * OpenAPI schema, so the calls go through `browserApi` and the same-origin
 * proxy; only the error bodies need reading by hand, because FastAPI wraps a
 * structured 409 in `detail`.
 */
import { browserApi, type Schemas } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";

import {
  API_MAX_PAGE_SIZE,
  BOARD_CAP,
  needsClientOwnerFilter,
  toPursuitQuery,
  type PipelineFilters,
} from "./filters";

export type PursuitListItem = Schemas["PursuitListItem"];
export type PursuitPage = Schemas["PursuitPage"];
export type PursuitOut = Schemas["PursuitOut"];
export type KeyDate = Schemas["KeyDateOut"];
export type KeyDateList = Schemas["KeyDateListOut"];
export type PursuitTask = Schemas["TaskOut"];
export type TaskList = Schemas["TaskListOut"];
export type PursuitComment = Schemas["CommentOut"];
export type CommentList = Schemas["CommentListOut"];
export type MeCalendar = Schemas["MeCalendarOut"];
export type CalendarConnection = Schemas["CalendarConnectionOut"];
export type TzDate = Schemas["TzDateOut"];

type Result<T> = { data?: T; error?: unknown; response: Response };

async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok) throw new ApiError(response.status, error);
  return data as T;
}

/** FastAPI wraps an HTTPException payload in `detail`; read through both. */
function payload(body: unknown): Record<string, unknown> | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    return detail as Record<string, unknown>;
  }
  return body as Record<string, unknown>;
}

/** 409 body of a refused drag: {error: stage_transition, from, to, reason}. */
export type StageConflict = { error: "stage_transition"; from: string; to: string; reason: string };

export function asStageConflict(body: unknown): StageConflict | null {
  const record = payload(body);
  if (!record || record.error !== "stage_transition") return null;
  return {
    error: "stage_transition",
    from: String(record.from ?? ""),
    to: String(record.to ?? ""),
    reason: String(record.reason ?? "the server refused this move"),
  };
}

/** 409 body when a tenant has several profiles (OQ-111). */
export type ProfileRequired = { error: "profile_required"; message: string; profile_ids: string[] };

export function asProfileRequired(body: unknown): ProfileRequired | null {
  const record = payload(body);
  if (!record || record.error !== "profile_required") return null;
  return {
    error: "profile_required",
    message: String(record.message ?? "this tenant has several profiles"),
    profile_ids: Array.isArray(record.profile_ids) ? record.profile_ids.map(String) : [],
  };
}

/** 409 body when an expired registration blocks bidding (OQ-130). */
export type ProfileBlocked = { error: "profile_blocked_for_bids"; profile_id: string; reason: string };

export function asProfileBlocked(body: unknown): ProfileBlocked | null {
  const record = payload(body);
  if (!record || record.error !== "profile_blocked_for_bids") return null;
  return {
    error: "profile_blocked_for_bids",
    profile_id: String(record.profile_id ?? ""),
    reason: String(record.reason ?? "this profile is blocked for bids"),
  };
}

/** The sentence to show the user for any refused pursuit action. */
export function conflictReason(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  return (
    asStageConflict(error.body)?.reason ??
    asProfileBlocked(error.body)?.reason ??
    asProfileRequired(error.body)?.message ??
    null
  );
}

// --- listing ------------------------------------------------------------------

export const listPursuits = (query: Record<string, string | number | boolean>, signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/pursuits", { params: { query: query as never }, signal }));

export type BoardData = {
  items: PursuitListItem[];
  byStage: Record<string, number>;
  total: number;
  /** True when the filter matches more than BOARD_CAP cards and we stopped. */
  capped: boolean;
};

/**
 * Every page of the current filter, for the board. The API caps `page_size`
 * at 200, so a 500-card board is three requests; past BOARD_CAP we stop and
 * say so rather than drawing a board nobody can read. `by_stage` always
 * covers the WHOLE filtered set (M6-01), so the column counts stay honest
 * even when the cards are capped.
 */
export async function loadBoard(
  filters: PipelineFilters,
  signal?: AbortSignal,
  cap = BOARD_CAP,
): Promise<BoardData> {
  const items: PursuitListItem[] = [];
  let byStage: Record<string, number> = {};
  let total = 0;
  let page = 1;
  let pages = 1;
  do {
    const result = await listPursuits(
      toPursuitQuery(filters, { page, pageSize: API_MAX_PAGE_SIZE }),
      signal,
    );
    byStage = result.by_stage ?? {};
    total = result.total;
    pages = Math.max(result.pages, 1);
    items.push(...result.items);
    page += 1;
  } while (page <= pages && items.length < cap);

  const capped = items.length > cap || (page <= pages && items.length >= cap);
  const trimmed = items.slice(0, cap);
  const filtered = needsClientOwnerFilter(filters)
    ? trimmed.filter((item) => !item.owner_user_id)
    : trimmed;
  return { items: filtered, byStage, total, capped };
}

export const getPursuit = (pursuitId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/pursuits/{pursuit_id}", {
      params: { path: { pursuit_id: pursuitId } },
      signal,
    }),
  );

export type PursuitPatch = {
  stage?: string;
  owner_user_id?: string | null;
  internal_due_at?: string | null;
  watch?: boolean;
};

/** PATCH /pursuits/{id}: 409 {error: stage_transition, from, to, reason} on a refused move. */
export const patchPursuit = (pursuitId: string, body: PursuitPatch) =>
  unwrap(
    browserApi.PATCH("/api/v1/pursuits/{pursuit_id}", {
      params: { path: { pursuit_id: pursuitId } },
      body: body as never,
    }),
  );

// --- key dates (M6-02) ---------------------------------------------------------

export const listKeyDates = (pursuitId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/pursuits/{pursuit_id}/dates", {
      params: { path: { pursuit_id: pursuitId } },
      signal,
    }),
  );

export type KeyDateBody = { kind?: string; at: string; label?: string | null; note?: string | null };

export const createKeyDate = (pursuitId: string, body: KeyDateBody) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/dates", {
      params: { path: { pursuit_id: pursuitId } },
      body: body as never,
    }),
  );

export const updateKeyDate = (
  pursuitId: string,
  dateId: string,
  body: { at?: string; label?: string | null; note?: string | null },
) =>
  unwrap(
    browserApi.PUT("/api/v1/pursuits/{pursuit_id}/dates/{date_id}", {
      params: { path: { pursuit_id: pursuitId, date_id: dateId } },
      body: body as never,
    }),
  );

export const deleteKeyDate = (pursuitId: string, dateId: string) =>
  unwrap(
    browserApi.DELETE("/api/v1/pursuits/{pursuit_id}/dates/{date_id}", {
      params: { path: { pursuit_id: pursuitId, date_id: dateId } },
    }),
  );

export const acknowledgeKeyDate = (pursuitId: string, dateId: string) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/dates/{date_id}/acknowledge", {
      params: { path: { pursuit_id: pursuitId, date_id: dateId } },
    }),
  );

// --- tasks and comments (M6-07) ------------------------------------------------

export const listTasks = (pursuitId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/pursuits/{pursuit_id}/tasks", {
      params: { path: { pursuit_id: pursuitId } },
      signal,
    }),
  );

export type TaskBody = {
  title: string;
  detail?: string | null;
  assignee_user_id?: string | null;
  due_at?: string | null;
};

export const createTask = (pursuitId: string, body: TaskBody) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/tasks", {
      params: { path: { pursuit_id: pursuitId } },
      body: body as never,
    }),
  );

export const patchTask = (
  pursuitId: string,
  taskId: string,
  body: Partial<TaskBody> & { status?: string },
) =>
  unwrap(
    browserApi.PATCH("/api/v1/pursuits/{pursuit_id}/tasks/{task_id}", {
      params: { path: { pursuit_id: pursuitId, task_id: taskId } },
      body: body as never,
    }),
  );

/** Anchors a thread to one artefact (M5-16 / OQ-145: draft_section, artifact, …). */
export type CommentTarget = { targetType?: string; targetId?: string | null };

export const listComments = (
  pursuitId: string,
  target: CommentTarget = {},
  signal?: AbortSignal,
) =>
  unwrap(
    browserApi.GET("/api/v1/pursuits/{pursuit_id}/comments", {
      params: {
        path: { pursuit_id: pursuitId },
        query: {
          ...(target.targetType ? { target_type: target.targetType } : {}),
          ...(target.targetId ? { target_id: target.targetId } : {}),
        } as never,
      },
      signal,
    }),
  );

export const createComment = (
  pursuitId: string,
  body: { body: string; target_type?: string; target_id?: string | null },
) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/comments", {
      params: { path: { pursuit_id: pursuitId } },
      body: body as never,
    }),
  );

export const patchComment = (
  pursuitId: string,
  commentId: string,
  body: { body?: string; resolved?: boolean },
) =>
  unwrap(
    browserApi.PATCH("/api/v1/pursuits/{pursuit_id}/comments/{comment_id}", {
      params: { path: { pursuit_id: pursuitId, comment_id: commentId } },
      body: body as never,
    }),
  );

// --- calendar (M6-04) ----------------------------------------------------------

export const getMyCalendar = (signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/me/calendar", { signal }));

/** Issues or rotates the feed nonce; every link handed out before stops working. */
export const rotateCalendarToken = () => unwrap(browserApi.POST("/api/v1/me/calendar-token", {}));

export const disconnectCalendar = (connectionId: string) =>
  unwrap(
    browserApi.DELETE("/api/v1/me/calendar-connections/{connection_id}", {
      params: { path: { connection_id: connectionId } },
    }),
  );

// --- calendar aggregation ------------------------------------------------------

export type CalendarEvent = {
  id: string;
  pursuitId: string;
  opportunityId: string;
  kind: string;
  label: string;
  at: TzDate;
  note: string | null;
  source: string;
  acknowledgedAt: string | null;
  pursuitTitle: string;
  buyer: string | null;
  stage: string;
};

export type CalendarLoad = {
  events: CalendarEvent[];
  pursuits: number;
  /** True when more pursuits matched than we were willing to fan out over. */
  capped: boolean;
};

/** How many pursuits the calendar will fan out over before it stops. */
export const CALENDAR_PURSUIT_CAP = 200;
const CONCURRENCY = 6;

/**
 * Every key date across the tenant's pursuits.
 *
 * There is no tenant-wide key-dates route (the only aggregate is the personal
 * iCal feed, which is per user and not JSON), so the calendar does what the
 * board does — one paged GET /pursuits — and then one GET /pursuits/{id}/dates
 * per card, six at a time. A pursuit whose dates cannot be read is skipped
 * rather than failing the whole month.
 */
export async function loadCalendarEvents(
  filters: PipelineFilters,
  signal?: AbortSignal,
  cap = CALENDAR_PURSUIT_CAP,
): Promise<CalendarLoad> {
  const board = await loadBoard(filters, signal, cap);
  const pursuits = board.items;
  const events: CalendarEvent[] = [];
  let cursor = 0;

  const worker = async () => {
    for (;;) {
      const index = cursor;
      cursor += 1;
      if (index >= pursuits.length) return;
      const pursuit = pursuits[index];
      let list: KeyDateList;
      try {
        list = await listKeyDates(pursuit.id, signal);
      } catch {
        continue; // one unreadable pursuit must not blank the calendar
      }
      for (const date of list.items) {
        events.push({
          id: date.id,
          pursuitId: pursuit.id,
          opportunityId: pursuit.opportunity_id,
          kind: date.kind,
          label: date.label,
          at: date.at,
          note: date.note,
          source: date.source,
          acknowledgedAt: date.acknowledged_at ?? null,
          pursuitTitle: pursuit.title,
          buyer: pursuit.buyer_org,
          stage: pursuit.stage,
        });
      }
    }
  };

  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, pursuits.length) }, worker));
  events.sort((a, b) => a.at.utc.localeCompare(b.at.utc) || a.pursuitTitle.localeCompare(b.pursuitTitle));
  return { events, pursuits: pursuits.length, capped: board.capped || board.total > cap };
}

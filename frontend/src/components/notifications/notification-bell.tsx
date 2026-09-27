"use client";

import { BellIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api/browser";
import {
  ACTION_LABELS,
  POLL_INTERVAL_MS,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  relativeTime,
  toBellItem,
  type BellItem,
} from "@/lib/notifications/api";
import { ApiError } from "@/lib/opportunities/api";
import { cn } from "@/lib/utils";

const PANEL_LIMIT = 10;

/**
 * Top-bar bell (SPEC 7, 10.4 screen 2): unread count polled every 60 seconds,
 * a dropdown of the latest notifications, mark-read per item and for all, deep
 * links into the opportunity or pursuit, and the signed one-click action links
 * the API minted with the notification.
 */
export function NotificationBell({ className }: { className?: string }) {
  const [items, setItems] = React.useState<BellItem[]>([]);
  const [unread, setUnread] = React.useState(0);
  const [open, setOpen] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [loaded, setLoaded] = React.useState(false);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [passFor, setPassFor] = React.useState<string | null>(null);
  const [passReason, setPassReason] = React.useState("");
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [now, setNow] = React.useState(() => new Date());

  const load = React.useCallback(async (signal?: AbortSignal) => {
    try {
      const page = await listNotifications({ limit: PANEL_LIMIT }, signal);
      setItems(page.items.map(toBellItem));
      setUnread(page.unread);
      setError(null);
    } catch (caught) {
      if (signal?.aborted) return;
      setError(
        caught instanceof ApiError
          ? errorMessage(caught.body, `Notifications could not be read (${caught.status}).`)
          : "Notifications could not be read.",
      );
    } finally {
      setLoaded(true);
    }
  }, []);

  React.useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = window.setInterval(() => {
      setNow(new Date());
      void load();
    }, POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load]);

  // Close on outside click / Escape, as a menu should.
  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const openPanel = () => {
    setOpen((value) => {
      if (!value) {
        setNow(new Date());
        void load();
      }
      return !value;
    });
  };

  const markOne = async (item: BellItem) => {
    if (item.readAt) return;
    setBusy(item.id);
    try {
      const result = await markNotificationRead(item.id);
      setItems((list) =>
        list.map((row) => (row.id === item.id ? { ...row, readAt: result.read_at } : row)),
      );
      setUnread((count) => Math.max(0, count - 1));
    } catch (caught) {
      toast.error(
        caught instanceof ApiError
          ? errorMessage(caught.body, "Could not mark it read")
          : "Could not mark it read",
      );
    } finally {
      setBusy(null);
    }
  };

  const markAll = async () => {
    setBusy("all");
    try {
      const result = await markAllNotificationsRead();
      const readAt = new Date().toISOString();
      setItems((list) => list.map((row) => (row.readAt ? row : { ...row, readAt })));
      setUnread(0);
      toast.success(result.marked === 1 ? "1 notification marked read" : `${result.marked} notifications marked read`);
    } catch (caught) {
      toast.error(
        caught instanceof ApiError ? errorMessage(caught.body, "Could not clear the badge") : "Could not clear the badge",
      );
    } finally {
      setBusy(null);
    }
  };

  const takeAction = async (item: BellItem, action: string, url: string, reason?: string) => {
    setBusy(`${item.id}:${action}`);
    try {
      const target = reason ? `${url}${url.includes("?") ? "&" : "?"}reason=${encodeURIComponent(reason)}` : url;
      const response = await fetch(target, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new ApiError(response.status, await response.json().catch(() => null));
      setItems((list) =>
        list.map((row) =>
          row.id === item.id ? { ...row, actionsTaken: [...row.actionsTaken, action] } : row,
        ),
      );
      toast.success(`${ACTION_LABELS[action] ?? action} recorded`);
      setPassFor(null);
      setPassReason("");
      void markOne(item);
    } catch (caught) {
      toast.error(
        caught instanceof ApiError
          ? errorMessage(caught.body, `Could not record ${action} (${caught.status})`)
          : `Could not record ${action}`,
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={cn("relative", className)} ref={containerRef}>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls="notification-panel"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        data-testid="notification-bell"
        onClick={openPanel}
        className="relative"
      >
        <BellIcon className="size-4" aria-hidden="true" />
        {unread > 0 ? (
          <span
            data-testid="notification-unread-count"
            className="ml-1 inline-flex min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-xs font-medium tabular-nums text-primary-foreground"
          >
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </Button>

      {open ? (
        <div
          id="notification-panel"
          data-testid="notification-panel"
          role="region"
          aria-label="Notifications"
          className="absolute right-0 z-50 mt-2 w-[22rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-md"
        >
          <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
            <p className="text-sm font-medium">Notifications</p>
            <Button
              type="button"
              variant="ghost"
              size="xs"
              onClick={markAll}
              disabled={busy === "all" || unread === 0}
              data-testid="mark-all-read"
            >
              Mark all read
            </Button>
          </div>

          <ul className="max-h-96 divide-y overflow-y-auto">
            {items.map((item) => (
              <li
                key={item.id}
                data-testid="notification-item"
                data-read={item.readAt ? "true" : "false"}
                className={cn("grid gap-1 px-3 py-2.5", !item.readAt && "bg-muted/40")}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    {item.href ? (
                      <Link
                        href={item.href}
                        className="text-sm font-medium underline-offset-4 hover:underline"
                        onClick={() => {
                          setOpen(false);
                          void markOne(item);
                        }}
                      >
                        {item.title}
                      </Link>
                    ) : (
                      <span className="text-sm font-medium">{item.title}</span>
                    )}
                    {item.detail ? (
                      <p className="truncate text-xs text-muted-foreground">{item.detail}</p>
                    ) : null}
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {relativeTime(item.createdAt, now)}
                  </span>
                </div>

                {item.actions.length ? (
                  <div className="flex flex-wrap items-center gap-1">
                    {item.actions.map(({ action, url }) => {
                      const taken = item.actionsTaken.includes(action);
                      return (
                        <Button
                          key={action}
                          type="button"
                          variant="outline"
                          size="xs"
                          disabled={taken || busy === `${item.id}:${action}`}
                          aria-label={`${ACTION_LABELS[action] ?? action} ${item.title}`}
                          onClick={() => {
                            if (action === "pass") {
                              setPassFor(passFor === item.id ? null : item.id);
                              setPassReason("");
                              return;
                            }
                            void takeAction(item, action, url);
                          }}
                        >
                          {taken ? `${ACTION_LABELS[action] ?? action} ✓` : ACTION_LABELS[action] ?? action}
                        </Button>
                      );
                    })}
                  </div>
                ) : null}

                {passFor === item.id ? (
                  <form
                    className="grid gap-1.5 rounded-md bg-muted/60 p-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      const url = item.actions.find((entry) => entry.action === "pass")?.url;
                      if (url && passReason.trim()) void takeAction(item, "pass", url, passReason.trim());
                    }}
                  >
                    <Label htmlFor={`pass-reason-${item.id}`} className="text-xs">
                      Why pass?
                    </Label>
                    <Input
                      id={`pass-reason-${item.id}`}
                      value={passReason}
                      onChange={(event) => setPassReason(event.target.value)}
                      placeholder="Out of scope, no capacity…"
                      className="h-8"
                      required
                    />
                    <div className="flex justify-end gap-1">
                      <Button type="button" variant="ghost" size="xs" onClick={() => setPassFor(null)}>
                        Cancel
                      </Button>
                      <Button type="submit" size="xs" disabled={!passReason.trim()}>
                        Record pass
                      </Button>
                    </div>
                  </form>
                ) : null}

                {!item.readAt ? (
                  <div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      onClick={() => void markOne(item)}
                      disabled={busy === item.id}
                      aria-label={`Mark “${item.title}” read`}
                    >
                      Mark read
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>

          {error ? (
            <p role="alert" className="px-3 py-3 text-sm text-destructive">
              {error}
            </p>
          ) : null}
          {!error && loaded && !items.length ? (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">
              Nothing yet. New High-fit matches and deadline reminders land here.
            </p>
          ) : null}

          <div className="border-t px-3 py-2 text-right">
            <Link
              href="/app/settings/notifications"
              className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
              onClick={() => setOpen(false)}
            >
              Notification settings →
            </Link>
          </div>
        </div>
      ) : null}
    </div>
  );
}

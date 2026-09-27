"use client";

import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorMessage } from "@/lib/api/browser";
import { ApiError, NotAvailableError } from "@/lib/opportunities/api";
import {
  MEMBERS_UNAVAILABLE_MESSAGE,
  inviteMember,
  listMembers,
  removeMember,
  updateMemberRole,
  type Member,
} from "@/lib/settings/api";
import {
  PLATFORM_ADMIN_META,
  ROLE_META,
  canManageMembers,
  isLastOwner,
  roleDescription,
  roleLabel,
} from "@/lib/settings/roles";
import type { Role } from "@/types/next-auth";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

/**
 * Settings > Users and roles (SPEC 3, 10.4 screen 8). Only the tenant owner
 * may invite or change a role; everyone else sees the table read-only, and
 * the last owner cannot demote themselves out of the tenant.
 */
export function UsersScreen({ role, currentUserId }: { role?: Role; currentUserId?: string }) {
  const owner = canManageMembers(role);
  const [members, setMembers] = React.useState<Member[]>([]);
  const [unavailable, setUnavailable] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [loaded, setLoaded] = React.useState(false);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [email, setEmail] = React.useState("");
  const [inviteRole, setInviteRole] = React.useState<string>("bid_manager");

  React.useEffect(() => {
    let cancelled = false;
    listMembers()
      .then((rows) => {
        if (!cancelled) setMembers(Array.isArray(rows) ? rows : []);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        if (caught instanceof NotAvailableError) setUnavailable(true);
        else setError(describe(caught, "The member list could not be read"));
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const changeRole = async (member: Member, next: string) => {
    setBusy(member.id);
    try {
      const updated = await updateMemberRole(member.id, next);
      setMembers((list) => list.map((row) => (row.id === member.id ? { ...row, ...updated } : row)));
      toast.success(`${member.email} is now ${roleLabel(next)}`);
    } catch (caught) {
      if (caught instanceof NotAvailableError) {
        setUnavailable(true);
        toast.info(MEMBERS_UNAVAILABLE_MESSAGE);
      } else {
        toast.error(describe(caught, "Could not change the role"));
      }
    } finally {
      setBusy(null);
    }
  };

  const invite = async (event: React.FormEvent) => {
    event.preventDefault();
    const clean = email.trim();
    if (!clean) return;
    setBusy("invite");
    try {
      const created = await inviteMember({ email: clean, role: inviteRole });
      setMembers((list) => [...list, created]);
      setInviteOpen(false);
      setEmail("");
      toast.success(`Invited ${clean} as ${roleLabel(inviteRole)}`);
    } catch (caught) {
      if (caught instanceof NotAvailableError) {
        setUnavailable(true);
        setInviteOpen(false);
        toast.info(MEMBERS_UNAVAILABLE_MESSAGE);
      } else {
        toast.error(describe(caught, "Could not send the invitation"));
      }
    } finally {
      setBusy(null);
    }
  };

  const remove = async (member: Member) => {
    setBusy(member.id);
    try {
      await removeMember(member.id);
      setMembers((list) => list.filter((row) => row.id !== member.id));
      toast.success(`${member.email} removed`);
    } catch (caught) {
      if (caught instanceof NotAvailableError) {
        setUnavailable(true);
        toast.info(MEMBERS_UNAVAILABLE_MESSAGE);
      } else {
        toast.error(describe(caught, "Could not remove the member"));
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="grid gap-6" data-testid="users-screen">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Users and roles</h2>
          <p className="text-sm text-muted-foreground">
            {owner
              ? "Invite colleagues and set what each of them may do."
              : "Your tenant owner manages who is here and what they may do."}
          </p>
        </div>
        {owner ? (
          <Button type="button" onClick={() => setInviteOpen(true)} disabled={unavailable}>
            Invite member
          </Button>
        ) : null}
      </div>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {unavailable ? (
        <p className="text-sm text-muted-foreground" data-testid="members-unavailable">
          {MEMBERS_UNAVAILABLE_MESSAGE}. Members are provisioned on first sign-in until then.
        </p>
      ) : null}

      {!unavailable && loaded ? (
        <div className="rounded-xl border">
          <Table data-testid="members-table">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Member</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                {owner ? <TableHead className="text-right">Actions</TableHead> : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.map((member) => {
                const last = isLastOwner(members, member.id);
                return (
                  <TableRow key={member.id} data-testid="member-row" data-email={member.email}>
                    <TableCell>
                      <span className="flex flex-col leading-tight">
                        <span className="font-medium">{member.name || member.email}</span>
                        {member.name ? (
                          <span className="text-xs text-muted-foreground">{member.email}</span>
                        ) : null}
                      </span>
                    </TableCell>
                    <TableCell>
                      {owner ? (
                        <NativeSelect
                          className="w-44"
                          aria-label={`Role for ${member.email}`}
                          value={member.role}
                          disabled={busy === member.id || last}
                          onChange={(event) => void changeRole(member, event.target.value)}
                        >
                          {ROLE_META.map((meta) => (
                            <option key={meta.value} value={meta.value}>
                              {meta.label}
                            </option>
                          ))}
                        </NativeSelect>
                      ) : (
                        <span>{roleLabel(member.role)}</span>
                      )}
                      {last ? (
                        <p className="mt-1 text-xs text-muted-foreground">
                          The last owner keeps the role.
                        </p>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{member.status ?? "active"}</Badge>
                    </TableCell>
                    {owner ? (
                      <TableCell className="text-right">
                        <Button
                          type="button"
                          variant="ghost"
                          size="xs"
                          disabled={busy === member.id || last || member.user_id === currentUserId}
                          onClick={() => void remove(member)}
                          aria-label={`Remove ${member.email}`}
                        >
                          Remove
                        </Button>
                      </TableCell>
                    ) : null}
                  </TableRow>
                );
              })}
              {!members.length ? (
                <TableRow>
                  <TableCell colSpan={owner ? 4 : 3} className="py-6 text-center text-muted-foreground">
                    Nobody else yet.
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </div>
      ) : null}

      <section aria-labelledby="roles-heading" className="grid gap-2">
        <h3 id="roles-heading" className="text-sm font-medium">
          What each role may do
        </h3>
        <dl className="grid gap-2 text-sm">
          {[...ROLE_META, PLATFORM_ADMIN_META].map((meta) => (
            <div key={meta.value} className="grid gap-0.5">
              <dt className="font-medium">{meta.label}</dt>
              <dd className="text-muted-foreground">{roleDescription(meta.value)}</dd>
            </div>
          ))}
        </dl>
      </section>

      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent data-testid="invite-dialog">
          <form onSubmit={invite} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Invite a member</DialogTitle>
              <DialogDescription>
                They get a magic-link sign-in for this tenant with the role you choose.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-1.5">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
                autoFocus
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="invite-role">Role</Label>
              <NativeSelect
                id="invite-role"
                value={inviteRole}
                onChange={(event) => setInviteRole(event.target.value)}
              >
                {ROLE_META.map((meta) => (
                  <option key={meta.value} value={meta.value}>
                    {meta.label}
                  </option>
                ))}
              </NativeSelect>
              <p className="text-xs text-muted-foreground">{roleDescription(inviteRole)}</p>
            </div>
            <DialogFooter>
              <DialogClose render={<Button type="button" variant="outline" />}>Cancel</DialogClose>
              <Button type="submit" disabled={busy === "invite" || !email.trim()}>
                Send invitation
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

"use client";

import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { useNow } from "@/components/opportunities/due-time";
import { KeyDatesPanel } from "@/components/pursuits/key-dates-panel";
import { TasksPanel } from "@/components/pursuits/tasks-panel";
import { ActivityTab } from "@/components/pursuits/workspace/activity-tab";
import { BidNoBidTab } from "@/components/pursuits/workspace/bid-no-bid-tab";
import { ChecklistTab } from "@/components/pursuits/workspace/checklist-tab";
import { DraftsTab } from "@/components/pursuits/workspace/drafts-tab";
import { MatrixTab } from "@/components/pursuits/workspace/matrix-tab";
import { PricingTab, readPricing, type PricingSummary } from "@/components/pursuits/workspace/pricing-tab";
import { WorkspaceHeader } from "@/components/pursuits/workspace/workspace-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { TabPanel, Tabs } from "@/components/ui/tabs";
import { errorMessage } from "@/lib/api/browser";
import { ApiError, getOpportunity, type OpportunityDetail } from "@/lib/opportunities/api";
import {
  getPursuit,
  listComments,
  listTasks,
  type PursuitComment,
  type PursuitOut,
  type PursuitTask,
} from "@/lib/pursuits/api";
import { canComment, canDecide, canExport, canReadDrafts } from "@/lib/pursuits/roles";
import {
  activityTimeline,
  DEFAULT_TAB,
  isTabId,
  readScorecard,
  TABS,
  type Scorecard,
  type TabId,
} from "@/lib/pursuits/workspace";
import {
  createExport,
  getMatrix,
  getPacket,
  getProfile,
  listDrafts,
  listExports,
  latestArtifact,
  type ChecklistItem,
  type DraftSummary,
  type Export,
  type ExportFormat,
  type Matrix,
  type Packet,
} from "@/lib/pursuits/workspace-api";
import { listMembers, type Member } from "@/lib/settings/api";
import type { Role } from "@/types/next-auth";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

type ProfileApprovers = { required_approver_roles?: string[] | null } | null;

/**
 * SPEC 10.4 screen 6: the pursuit workspace. One header (stage, owner,
 * deadline, cost meter, agent runs, the two gates and the exports) over the
 * seven tabs the spec names.
 */
export function PursuitWorkspace({ pursuitId, role }: { pursuitId: string; role: Role | undefined }) {
  const router = useRouter();
  const params = useSearchParams();
  const now = useNow();

  const urlTab = params.get("tab");
  const tab: TabId = isTabId(urlTab) ? urlTab : DEFAULT_TAB;
  const linkedTaskId = params.get("task");

  const [pursuit, setPursuit] = React.useState<PursuitOut | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [opportunity, setOpportunity] = React.useState<OpportunityDetail | null>(null);
  const [members, setMembers] = React.useState<Member[]>([]);
  const [profile, setProfile] = React.useState<ProfileApprovers>(null);
  const [matrix, setMatrix] = React.useState<Matrix | null>(null);
  const [matrixError, setMatrixError] = React.useState<string | null>(null);
  const [packet, setPacket] = React.useState<Packet | null>(null);
  const [packetError, setPacketError] = React.useState<string | null>(null);
  const [drafts, setDrafts] = React.useState<DraftSummary[]>([]);
  const [tasks, setTasks] = React.useState<PursuitTask[]>([]);
  const [comments, setComments] = React.useState<PursuitComment[]>([]);
  const [exports, setExports] = React.useState<Export[]>([]);
  const [packageFinal, setPackageFinal] = React.useState(false);
  const [exporting, setExporting] = React.useState<ExportFormat | null>(null);
  const [scorecard, setScorecard] = React.useState<Scorecard | null>(null);
  const [pricing, setPricing] = React.useState<PricingSummary | null>(null);
  const [reloadToken, setReloadToken] = React.useState(0);

  const mayReadDrafts = canReadDrafts(role);
  const mayExport = canExport(role);

  const setTab = React.useCallback(
    (next: string, extra?: Record<string, string | null>) => {
      const query = new URLSearchParams(params.toString());
      query.set("tab", next);
      for (const [key, value] of Object.entries(extra ?? {})) {
        if (value === null) query.delete(key);
        else query.set(key, value);
      }
      router.replace(`?${query.toString()}`, { scroll: false });
    },
    [params, router],
  );

  // the pursuit itself, and everything that hangs off it
  React.useEffect(() => {
    const controller = new AbortController();
    getPursuit(pursuitId, controller.signal)
      .then((row) => {
        setPursuit(row);
        setError(null);
        getOpportunity(row.opportunity_id, controller.signal)
          .then(setOpportunity)
          .catch(() => setOpportunity(null));
        getProfile(row.profile_id, controller.signal)
          .then((row2) => setProfile(row2 as ProfileApprovers))
          .catch(() => setProfile(null));
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(describe(caught, "This pursuit could not be read"));
      });
    listMembers(controller.signal)
      .then(setMembers)
      .catch(() => setMembers([]));
    return () => controller.abort();
  }, [pursuitId, reloadToken]);

  React.useEffect(() => {
    const controller = new AbortController();
    getMatrix(pursuitId, controller.signal)
      .then((row) => {
        setMatrix(row);
        setMatrixError(null);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setMatrixError(describe(caught, "The compliance matrix could not be read"));
      });
    getPacket(pursuitId, controller.signal)
      .then((row) => {
        setPacket(row);
        setPacketError(null);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setPacketError(describe(caught, "The submission packet could not be read"));
      });
    listTasks(pursuitId, controller.signal)
      .then((list) => setTasks(list.items))
      .catch(() => setTasks([]));
    if (canComment(role)) {
      listComments(pursuitId, {}, controller.signal)
        .then((list) => setComments(list.items))
        .catch(() => setComments([]));
    }
    if (mayReadDrafts) {
      listDrafts(pursuitId, controller.signal)
        .then((list) => setDrafts(list.items ?? []))
        .catch(() => setDrafts([]));
    }
    if (mayExport) {
      listExports(pursuitId, controller.signal)
        .then((list) => {
          setExports(list.items ?? []);
          setPackageFinal(!!list.package_final);
        })
        .catch(() => setExports([]));
    }
    void latestArtifact(pursuitId, "scorecard", controller.signal).then((row) =>
      setScorecard(readScorecard(row)),
    );
    void latestArtifact(pursuitId, "pricing_template", controller.signal).then((row) =>
      setPricing(readPricing(row)),
    );
    return () => controller.abort();
  }, [pursuitId, reloadToken, role, mayReadDrafts, mayExport]);

  const memberName = React.useCallback(
    (userId: string | null) => {
      if (!userId) return "Someone";
      const member = members.find((row) => row.user_id === userId);
      return member?.name ?? member?.email ?? userId;
    },
    [members],
  );

  const lookup = React.useCallback(
    (userId: string | null) => {
      if (!userId) return null;
      const member = members.find((row) => row.user_id === userId);
      return member ? { name: member.name ?? null, email: member.email ?? null } : null;
    },
    [members],
  );

  const runExport = React.useCallback(
    async (format: ExportFormat) => {
      setExporting(format);
      try {
        const created = await createExport(pursuitId, format);
        setExports((current) => [created, ...current.filter((row) => row.id !== created.id)]);
        if (created.url && typeof window !== "undefined") {
          window.open(created.url, "_blank", "noopener,noreferrer");
        }
        toast.success(`${format.toUpperCase()} ready: ${created.file_name}`);
      } catch (caught) {
        toast.error(describe(caught, `The ${format.toUpperCase()} export failed`));
      } finally {
        setExporting(null);
      }
    },
    [pursuitId],
  );

  const refreshExports = React.useCallback(() => {
    listExports(pursuitId)
      .then((list) => {
        setExports(list.items ?? []);
        setPackageFinal(!!list.package_final);
      })
      .catch(() => undefined);
  }, [pursuitId]);

  if (error) {
    return (
      <Card role="alert" data-testid="pursuit-error">
        <CardHeader>
          <CardTitle>Could not load this pursuit</CardTitle>
          <CardDescription>{error}</CardDescription>
        </CardHeader>
        <CardContent />
      </Card>
    );
  }

  if (!pursuit) {
    return <p className="text-sm text-muted-foreground">Loading the pursuit…</p>;
  }

  const events = activityTimeline({ pursuit, drafts, tasks, comments, exports });
  const linkedTask = linkedTaskId ? tasks.find((row) => row.id === linkedTaskId) ?? null : null;
  const tabItems = TABS.map((item) => {
    if (item.id === "drafts" && pursuit.drafts?.unsupported_claims_count) {
      return {
        ...item,
        badge: (
          <Badge variant="destructive" data-testid="drafts-tab-badge">
            {pursuit.drafts.unsupported_claims_count}
          </Badge>
        ),
      };
    }
    if (item.id === "matrix" && pursuit.matrix_recheck_required) {
      return { ...item, badge: <Badge variant="destructive">re-check</Badge> };
    }
    return item;
  });

  return (
    <div className="grid gap-5">
      <WorkspaceHeader
        pursuit={pursuit}
        role={role}
        title={opportunity?.title ?? null}
        buyer={opportunity?.buyer_org ?? null}
        ownerName={pursuit.owner_user_id ? memberName(pursuit.owner_user_id) : null}
        exports={exports}
        packageFinal={packageFinal}
        exporting={exporting}
        onExport={(format) => void runExport(format)}
        onPursuit={(next) => setPursuit(next)}
        onExportsChanged={refreshExports}
      />

      <Tabs items={tabItems} value={tab} onValueChange={(next) => setTab(next)} label="Pursuit workspace" />

      <TabPanel id="bid-no-bid" active={tab === "bid-no-bid"}>
        <BidNoBidTab
          pursuit={pursuit}
          scorecard={scorecard}
          mayDecide={canDecide(role, profile)}
          ownerName={memberName}
          onDecided={(next) => {
            setPursuit(next);
            setReloadToken((token) => token + 1);
          }}
        />
      </TabPanel>

      <TabPanel id="matrix" active={tab === "matrix"}>
        <MatrixTab
          matrix={matrix}
          error={matrixError}
          documents={opportunity?.documents ?? []}
          opportunityId={pursuit.opportunity_id}
          lookup={lookup}
          recheckRequired={pursuit.matrix_recheck_required}
        />
      </TabPanel>

      <TabPanel id="drafts" active={tab === "drafts"}>
        <DraftsTab
          pursuitId={pursuitId}
          role={role}
          onOpenTask={(taskId) => setTab("tasks", { task: taskId })}
          onChanged={() => setReloadToken((token) => token + 1)}
        />
      </TabPanel>

      <TabPanel id="pricing" active={tab === "pricing"}>
        <PricingTab
          pricing={pricing}
          mayExport={mayExport}
          exporting={exporting}
          onExport={(format) => void runExport(format)}
        />
      </TabPanel>

      <TabPanel id="checklist" active={tab === "checklist"}>
        <ChecklistTab
          packet={packet}
          checklist={(packet?.checklist ?? matrix?.checklist ?? []) as ChecklistItem[]}
          error={packetError}
        />
      </TabPanel>

      <TabPanel id="tasks" active={tab === "tasks"} className="grid gap-4 pt-4">
        {linkedTask ? (
          <Card data-testid="linked-task">
            <CardHeader>
              <CardTitle className="text-sm">Task behind that [NEEDS INPUT] chip</CardTitle>
              <CardDescription>
                {linkedTask.title} · {linkedTask.status}
              </CardDescription>
            </CardHeader>
            {linkedTask.detail ? (
              <CardContent className="text-sm text-muted-foreground">{linkedTask.detail}</CardContent>
            ) : null}
          </Card>
        ) : null}
        <TasksPanel pursuitId={pursuitId} />
        <KeyDatesPanel pursuitId={pursuitId} region={opportunity?.region ?? null} />
      </TabPanel>

      <TabPanel id="activity" active={tab === "activity"}>
        <ActivityTab
          events={events}
          pursuitId={pursuitId}
          now={now}
          ownerName={memberName}
          canComment={canComment(role)}
        />
      </TabPanel>
    </div>
  );
}

"use client";

import { ChevronRightIcon, ExternalLinkIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { ActionBar } from "@/components/opportunities/action-bar";
import { AttributionFooter } from "@/components/opportunities/attribution-footer";
import { NoticeTypeBadge, RegionBadge, StatusBadge } from "@/components/opportunities/badges";
import { DocumentsList } from "@/components/opportunities/documents-list";
import { DueTime, useNow } from "@/components/opportunities/due-time";
import { EligibilityList } from "@/components/opportunities/eligibility-list";
import { FitScoreCard } from "@/components/opportunities/fit-score-card";
import { VersionsPanel } from "@/components/opportunities/versions-panel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage } from "@/lib/api/browser";
import { formatMoney, type Currency } from "@/lib/money";
import { ApiError, getOpportunity, normalizeMatch, type OpportunityDetail } from "@/lib/opportunities/api";
import { buyerPath, formatValueRange, truncate } from "@/lib/opportunities/format";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; record: OpportunityDetail }
  | { kind: "missing" }
  | { kind: "error"; message: string };

type Contact = { name?: string | null; title?: string | null; email?: string | null; phone?: string | null; kind?: string | null };
type AlsoFrom = { source_id?: string; external_id?: string; source_url?: string | null; source_name?: string | null };

const SOURCE_NAMES: Record<string, string> = {
  sam_opps: "SAM.gov",
  sam_awards: "SAM.gov awards",
  grants_gov: "Grants.gov",
  usaspending: "USAspending",
  gem: "GeM",
  cppp: "CPPP",
};

function sourceLabel(id: string | undefined, name: string | null | undefined): string {
  return name || (id ? SOURCE_NAMES[id] ?? id : "Other source");
}

function placeOfPerformance(value: Record<string, unknown> | null): string | null {
  if (!value) return null;
  const parts = ["city", "state", "country", "pin", "zip", "pincode"]
    .map((key) => value[key])
    .filter((v): v is string => typeof v === "string" && v.trim() !== "");
  const text = parts.join(", ");
  if (value.remote === true) return text ? `${text} (remote allowed)` : "Remote";
  return text || null;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  if (children === null || children === undefined || children === "") return null;
  return (
    <div className="grid gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function Money({ amount, currency }: { amount: string | null; currency: string }) {
  if (!amount) return null;
  const code: Currency = currency === "INR" ? "INR" : "USD";
  return <span className="tabular-nums">{formatMoney(amount, code, { fractionDigits: 0 })}</span>;
}

export function DetailScreen({ id }: { id: string }) {
  const now = useNow();
  const [state, setState] = React.useState<LoadState>({ kind: "loading" });
  const [reload, setReload] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    getOpportunity(id, controller.signal)
      .then((record) => setState({ kind: "ready", record }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 404) {
          setState({ kind: "missing" });
          return;
        }
        setState({
          kind: "error",
          message: error instanceof ApiError ? errorMessage(error.body, `Request failed (${error.status})`) : "Could not reach the API.",
        });
      });
    return () => controller.abort();
  }, [id, reload]);

  if (state.kind === "loading") {
    return (
      <div role="status" aria-live="polite" className="grid gap-4">
        <span className="sr-only">Loading opportunity</span>
        <div className="h-4 w-48 animate-pulse rounded bg-muted" aria-hidden="true" />
        <div className="h-8 w-2/3 animate-pulse rounded bg-muted" aria-hidden="true" />
        <div className="h-40 animate-pulse rounded-xl bg-muted" aria-hidden="true" />
      </div>
    );
  }

  if (state.kind === "missing" || state.kind === "error") {
    return (
      <Card role="alert" data-testid="detail-error" className="max-w-lg">
        <CardHeader>
          <CardTitle>{state.kind === "missing" ? "Opportunity not found" : "Could not load this opportunity"}</CardTitle>
          <CardDescription>
            {state.kind === "missing"
              ? "It may have been merged into another record or removed by its source."
              : state.message}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex gap-2">
          <Button render={<Link href="/app/opportunities" />} variant="outline" size="sm">
            Back to search
          </Button>
          {state.kind === "error" ? (
            <Button type="button" size="sm" onClick={() => setReload((n) => n + 1)}>
              Retry
            </Button>
          ) : null}
        </CardContent>
      </Card>
    );
  }

  const record = state.record;
  const buyer = buyerPath(record);
  const value = formatValueRange(record);
  const match = normalizeMatch(record.match);
  const summaryLines = (record.summary_ai ?? "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const contacts = (Array.isArray(record.contacts) ? record.contacts : []) as Contact[];
  const alsoFrom = (record.also_from ?? []) as AlsoFrom[];
  const pop = placeOfPerformance(record.place_of_performance as Record<string, unknown> | null);
  const codes = [
    record.naics.length ? `NAICS ${record.naics.join(", ")}` : null,
    record.psc.length ? `PSC ${record.psc.join(", ")}` : null,
    record.aln.length ? `ALN ${record.aln.join(", ")}` : null,
    record.india_category.length ? `Category ${record.india_category.join(", ")}` : null,
  ].filter(Boolean);

  return (
    <article className="grid gap-6" data-testid="opportunity-detail" data-id={record.id}>
      <nav aria-label="Breadcrumb" className="text-sm text-muted-foreground">
        <ol className="flex flex-wrap items-center gap-1">
          <li>
            <Link href="/app/opportunities" className="underline-offset-4 hover:underline">
              Opportunities
            </Link>
          </li>
          {buyer.map((crumb, index) => (
            <li key={`${crumb}-${index}`} className="flex items-center gap-1">
              <ChevronRightIcon className="size-3.5" aria-hidden="true" />
              <span aria-current={index === buyer.length - 1 ? "location" : undefined}>{crumb}</span>
            </li>
          ))}
        </ol>
      </nav>

      <header className="grid gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <NoticeTypeBadge type={record.notice_type} />
          <StatusBadge status={record.status} />
          <RegionBadge region={record.region} />
          {record.solicitation_number ? (
            <span className="text-xs text-muted-foreground">{record.solicitation_number}</span>
          ) : null}
          {record.set_aside ? <span className="text-xs text-muted-foreground">Set-aside: {record.set_aside}</span> : null}
          {record.reservation ? <span className="text-xs text-muted-foreground">Reservation: {record.reservation}</span> : null}
        </div>
        <h1 className="text-2xl font-semibold leading-tight tracking-tight">{record.title}</h1>
        <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2 xl:grid-cols-4">
          <Fact label="Response due">
            <DueTime value={record.response_due_at} sourceTz={record.source_tz} now={now} showCountdown withYear />
          </Fact>
          <Fact label="Posted">
            <DueTime value={record.posted_at} sourceTz={record.source_tz} withYear />
          </Fact>
          {record.questions_due_at ? (
            <Fact label="Questions due">
              <DueTime value={record.questions_due_at} sourceTz={record.source_tz} now={now} showCountdown withYear />
            </Fact>
          ) : null}
          {record.prebid_meeting_at ? (
            <Fact label="Pre-bid meeting">
              <DueTime value={record.prebid_meeting_at} sourceTz={record.source_tz} withYear />
            </Fact>
          ) : null}
          {record.opening_at ? (
            <Fact label="Bid opening">
              <DueTime value={record.opening_at} sourceTz={record.source_tz} withYear />
            </Fact>
          ) : null}
        </dl>
        <ActionBar opportunityId={record.id} />
        {record.detail_status === "manual" ? (
          <p className="text-sm text-amber-900 dark:text-amber-200">
            The source detail page needs a person (CAPTCHA or login wall); open the official portal for the full notice.
          </p>
        ) : null}
      </header>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="grid content-start gap-6">
          <Card data-testid="summary-card">
            <CardHeader>
              <CardTitle>Summary</CardTitle>
              <CardDescription>
                {summaryLines.length ? "Five-line AI summary. Verify against the documents." : "From the notice text; an AI summary appears once generated."}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {summaryLines.length ? (
                <ul className="grid gap-1.5 text-sm">
                  {summaryLines.map((line, index) => (
                    <li key={index} className="flex gap-2">
                      <span className="w-4 shrink-0 tabular-nums text-muted-foreground">{index + 1}.</span>
                      <span>{line}</span>
                    </li>
                  ))}
                </ul>
              ) : record.description_text ? (
                <p className="whitespace-pre-line text-sm">{truncate(record.description_text, 1200)}</p>
              ) : (
                <p className="text-sm text-muted-foreground">No description was published; see the source documents.</p>
              )}
            </CardContent>
          </Card>

          <EligibilityList eligibility={record.eligibility} />
          <DocumentsList documents={record.documents} sourceUrl={record.attribution.source_url} />
          <VersionsPanel versions={record.versions} currentVersion={record.version} sourceTz={record.source_tz} />
        </div>

        <div className="grid content-start gap-6">
          <FitScoreCard match={match} />

          <Card data-testid="facts-card">
            <CardHeader>
              <CardTitle>Key facts</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="grid gap-3">
                <Fact label="Estimated value">
                  {value.primary ? (
                    <>
                      <span className="tabular-nums">{value.primary}</span>
                      {value.usd ? <span className="text-muted-foreground"> ≈ {value.usd}</span> : null}
                    </>
                  ) : (
                    <span className="text-muted-foreground">Not stated</span>
                  )}
                </Fact>
                <Fact label="EMD">
                  <Money amount={record.emd_amount} currency={record.currency} />
                </Fact>
                <Fact label="Tender fee">
                  <Money amount={record.tender_fee} currency={record.currency} />
                </Fact>
                <Fact label="Buyer">{buyer.length ? buyer.join(" › ") : null}</Fact>
                <Fact label="Codes">{codes.length ? codes.join(" · ") : null}</Fact>
                <Fact label="Place of performance">{pop}</Fact>
                <Fact label="Incumbent">{record.incumbent}</Fact>
                <Fact label="Prior award">
                  {record.prior_award_value ? (
                    <>
                      <Money amount={record.prior_award_value} currency="USD" />
                      {record.prior_pop_end ? <span className="text-muted-foreground"> · period ended {record.prior_pop_end}</span> : null}
                    </>
                  ) : null}
                </Fact>
                <Fact label="Record">
                  <span className="text-muted-foreground">
                    v{record.version} · {record.source_id}/{record.external_id}
                  </span>
                </Fact>
              </dl>
            </CardContent>
          </Card>

          <Card data-testid="contacts-card">
            <CardHeader>
              <CardTitle>Contacts</CardTitle>
              <CardDescription>Official contacts as published by the buyer.</CardDescription>
            </CardHeader>
            <CardContent>
              {contacts.length === 0 ? (
                <p className="text-sm text-muted-foreground">None published.</p>
              ) : (
                <ul className="grid gap-3">
                  {contacts.map((contact, index) => (
                    <li key={index} className="grid gap-0.5 text-sm">
                      <span className="font-medium">
                        {contact.name || "Contracting office"}
                        {contact.kind ? <span className="ml-1 text-xs font-normal capitalize text-muted-foreground">({contact.kind})</span> : null}
                      </span>
                      {contact.title ? <span className="text-muted-foreground">{contact.title}</span> : null}
                      {contact.email ? (
                        <a href={`mailto:${contact.email}`} className="underline-offset-4 hover:underline">
                          {contact.email}
                        </a>
                      ) : null}
                      {contact.phone ? (
                        <a href={`tel:${contact.phone}`} className="underline-offset-4 hover:underline">
                          {contact.phone}
                        </a>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {alsoFrom.length ? (
            <Card data-testid="also-from-card">
              <CardHeader>
                <CardTitle>Also listed on</CardTitle>
                <CardDescription>The same notice seen on other sources and merged into this record.</CardDescription>
              </CardHeader>
              <CardContent>
                <ul className="grid gap-1.5 text-sm">
                  {alsoFrom.map((link, index) => (
                    <li key={`${link.source_id}-${link.external_id}-${index}`}>
                      {link.source_url ? (
                        <a
                          href={link.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 underline-offset-4 hover:underline"
                        >
                          {sourceLabel(link.source_id, link.source_name)}
                          {link.external_id ? <span className="text-muted-foreground">· {link.external_id}</span> : null}
                          <ExternalLinkIcon className="size-3.5" aria-hidden="true" />
                        </a>
                      ) : (
                        <span>
                          {sourceLabel(link.source_id, link.source_name)}
                          {link.external_id ? <span className="text-muted-foreground"> · {link.external_id}</span> : null}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>

      <AttributionFooter attribution={record.attribution} disclaimer={record.disclaimer} sourceUrl={record.attribution.source_url} />
    </article>
  );
}

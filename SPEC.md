# BidRadar — Contract Discovery & Bid Automation: Requirements Spec

_Text export of the source PDF (Sep 26, 2026). The PDF in the parent folder is authoritative._


  BidRadar — Contract Discovery & Bid
  Automation: Requirements Spec
    Sep 26, 2026         · ​@Vrkeesara15

  1. Summary, goals and scope
  BidRadar (working name) finds every public contract that fits a company's profile, alerts
  the team the moment one appears, drafts the response package with AI agents, and
  reminds the team until a human reviews and submits it on time. Yes, the data exists: US
  federal and grant notices come from free official APIs, and Indian central, GeM and state
  tenders are published on public portals (section 2).

  Business goals

   1. Internal first: feed our own biz-dev pipeline (the IRS sources-sought pursuit is the
      pattern to automate).
   2. Then sell it as multi-tenant SaaS, with India companies as the first external market
      (Indian tenders + Indian bidders pursuing US work).
   3. Build and ship an MVP in one week of focused Claude build credit; harden over the
      following weeks.

  In scope (MVP, v1)

       Company profile with every field bids ask for (US + India).
       Ingestion from SAM.gov, Grants.gov, USAspending, CPPP, GeM and 3+ Indian state
       portals.
       Normalized opportunity store with dedupe, amendments and corrigenda tracking.
       Fit scoring against the profile (rules + embeddings + LLM rationale).
       Alerts: in-app, email, Slack/Teams, WhatsApp (India) — instant and daily digest.
       Agent pipeline that downloads documents, extracts requirements, runs bid/no-bid,
       and drafts the response package into a review queue.
       Deadline tracker, reminder ladder, and a pipeline board (Kanban).
       Multi-tenant auth, roles, billing hooks, audit log.

  Out of scope (v1)

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

         Auto-submitting bids. A human always submits on the portal (DSC in India, SAM login
         in US).
         Bypassing CAPTCHAs, logins or portal terms. Paid aggregators are optional adapters,
         not scraped.
         Pricing/cost volume generation beyond a template with placeholders.

  Success metrics (first 60 days)

        Metric                                                                        Target

        New matching notices surfaced within                                          60 min of posting (US APIs), 6 h (India
                                                                                      portals)

        Precision of "High fit" alerts (human-rated                                   ≥ 70%
        relevant)

        Draft package ready after human clicks                                        ≤ 30 min for a 50-page solicitation
        "Pursue"

        Deadlines missed on tracked pursuits                                          0

        Submissions made per month (internal)                                         ≥4

        Paying pilot tenants (India)                                                  2 by day 60

  2. Where new contracts are published
  US federal opportunities and grants have free official APIs; Indian tenders are public web
  portals with no official API, so they need polite, compliant crawlers of public listing pages.
  Build adapters in the priority order below.

    #          Source                   Region              What it gives            Access               Auth                Limits and gotchas

    1          SAM.gov Get              US federal          All contract notices:    REST                 Free API key from   Daily quota depends
               Opportunities API v2                         presolicitation,         api.sam.gov/opport   SAM.gov account     on role (non-federal
                                                            solicitation,            unities/v2/search    (Login.gov)         keys are low; request
                                                            combined, sources                                                 a system account for
                                                            sought (RFI), special,                                            higher); max 1,000
                                                            award, J&A                                                        records/page;
                                                                                                                               postedFrom / posted
                                                                                                                              To required,
                                                                                                                              MM/dd/yyyy,
                                                                                                                              window ≤ 1 year;
                                                                                                                              attachments via
                                                                                                                              resourceLinks

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

    #          Source                   Region              What it gives           Access                 Auth                  Limits and gotchas

    2          SAM.gov Contract         US federal          Past awards:            REST via               SAM.gov API key       FPDS.gov was
               Awards API                                   incumbent, value,       open.gsa.gov                                 decommissioned Feb
               (replaced FPDS                               period of                                                            24, 2026; ATOM feed
               ATOM feed)                                   performance,                                                         retires in FY2026 —
                                                            number of offers —                                                   do not build on FPDS
                                                            for recompete
                                                            detection and
                                                            competitor intel

    3          USAspending.gov API      US federal          Obligations by          REST POST              None                  Page-based
                                                            agency/NAICS/PSC/r      /api/v2/search/spe                           pagination capped at
                                                            ecipient since          nding_by_award/                              50,000 results/query;
                                                            FY2008; expiring                                                     DoD data lags ~90
                                                            contracts                                                            days

    4          Grants.gov API           US federal          Grant/cooperative       POST                   None for              Two records per
                                        grants              agreement NOFOs,        api.grants.gov/v1/     search2/fetchOppo     opportunity can exist
                                                            forecasts, ALN          api/search2 ,          rtunity               — keep latest version
                                                            codes, eligibility      fetchOpportunity

    5          defense.gov/News/C       US DoD              Same-day awards >       HTML/RSS daily 5 PM    None                  Awards only; good
               ontracts                                     $9M                     ET                                           for competitor
                                                                                                                                 tracking

    6          US state & local         US SLED             State RFPs/RFQs         Mixed: some            Some need vendor      50 states × many
               (SLED): e.g. Cal                                                     RSS/CSV exports,       registration          portals: v1 = 5 target
               eProcure, Texas                                                      most HTML                                    states via official
               ESBD, NYS Contract                                                                                                exports; wider SLED
               Reporter, eVA (VA),                                                                                               via paid API (row 11)
               county/city portals

    7          CPPP —                   India central +     Mandatory               Public HTML listing    None to view; Class   Search forms and
               eprocure.gov.in          aggregated          publication point for   pages                  3 DSC + enrolment     archive require
                                        state/PSU           central                                        to bid                CAPTCHA; per-
                                                            ministries/CPSEs;                                                    tender links expire.
                                                            "Latest active                                                       Use only captcha-
                                                            tenders", tenders by                                                 free listing pages
                                                            organisation/location                                                (e.g. "Tenders by
                                                            /classification,                                                     Organisation"), store
                                                            corrigenda, bid                                                      portal URL + tender
                                                            awards                                                               ID for humans to
                                                                                                                                 open

    8          GeM —                    India central +     Bids and reverse        Public bid listing +   GeM seller            No public API; listing
               bidplus.gem.gov.in       state               auctions for            bid document PDFs      registration to bid   is JS-driven; parse
                                                            goods/services; bid                                                  bid PDF for eligibility
                                                            PDF per bid; results                                                 (turnover,
                                                            with L1/L2                                                           experience,
                                                                                                                                 MSE/startup
                                                                                                                                 exemption, EMD)

    9          NIC GePNIC state         India state         State department        Public HTML listing    DSC to bid            One GePNIC adapter
               portals (same                                and PSU tenders                                                      + per-portal config
               software as CPPP):                                                                                                covers most states
               e.g. etenders.gov.in,
               Maharashtra, UP
               (etender.up.nic.in),
               Tamil Nadu,
               Telangana, Karnataka
               (KPPP), Rajasthan
               (eproc)

    10         IREPS (Railways),        India               Sector tenders          Public HTML            Vendor registration   Phase 2
               defproc (Defence),
               PSU portals (IOCL,
               BHEL, NTPC, etc.)

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

    #          Source                   Region              What it gives          Access         Auth       Limits and gotchas

    11         Optional paid feeds:     Both                Pre-normalized,        Licensed API   Paid key   Use as adapters
               HigherGov API                                wider coverage incl.                             when a tenant
               (federal + SLED),                            SLED                                             supplies a licence;
               GovSpend, BidNet;                                                                             never scrape their
               India: Tender Tiger,                                                                          sites
               Tender247, BidAssist

  Decision for v1: rows 1, 2, 3, 4, 7, 8 and three GePNIC state portals (Telangana, Karnataka,
  Maharashtra — confirm list in section 14). Everything else ships as a documented adapter
  stub.

  3. Users, roles and tenancy
  One codebase serves our internal team and paying tenants; every row carries tenant_id
  and is isolated by Postgres row-level security.

     Role                                Can do

     Platform admin (us)                 Manage tenants, plans, source adapters, global
                                         settings, view system health; no access to tenant
                                         drafts unless support access is granted and logged

     Tenant owner                        Billing, users, company profile(s), integrations, data
                                         retention

     Bid manager                         Configure searches and alert rules, move pursuits
                                         through stages, assign owners, approve bid/no-bid,
                                         approve final package

     Writer / SME                        Edit drafts, answer agent questions, upload past
                                         performance and resumes

     Reviewer                            Comment and approve sections only

     Viewer                              Read-only dashboards

  Tenancy rules

         A tenant may hold several company profiles (e.g. US entity + Indian subsidiary, or a
         consultancy bidding for clients). Matching and drafting always run per profile.
         Plans: Free (1 profile, 1 source region, digest only), Pro (3 profiles, all sources, instant
         alerts, 10 agent drafts/month), Enterprise (unlimited, SSO, private LLM key, data
         residency). Limits enforced server-side via a plan_limits table.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

       Data residency: tenant chooses us or in region at signup; data and files stay in that
       region (section 11).
       Our own internal tenant is created by seed script and flagged is_internal = true
       for unlimited usage.

  4. Company profile — every field needed
  The profile drives three things: matching (codes, keywords, geography), eligibility checks
  (registrations, size, turnover, certifications) and drafting (narratives, past performance,
  people). Onboarding is a 7-step wizard; the agent pre-fills from the company website, a
  capability statement upload and SAM.gov entity data, then a human confirms each field.
  Fields marked US or IN show only for that region.

  4.1 Identity and registrations

     Field                                       Type               Req        Region   Used for

     Legal name, DBA/trade                       text               Y          both     Cover letters, forms
     names

     Registered address,                         address[]          Y          both     Place-of-performance
     HQ, branch offices                                                                 match, forms

     Website, main phone,                        text               Y          both     Forms, notifications
     bid-inbox email

     Year founded, legal                         enum               Y          both     Eligibility
     structure (LLC, Corp,
     Pvt Ltd, LLP,
     partnership,
     proprietorship)

     UEI (SAM Unique Entity                      text(12)           Y for US   US       Validates SAM
     ID)                                                                                registration via Entity
                                                                                        API

     CAGE code                                   text(5)            N          US       Forms

     SAM registration status                     date               Y for US   US       Blocks bids if expired;
     + expiry date                                                                      renewal reminder at
                                                                                        60/30/7 days

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

     Field                                       Type               Req          Region   Used for

     EIN                                         text               N (stored    US       Forms only
                                                                    encrypted,
                                                                    masked)

     GSA Schedule / MAS,                         list               N            US       Matches task orders
     GWACs, IDIQs, BPAs                                                                   under vehicles held
     held (vehicle, number,
     expiry)

     PAN, GSTIN, CIN/LLPIN,                      text               Y for IN     IN       Eligibility, forms
     TAN                                                                                  (encrypted, masked)

     Udyam (MSME)                                text/enum          N            IN       EMD exemption and
     registration no. +                                                                   MSE purchase
     category                                                                             preference
     (micro/small/medium)

     DPIIT Startup India                         text               N            IN       Relaxation of prior-
     recognition no.                                                                      turnover/experience
                                                                                          criteria where the
                                                                                          tender allows

     GeM seller ID +                             text/list          N            IN       GeM bid eligibility
     registered categories

     CPPP / state portal                         list               N            IN       Links to portals (no
     enrolment IDs                                                                        passwords stored)

     Class 3 DSC holder                          text/date          N            IN       Reminder before
     name + expiry                                                                        expiry — an expired
                                                                                          DSC blocks
                                                                                          submission

     Class-I/Class-II local                      enum/number        N            IN       Purchase preference
     supplier status (Make in                                                             eligibility
     India) and local content
     %

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

  4.2 Size, finances and socio-economic status

     Field                                                  Type                  Region    Used for

     Employee count (total, by                              int                   both      Size standards, staffing
     country)                                                                               claims

     Annual revenue last 3 years                            money[]               both      US size standards (SBA),
                                                                                            India turnover criteria
                                                                                            (usually average of last 3
                                                                                            FYs)

     Net worth, solvency certificate                        money/bool            IN        Common tender criteria
     available (Y/N)

     Audited financials available for                       list                  both      Checklist
     FYs

     Bonding capacity / bank                                money                 both      Performance security,
     guarantee limit                                                                        EMD/BG

     Small-business status per                              computed              US        Set-aside eligibility
     NAICS (auto-computed from
     revenue/employees vs SBA
     table)

     Socio-economic certifications:                         multi-enum + cert     US        Set-aside filter
     8(a), HUBZone,                                         no. + expiry
     WOSB/EDWOSB, SDVOSB,
     VOSB, SDB

     SC/ST-owned or women-                                  enum                  IN        Reserved procurement
     owned MSE                                                                              share

  4.3 What we sell

     Field                                                            Type                             Used for

     NAICS codes (primary + secondary)                                list, validated against 2022     SAM matching
                                                                      NAICS

     PSC codes                                                        list                             SAM matching

     ALN/CFDA programs (grants)                                       list                             Grants matching

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

     Field                                                          Type                                Used for

     GeM categories, CPV-like Indian product                        list                                India matching
     categories, CPPP "product category"

     Capability keywords (include) and                              tag lists with weights              Scoring, noise
     exclusion keywords                                                                                 filter

     Service lines: name, 150-word                                  structured list                     Matching +
     description, differentiators,                                                                      drafting
     tools/platforms, delivery model

     Capability statement PDF, brochures,                           files                               RAG knowledge
     case studies                                                                                       base

  4.4 Where and how big

     Field                                                           Type             Used for

     Target countries, US states, Indian                             lists            Geography filter
     states/UTs, cities; remote OK (Y/N)

     Target agencies/ministries/PSUs (include)                       lists            Boost / exclude
     and blocked buyers

     Contract value range min–max per                                money            Filter
     currency (USD, INR)                                             range

     Notice types wanted (sources sought,                            multi-           Filter
     RFI, RFP, RFQ, grant, award for                                 enum
     recompete, EOI, GeM bid, RA)

     Contract types preferred (FFP, T&M,                             multi-           Scoring
     cost-plus, IDIQ task order, rate contract)                      enum

     Teaming: willing to prime / sub / JV; known                     enum +           Suggest teaming when we miss a
     partners with their UEI/PAN and                                 list             requirement
     capabilities

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

  4.5 Proof (for drafting)

     Field                                                          Type                     Used for

     Past performance records: title,                               structured list, min 3   Past-performance volume,
     customer (can be anonymized),                                                           relevance scoring
     agency type, value, period, role
     (prime/sub), NAICS, scope,
     outcomes with numbers,
     technologies, reference contact,
     CPARS rating, public/confidential
     flag

     Key personnel: name, role, years,                              list                     Staffing plan, résumés
     clearances, certifications (PMP,
     AWS, GCP, CISSP…), education,
     résumé file

     Labor categories and rate card                                 table                    Pricing template
     (US), man-month rates (IN)                                                              placeholders

     Security: facility clearance level,                            multi-enum + cert        Eligibility + compliance
     personnel clearances count,                                    files + expiry           matrix
     CMMC level, FedRAMP, SOC 2, ISO
     27001/9001/20000, CMMI level,
     STQC/CERT-In empanelment

     Insurance: GL, professional liability,                         list                     Forms
     cyber (limits, expiry)

     Boilerplate library: company                                   rich text per item       Drafting
     overview, management approach,
     QA plan, transition plan, security
     approach, diversity, sustainability

     Past proposals (won/lost) with                                 files + metadata         Style and win-theme learning
     outcome and debrief notes

     Brand: logo, colours, fonts, proposal                          files                    Output formatting
     template (.docx)

  4.6 Preferences
       Alert channels per user, quiet hours, time zone, digest time.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

       Minimum fit score for instant alert (default 70) and for digest (default 50).
       Bid/no-bid criteria weights (section 6) and required approvers.
       Languages for output: English default; Hindi summaries optional for India tenants.

  Profile completeness score (0–100) shows on the dashboard; matching runs from 40,
  drafting needs 70 plus ≥ 3 past performances.

  5. Ingestion, normalization and dedupe
  Each source is a pluggable adapter that emits raw records; a shared pipeline normalizes
  them into one opportunity schema, detects changes, and triggers matching.

  5.1 Adapter contract (Python)

     class SourceAdapter(Protocol):
         source_id: str             # 'sam_opps', 'sam_awards', 'usaspending',
     'grants_gov', 'cppp', 'gem', 'gepnic_ts', ...
         region: Literal['us','in']
             schedule: str             # cron, e.g. '*/30 * * * *'
             def fetch(self, since: datetime, cursor: str | None) ->
     Iterator[RawRecord]: ...
         def fetch_detail(self, external_id: str) -> RawRecord: ...
             def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]: ...
             def normalize(self, raw: RawRecord) -> OpportunityIn: ...
             def health(self) -> AdapterHealth: ...

  Rules every adapter follows

       Incremental: keep a per-source watermark ( last_posted_at , cursor); overlap each
       window by 2 days to catch late edits.
       Politeness: token-bucket rate limiter per host (default 1 req/s for India portals, quota-
       aware for SAM.gov keys), exponential backoff with jitter on 429/5xx, identifying User-
       Agent with contact email.
       Compliance: respect robots.txt and portal terms; never solve or bypass CAPTCHA;
       never log in to a portal on a user's behalf; public pages only. If a page needs CAPTCHA,
       store the tender ID + portal search URL and mark detail_status = 'manual' .
       Raw payload (JSON/HTML/PDF) stored immutably in object storage under
        raw/{source}/{yyyy}/{mm}/{dd}/{external_id}/{fetched_at} for replay and audit.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

       Contract tests with recorded fixtures (VCR-style) for every adapter; a nightly live
       smoke test per source alerts us when a portal layout changes.

  5.2 Source-specific notes
       SAM.gov opportunities: poll every 30 min with postedFrom = watermark − 2d ,
          limit=1000 , paginate by offset . Pull description via the description link and
       attachments via resourceLinks . Map type codes (p, o, k, r, s, i, a, u, g). Track
          solicitationNumber to link amendments to their parent.

       SAM.gov awards: daily; used to enrich opportunities with incumbent, prior value and
       period of performance; flag contracts ending in 6–18 months as "recompete watch".
       USAspending: weekly; builds agency spend-by-NAICS stats for the profile's codes
       ("who buys what we sell").
       Grants.gov: every 2 h via search2 (posted + forecasted), then fetchOpportunity for
       eligibility, award ceiling/floor, close date.
       CPPP: every 3 h, read captcha-free listing pages (latest active, by organisation); parse
       tender ID, reference no., title, organisation, published, closing, opening dates,
       corrigendum flag, detail/document links where accessible without CAPTCHA.
       GeM: every 3 h, listing of ongoing bids; download bid PDF; extract with PDF parser +
       LLM: item/service, quantity, estimated value, EMD, turnover and experience criteria,
       MSE/startup exemptions, bid end date, consignee locations.
       GePNIC state portals: one adapter class, per-portal config (base URL, org list pages,
       date formats).

  5.3 Canonical opportunity schema

     Field                                                          Type   Notes

     id                                                             uuid   Internal

     source_id, external_id, source_url                             text   Unique on (source_id, external_id)

     region, country, currency                                      enum   us/in; USD/INR

     notice_type                                                    enum   rfi, sources_sought,
                                                                           presolicitation, rfp, rfq, combined,
                                                                           grant, forecast, award, eoi,
                                                                           gem_bid, reverse_auction,
                                                                           corrigendum, special

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

     Field                                                          Type               Notes

     title, description_text, summary_ai                            text               summary_ai = 5-line LLM summary

     solicitation_number,                                           text / uuid        Links amendments/corrigenda
     parent_opportunity_id

     buyer_org, buyer_sub_org,                                      text               Agency → sub-tier → office;
     buyer_office, buyer_hierarchy[]                                                   Ministry → Dept → Org

     naics[], psc[], aln[],                                         text[]
     india_category[]

     set_aside, reservation                                         enum               US set-asides; India MSE/SC-
                                                                                       ST/women reservations

     place_of_performance                                           jsonb              city, state, country, pin/zip, remote
                                                                                       flag

     estimated_value_min/max,                                       numeric            In source currency + USD
     emd_amount, tender_fee                                                            normalized

     posted_at, questions_due_at,                                   timestamptz        All stored UTC, displayed in user
     prebid_meeting_at,                                                                time zone; source time zone kept
     response_due_at, opening_at,
     archive_at

     contacts                                                       jsonb              Official contracting officer
                                                                                       contacts as published

     eligibility                                                    jsonb              turnover, experience,
                                                                                       certifications, registrations,
                                                                                       exemptions (LLM-extracted, with
                                                                                       page citations)

     documents                                                      relation           file name, url, hash, size, pages,
                                                                                       parsed text, status

     incumbent, prior_award_value,                                  text / numeric /   From awards enrichment
     prior_pop_end                                                  date

     status                                                         enum               open, closing_soon, closed,
                                                                                       cancelled, awarded

     content_hash, version                                          text / int         Change detection

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

     Field                                                          Type           Notes

     embedding                                                      vector(1024)   pgvector, on title + summary +
                                                                                   requirements

     raw_ref                                                        text           Pointer to raw payload

  5.4 Dedupe and change tracking
       Exact key: (source_id, external_id). Cross-source key: normalized solicitation/tender
       reference number + buyer. Fuzzy fallback: title trigram similarity ≥ 0.9 and same buyer
       and response date within 1 day → merge as duplicate_of . CPPP often mirrors GeM
       and state tenders; prefer the richer record and keep both source links.
       On every fetch compute content_hash over normalized fields + document hashes. A
       change creates opportunity_version with a field-level diff (deadline moved, new
       attachment, Q&A posted, cancelled) and fires an "amendment" event to everyone
       tracking that opportunity.
       Status job every 15 min: open → closing_soon (≤ 7 days) → closed; cancellation/award
       notices update the parent.

  6. Matching and fit scoring
  Every new or changed opportunity is scored against every active profile in its region in
  three stages: hard filters (cheap), a weighted score (0–100), then an LLM rationale only
  for scores ≥ 50.

  Stage 1 — hard filters (drop if any fails)

       Region/country allowed by profile; notice type wanted; not from a blocked buyer;
       response date in the future (or recompete watch).
       Exclusion keywords absent from title/summary.
       Set-aside the company cannot meet (e.g. 8(a) only, company not 8(a)) → keep but cap
       score at 30 and label "Ineligible: set-aside" (still useful for teaming).

  Stage 2 — weighted score

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

     Signal                                            Weight       How computed

     Code match                                        25           Exact NAICS/PSC/ALN/GeM category = 1.0; same
                                                                    4-digit NAICS = 0.6

     Semantic similarity                               25           Cosine of opportunity embedding vs profile
                                                                    service-line embeddings (max)

     Keyword match                                     10           Weighted include keywords, BM25 over title +
                                                                    description + parsed docs

     Eligibility                                       15           Turnover, experience years, certifications,
                                                                    registrations, size status vs extracted criteria;
                                                                    missing data = 0.5

     Value fit                                         5            Inside value range = 1, within 2× = 0.5

     Geography                                         5            Place of performance in target list or remote

     Buyer affinity                                    5            Past customer or target buyer = 1

     Past-performance                                  10           Best cosine vs past-performance records
     relevance

  Weights are editable per profile. Score bands: High ≥ 70 (instant alert), Medium 50–69
  (digest), Low < 50 (visible in search only).

  Stage 3 — AI rationale (Claude, for score ≥ 50)

  Returns strict JSON: fit_summary (3 bullets), matched_capabilities[] , gaps[] (each
  with suggested fix: teaming partner, hire, certification), eligibility_risks[] with page
  citations, recommended_action (pursue / watch / pass) and confidence . Cached by
  (opportunity version, profile version).

  Learning loop

       Thumbs up/down and "Not relevant because…" on every alert; stored as
       match_feedback .
       Weekly job re-tunes keyword weights and suggests new include/exclude keywords for
       the owner to approve (never silently applied).
       Saved searches: users can save any filter set; each saved search is also an alert rule.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

  7. Notifications
  A High-fit match or a change to a tracked pursuit must reach the right person within 5
  minutes of scoring, on the channel they chose, exactly once.

     Event                                                          Default channels         Timing

     New High-fit match (≥ 70)                                      In-app + email +         Instant (respect quiet
                                                                    Slack/Teams              hours → queued to next
                                                                                             morning unless due < 72
                                                                                             h)

     New Medium match (50–69)                                       Email digest             Daily at user's digest time;
                                                                                             weekly roll-up Monday

     Amendment/corrigendum on a                                     In-app + email + Slack   Instant, with a field-level
     tracked opportunity (deadline                                                           diff
     moved, new doc, Q&A, cancellation)

     Deadline reminder (section 9)                                  Email + Slack +          Reminder ladder
                                                                    WhatsApp (IN) +
                                                                    calendar

     Agent draft ready for review /                                 In-app + email to        Instant
     needs input                                                    assignee

     Registration expiring (SAM, DSC,                               Email to owner           60/30/7 days before
     certifications, insurance)

     Adapter failure > 2 runs (admins                               Slack ops channel +      Instant
     only)                                                          PagerDuty-style email

  Requirements

       Channels v1: in-app bell + web push, email (SendGrid or AWS SES; Indian tenants via
       SES Mumbai), Slack (incoming webhook + app with "Pursue / Pass / Assign" buttons),
       Microsoft Teams (webhook), WhatsApp Business API via a BSP (Gupshup/Twilio) with
       pre-approved templates, iCal feed + Google/Outlook calendar events for deadlines.
       Every notification links to the opportunity page and has one-click actions: Pursue,
       Watch, Pass (with reason), Assign.
       Idempotency key = (user, event_type, opportunity_id, version); a delivery log records
       sent/failed/opened; failed sends retry 3× then fall back to email.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

         Per-user settings: channels per event, quiet hours, time zone, digest time, minimum
         score.
         Email content: title, buyer, value, due date in the user's time zone with countdown, fit
         score + 3-bullet rationale, top gap, links. Unsubscribe link per category (CAN-SPAM).

  8. Agent pipeline — from notice to review-ready package
  When a user clicks Pursue, a durable workflow runs eight agents and stops at two human
  gates; nothing is ever submitted by the system. Target: a first full draft within 30 minutes
  for a 50-page solicitation.

     #          Agent                            Input              Output (stored, versioned)   Gate

     1          Document                         Opportunity +      All attachments              —
                collector                        source links       downloaded, hashed,
                                                                    virus-scanned (ClamAV),
                                                                    converted to text (PDF,
                                                                    DOCX, XLSX, scanned PDF
                                                                    via OCR), split into
                                                                    sections with page
                                                                    numbers

     2          Requirements                     Parsed docs        requirements[] : id, text,   —
                extractor                                           source doc + page, type
                                                                    (shall/must/should,
                                                                    eligibility, format,
                                                                    submission, evaluation
                                                                    criterion), volume it
                                                                    belongs to

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

     #          Agent                            Input                Output (stored, versioned)     Gate

     3          Compliance                       Requirements         Matrix: requirement →          —
                matrix builder                                        proposal section → owner
                                                                      → status; format rules
                                                                      (page limits, fonts, file
                                                                      naming, portal, copies);
                                                                      submission checklist incl.
                                                                      forms (SF-33/SF-1449, reps
                                                                      & certs; India: EMD/BG,
                                                                      affidavits, turnover
                                                                      certificate, DSC-signed
                                                                      covers)

     4          Bid/no-bid                       Matrix + profile +   Scorecard (fit, eligibility,   Gate 1:
                analyst                          awards data          capacity,                      human
                                                                      competition/incumbent,         approves
                                                                      value, win probability 0–      Bid / No-bid
                                                                      100), gaps, teaming
                                                                      suggestions,
                                                                      recommendation with
                                                                      reasons

     5          Outline & win-                   Matrix + profile     Proposal outline mirroring     —
                theme writer                                          evaluation criteria (Section
                                                                      L/M in US;
                                                                      technical/financial covers
                                                                      in India), 3–5 win themes,
                                                                      discriminators

     6          Section drafters                 Outline + RAG        Drafts: cover letter,          —
                (parallel, one per               over profile,        executive summary,
                volume)                          boilerplate, past    technical approach,
                                                 performance, past    management plan, staffing
                                                 proposals            + résumés, past
                                                                      performance write-ups,
                                                                      capability one-pager,
                                                                      responses to RFI
                                                                      questions, India technical
                                                                      bid forms

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

     #          Agent                            Input                 Output (stored, versioned)     Gate

     7          Pricing template                 Requirements +        Spreadsheet with labour        —
                builder                          rate card             categories/man-months
                                                                       and placeholders; never
                                                                       invents prices

     8          Red-team                         Full draft + matrix   Scores each section            Gate 2:
                reviewer                                               against evaluation criteria,   human
                                                                       lists non-compliant or         review, edit
                                                                       unsupported claims,            and approve
                                                                       missing requirements,
                                                                       page-limit overruns; drafts
                                                                       auto-revised once,
                                                                       remaining issues become
                                                                       review comments

  Guardrails (hard requirements)

         Grounding: every factual claim about the company must cite a profile record, past-
         performance ID or uploaded file; unsupported claims are highlighted red and listed.
         The agent never invents past performance, certifications, people, numbers or prices —
         it writes [NEEDS INPUT: …] placeholders and creates a task for the owner.
         Every extracted requirement carries its document + page citation, clickable in the UI.
         Structured outputs validated against JSON Schema (Pydantic); invalid output retried
         up to 2× then flagged.
         Model routing: Claude Opus-class model for extraction, bid/no-bid and red-team;
         Sonnet-class for section drafting; Haiku-class for summaries and classification. Model
         IDs are config, not code. Prompt caching on the solicitation text and profile.
         Cost guard: per-tenant monthly token budget and per-pursuit cap (default USD 15),
         shown in UI; stop and ask when exceeded.
         Confidential data: tenant data is never used across tenants; RAG indexes are per
         tenant.
         Human-in-the-loop edits are diffed and saved as feedback to improve future drafts.

  Outputs the reviewer gets

         A pursuit workspace with the compliance matrix, draft sections in a rich-text editor
         with comments/track changes, the checklist and a task list.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

       Export: DOCX in the tenant's template, PDF, XLSX (matrix, pricing), and a ZIP named
       per the solicitation's file-naming rules.
       "Submission packet" page: what to upload where, portal link, required signatures/DSC
       steps, and the final deadline in both the buyer's and user's time zone.

  9. Deadlines, reminders and pipeline tracker
  Every pursuit has an internal deadline 48 hours before the real one, and reminders
  escalate until someone marks it submitted, passed or cancelled.

  Pipeline stages: Identified → Qualifying → Bid decision → Drafting → In review → Final
  approval → Submitted → Awarded / Lost / Cancelled / No-bid. Board (Kanban) and table
  views; filters by owner, due date, value, region, stage; drag to move with stage rules (e.g.
  cannot enter Drafting without Gate 1 approval).

  Key dates auto-created per pursuit (from the opportunity, editable): questions due, pre-
  bid meeting, internal draft complete (due − 5 days), internal review (due − 3 days), internal
  final (due − 48 h), portal submission due. India adds EMD/BG ready and DSC check (due −
  3 days).

  Reminder ladder for each key date: 7 days, 3 days, 24 h, 4 h, 1 h before, and overdue every
  4 h. Escalation: if not acknowledged by the owner 24 h before due, notify the bid manager;
  at 4 h, notify the tenant owner.

  Calendar: events pushed to Google/Outlook for assignees; per-user iCal feed; all times
  shown in user time zone with the buyer's time zone alongside (e.g. "Oct 14, 2:00 PM EDT =
  11:30 PM IST").

  Recurring checks: registrations and certificate expiry (section 4), stale pursuits with no
  activity for 5 days, opportunities amended after drafting started (forces a matrix re-
  check).

  Dashboard KPIs: open pursuits by stage, due in next 7 days, pipeline value by stage (USD
  and INR), win rate, average hours saved per package, alert precision from feedback.

  10. Architecture, stack, data model, API and screens
  A Python backend with scheduled workers and a Next.js front end, deployed on GCP
  Cloud Run in two regions, fits the team's existing GCP skills and keeps Indian tenant data
  in India.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

  system architecture · 6 layers

  Each layer only reads from the one above; agents never write to sources, and nothing
  leaves the Delivery layer without a person acting on it.

  10.1 Stack

     Layer                           Choice                                  Why

     Backend API                     Python 3.12, FastAPI, SQLAlchemy 2,     Team skill, typed schemas
                                     Alembic, Pydantic v2                    for agent outputs

     Jobs and                        Celery + Redis; agent workflow state    Durable, simple
     workflows                       persisted in agent_runs / agent_steps
                                     so any step resumes after a crash

     Scheduling                      Cloud Scheduler → Cloud Run jobs (one   Isolated failures, per-source
                                     per adapter)                            cron

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

     Layer                           Choice                                     Why

     Database                        Cloud SQL Postgres 16 + pgvector +         One store for relational, full-
                                     pg_trgm, row-level security by             text and vectors
                                      tenant_id

     Files                           Google Cloud Storage, per-region           Residency
                                     buckets, signed URLs, CMEK

     LLM                             Claude API (Anthropic SDK), model IDs      Section 8
                                     in config, prompt caching, JSON-
                                     schema tool outputs

     Embeddings                      Configurable provider (default Voyage,     Matching
                                     1024-dim), batch embed on ingest

     Parsing                         PyMuPDF, pdfplumber, python-docx,          Solicitation docs
                                     openpyxl, Tesseract OCR (eng + hin),
                                     ClamAV

     Front end                       Next.js 15 (App Router), TypeScript,       Fast UI build
                                     Tailwind, shadcn/ui, TanStack Table,
                                     TipTap editor

     Auth                            Auth.js: email magic link, Google and      Residency
                                     Microsoft OAuth; SAML SSO for
                                     Enterprise; users stored in our regional
                                     DB

     Notifications                   SES/SendGrid, Slack app, Teams             Section 7
                                     webhook, WhatsApp BSP
                                     (Gupshup/Twilio), web push, iCal

     Billing                         Stripe (USD) and Razorpay (INR with        India selling
                                     GST invoices)

     Observability                   OpenTelemetry → Cloud Logging/Trace,       Debug + cost control
                                     Sentry, Langfuse for LLM traces and
                                     cost

     IaC and CI                      Terraform, GitHub Actions, Docker;         Section 12
                                     preview env per PR

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

  10.2 Data model (tables)
       Tenancy: tenants , users , memberships(role) , plan_limits , usage_ledger ,
        audit_log .
       Profile: company_profiles , profile_codes(naics/psc/aln/india_category) ,
        profile_keywords(include/exclude, weight) , service_lines , past_performance ,
        personnel , certifications , registrations , vehicles , teaming_partners ,
        boilerplate_blocks , profile_files , kb_chunks(embedding) .
       Sources: sources , source_runs(status, counts, errors, watermark) .
       Opportunities: opportunities , opportunity_versions(diff) ,
        opportunity_documents , document_chunks(embedding, page) , awards_enrichment .
       Matching: matches(profile, opportunity, version, score, breakdown jsonb,
       rationale jsonb) , match_feedback , saved_searches , alert_rules .
       Notify: notifications , notification_deliveries(channel, status,
       idempotency_key) , user_notification_prefs .
       Pursuits: pursuits(stage, owner, decision) , pursuit_dates , tasks , comments ,
       reminders .
       Agents: agent_runs , agent_steps(input_ref, output jsonb, tokens, cost,
       status) , requirements , compliance_items , drafts , draft_versions , exports .

  10.3 REST API (v1, all under /api/v1 , tenant from token)
        GET/PUT /profiles/{id} , POST /profiles/{id}/autofill (website URL, capability
       PDF, UEI), sub-resources for codes, keywords, past-performance, personnel,
       certifications, files.
        GET /opportunities?
       q=&region=&type=&naics=&due_before=&min_score=&status=&page= ; GET
       /opportunities/{id} (with versions, documents, match for the active profile).
        POST /opportunities/{id}/feedback , POST
       /opportunities/{id}/pursue|watch|pass .

        GET/POST /saved-searches , GET/POST /alert-rules , GET/PUT /me/notification-
       prefs .

        GET/PATCH /pursuits/{id} , POST /pursuits/{id}/decision , GET
       /pursuits/{id}/matrix , GET/PUT /pursuits/{id}/drafts/{section} , POST
       /pursuits/{id}/agents/run (step or all), POST /pursuits/{id}/export?
       format=docx|pdf|xlsx|zip .

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

        GET /calendar.ics?token= , POST /integrations/slack/actions , POST
       /webhooks/{provider} (billing, email events).
       Admin: GET /admin/sources , POST /admin/sources/{id}/run , GET
       /admin/tenants .
       OpenAPI spec generated; typed TS client generated for the front end.

  10.4 Screens
   1. Sign-up → region choice → onboarding wizard (7 steps, section 4) with autofill and
      completeness meter.
   2. Home: today's High-fit matches, due this week, pipeline value, alerts inbox.
   3. Opportunity search: filters left, results table (score, title, buyer, value, due, type,
       source), saved searches.
   4. Opportunity detail: summary, fit score breakdown + rationale, eligibility checks
      (pass/fail/unknown), documents viewer, versions/diff, actions.
   5. Pipeline board and table.
   6. Pursuit workspace: tabs for Bid/no-bid, Compliance matrix, Drafts (editor + comments
      + citations panel), Pricing, Checklist, Tasks, Activity.
   7. Calendar view of all key dates.
   8. Settings: profile, users and roles, notifications, integrations, billing, data export/delete.
   9. Admin console: sources health, run history, tenants, usage and LLM cost.

  11. Security, compliance and legal
  The product handles unreleased bid strategy and company financials, so tenant isolation,
  encryption and an audit trail are launch blockers, not later work.

  Security

       Row-level security on every tenant table plus a test that fails CI if any query path skips
       tenant_id .
       Encryption: TLS 1.2+, CMEK at rest; field-level encryption (AES-GCM via KMS) for EIN,
       PAN, GSTIN, TAN, bank details; masked in UI (last 4).
       Never store portal passwords, DSC private keys or SAM.gov login credentials. API keys
       (SAM.gov, paid feeds, tenant's own Claude key) in Secret Manager, referenced by ID.
       RBAC per section 3; every read of a draft/export and every admin support access
       written to audit_log (who, what, when, IP).

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

       Uploaded files: type allow-list, 50 MB cap, ClamAV scan before parsing.
       LLM safety: solicitation text and portal pages are untrusted input — agents treat them
       as data; tool access for agents is limited to read-only retrieval and writing to the
       pursuit's own records. Prompt-injection test cases in the eval suite.
       OWASP ASVS L2 checklist, dependency scanning (Dependabot, pip-audit, npm audit),
       secrets scanning, rate limiting on auth and API.
       Backups: daily Cloud SQL backups + PITR 7 days; restore drill before launch.

  Privacy and residency

       India: tenants in region in store all data in asia-south1 (Mumbai) to align with the
       Digital Personal Data Protection Act 2023; consent notice at signup, data-principal
       requests (access, correction, erasure) via Settings, grievance officer contact. Confirm
       exact obligations with counsel (section 14).
       US: privacy policy, CCPA-style deletion/export, SOC 2 readiness controls logged from
       day one.
       LLM provider: use zero-data-retention/no-training terms on the Anthropic API org; list
       sub-processors publicly.

  Source compliance

       Public-domain US federal data (SAM.gov, USAspending, Grants.gov) — follow each
       API's terms and quotas; one key per environment; no key sharing across tenants unless
       the terms allow a system account.
       Indian portals: public pages only, low request rates, respect robots.txt, no CAPTCHA
       solving, no automated login, show source attribution and a link back to the official
       portal on every record. Get a written legal opinion before commercial resale of Indian
       portal data.
       Paid aggregators only through licensed APIs with the tenant's or our licence.

  Product disclaimers

       "Verify every detail on the official portal before submitting." on every opportunity and
       export.
       AI drafts are labelled as drafts; the export carries an internal footer until a human
       marks it final.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

  12. Deploy, test and India testing
  Four environments, one pipeline: every merge deploys to dev, a tag promotes to staging,
  and production runs in both US and India regions from the same image.

     Env                    Where                                   Data                     Purpose

     local                  Docker Compose                          Fixtures + recorded      Dev and
                            (Postgres+pgvector, Redis,              source responses         Claude loop
                            MinIO, Mailpit)

     dev                    Cloud Run, us-east1                     Live sources with low    Every merge
                                                                    quotas, synthetic        to main
                                                                    tenants

     staging-in             Cloud Run, asia-south1                  Live Indian sources, 2   India testing
                            (Mumbai)                                pilot India tenants

     prod-us /              Cloud Run us-east1 / asia-south1        Real tenants             Launch
     prod-in

  Test plan

       Unit: every normalizer, scorer, date/time-zone function, eligibility rule; ≥ 85% line
       coverage on core/ .
       Adapter contract tests: recorded fixtures for each source (including a malformed page
       and a layout change) must parse to the canonical schema.
       Live smoke (nightly): each adapter fetches ≥ 1 record from the real source; failure
       pages the admin channel.
       Integration: ingest → match → notify end-to-end on local stack with Mailpit asserting
       the email.
       Tenant isolation: automated test creates two tenants and tries every endpoint cross-
       tenant; any 200 with foreign data fails the build.
       Agent evals: a golden set of 10 US solicitations (incl. the IRS sources-sought notice
       pattern) and 10 Indian tenders (CPPP, GeM, state) with hand-labelled requirements.
       Pass bars: requirement extraction recall ≥ 90%, precision ≥ 85%; zero fabricated
       company facts; every requirement cites a page; eligibility extraction (turnover, EMD,
       experience) exact match ≥ 90%.
       UI: Playwright flows for onboarding, search, pursue, review, export; axe accessibility
       check.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

       Load: 50k opportunities, 200 profiles scored in < 10 min; search p95 < 500 ms.

  India testing checklist (staging-in)

       Dates: DD-MM-YYYY and "17-Jul-2026 08:23 PM" formats parse correctly; IST shown;
       overnight US deadlines shown in IST.
       Money: INR with lakh/crore formatting (₹12,50,000; ₹1.2 Cr), EMD and tender fee
       extracted.
       Content: Hindi/bilingual tender titles and PDFs (OCR hin) do not break parsing;
       transliterated organisation names dedupe.
       MSME/Udyam and Startup exemptions correctly change eligibility results.
       WhatsApp templates approved and delivered; SES Mumbai email deliverability (SPF,
       DKIM, DMARC).
       Razorpay test payments with GST invoice; data stays in asia-south1 (verify bucket and
       DB locations).
       Latency from Indian ISPs (Jio, Airtel) p95 page load < 2.5 s.
       Two pilot users run a real GeM bid and a CPPP tender end-to-end to a reviewed
       package.

  Acceptance criteria for MVP (all must pass)

       A new SAM.gov notice matching the internal profile appears in the app and in Slack
       within 60 minutes.
       A new CPPP or GeM tender matching an India test profile appears within 6 hours.
       Clicking Pursue produces a compliance matrix + full draft + bid/no-bid scorecard
       within 30 minutes, with page citations.
       Deadline reminders fire on the ladder in the user's time zone; calendar events created.
       DOCX/PDF/XLSX/ZIP exports open cleanly in Word, Acrobat and Excel.
       Cross-tenant test suite passes; audit log records every draft access.
       India checklist above passes in staging-in.

  13. Build plan for Claude Code in a loop
  Run Claude Code headless in a loop against a repo that holds this spec, a machine-
  readable task list and tests; each iteration picks the next failing task, builds it, runs tests,
  commits, and stops only when every acceptance test passes.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

  13.1 Repo layout

     bidradar/
       CLAUDE.md                                           # rules for the agent (below)
       SPEC.md                                             # this document, exported
       tasks.json                                          # ordered tasks with id, milestone, depends_on,
     acceptance, status
       PROGRESS.md                                         # append-only log the loop writes each iteration
         backend/
           app/ (api/, core/, models/, services/, agents/, adapters/, notify/)
           migrations/
           tests/ (unit/, adapters/fixtures/, integration/, isolation/, evals/)
         frontend/ (Next.js app, generated API client)
         infra/ (terraform/, docker-compose.yml, cloudrun/)
         evals/golden/ (us/, in/ solicitations + labelled requirements)
         .github/workflows/ (ci.yml, deploy.yml, nightly-smoke.yml)

  13.2 Milestones (one week of build credit, in order)
   1. M0 Foundation (day 1): repo, Docker Compose, FastAPI skeleton, Postgres + pgvector
      + RLS, Auth.js, tenants/users/roles, CI with lint + tests, seed internal tenant. Exit:
       isolation test passes.
   2. M1 Profile (day 1–2): all section 4 tables, API, onboarding wizard, autofill from
      website/PDF/UEI, completeness score. Exit: profile round-trips via API and UI.
   3. M2 US ingestion (day 2–3): adapter framework, SAM.gov opportunities, Grants.gov,
       USAspending, SAM awards enrichment; normalization, dedupe, versions, documents
       parsing. Exit: 7 days of SAM data loaded, amendments linked.
   4. M3 India ingestion (day 3–4): CPPP, GeM (with bid PDF extraction), GePNIC generic
       adapter + 3 state configs; INR/IST handling. Exit: fixtures + live smoke green.
   5. M4 Matching + alerts (day 4–5): scoring, rationale, feedback, saved searches,
       email/Slack/in-app, digests, quiet hours. Exit: integration test ingest → Slack message.
   6. M5 Agents (day 5–6): 8-agent workflow, compliance matrix, drafts with citations, red-
      team, exports; cost guard. Exit: golden-set eval bars met.
   7. M6 Pursuits + reminders (day 6): board, key dates, reminder ladder, calendar/iCal,
       WhatsApp. Exit: reminder schedule test passes with time-zone cases.
   8. M7 Deploy + India testing (day 7): Terraform for dev, staging-in, prod-us, prod-in; billing
      (Stripe, Razorpay); observability; run section 12 checklists. Exit: all MVP acceptance
       boxes ticked.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

  13.3 CLAUDE.md (agent rules)

     - Read SPEC.md and tasks.json before any change. Work on ONE task: the first
     with status "todo" whose depends_on are all "done".
     - Write or update tests first from the task's acceptance criteria, then code
     until they pass.
     - Run: make lint && make test (and make eval when touching
     backend/app/agents). Never mark a task done with failing checks.
     - Never weaken, skip or delete a test to make it pass. If the spec is
     ambiguous, add a question to PROGRESS.md under "Open questions", pick the
     safest option, and continue.
     - Never commit secrets; read keys from env. Never add CAPTCHA solving, portal
     login automation or auto-submission.
     - Keep adapters behind the SourceAdapter protocol; keep model IDs in config.
     - After finishing: set task status "done", append a 3-line entry to
     PROGRESS.md (task, what changed, how verified), git commit with the task id.
     - If blocked 3 iterations on the same task, set status "blocked" with the
     reason and move to the next unblocked task.

  13.4 tasks.json entry format

     {
         "id": "M2-03",
         "milestone": "M2",
         "title": "SAM.gov opportunities adapter with incremental watermark",
         "spec_refs": ["5.1", "5.2", "5.3"],
         "depends_on": ["M2-01", "M2-02"],
       "acceptance": [
         "fetch() paginates with limit=1000 and offset until totalRecords
     reached",
             "postedFrom = watermark - 2 days, MM/dd/yyyy format",
             "fixture tests/adapters/fixtures/sam_page1.json normalizes to 1000
     OpportunityIn rows",
         "amendment with same solicitationNumber links parent_opportunity_id",
         "429 response triggers backoff and resumes from cursor"
         ],
         "status": "todo"
     }

  The first loop iteration's only job is to expand every section of this spec into tasks.json
  (target 80–120 tasks, each ≤ 2 hours of work, each with testable acceptance lines), then
  stop for a human to review the list.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

  13.5 Loop runner

     #!/usr/bin/env bash
     # loop.sh — run until all tasks are done or the budget is spent
     set -euo pipefail
     for i in $(seq 1 200); do
         remaining=$(jq '[.[] | select(.status=="todo")] | length' tasks.json)
         [ "$remaining" -eq 0 ] && echo "All tasks done" && break
         claude -p "Follow CLAUDE.md. Complete exactly one task from tasks.json,
     verify, commit, then stop." \
         --permission-mode acceptEdits --max-turns 60 \
          --output-format stream-json >> logs/iter-$i.jsonl
       make test || echo "iteration $i left tests red" >> PROGRESS.md
     done

  Check the current Claude Code CLI flags before running; they change between releases.
  Run the loop inside a container with no production credentials, and review PROGRESS.md
  and open questions at least twice a day.

  13.6 Definition of done
       All tasks done or explicitly deferred by a human; section 12 acceptance boxes ticked.
       CI green on main; nightly smoke green 3 nights running.
       README with setup, env vars, adapter guide and runbook for a broken source.

  14. Open decisions (answer before the loop starts)
       Product name and domain (BidRadar is a placeholder).
       Which company profile seeds the internal tenant, and who owns filling it (UEI, NAICS,
       past performance).
       SAM.gov key type: request a system account for higher quotas, or start with a personal
       non-federal key.
       Which 3 Indian state portals first (proposed: Telangana, Karnataka, Maharashtra) and
       whether IREPS/defproc are needed for pilots.
       Buy vs build for US SLED coverage: license HigherGov (or similar) API, or crawl 5 states.
       Hosting: GCP Cloud Run (proposed) vs Railway for the week-one demo.
       Embedding provider and whether India tenants need an in-region LLM endpoint.

BidRadar — Contract Discovery & Bid Automation: Requirements Spec

       Legal opinion on commercial use of Indian portal data and DPDP obligations; terms of
       service and privacy policy.
       Pricing for India (INR per month per profile) and the two pilot companies.

  Sources
       SAM.gov Get Opportunities Public API · SAM.gov Federal Hierarchy API
       SAM.gov announcement: contract awards search moved from FPDS · FPDS retirement
       timeline
       Grants.gov API guide
       USAspending award search API notes
       HigherGov API
       CPPP eProcurement · CPPP archive search (CAPTCHA) · UP GePNIC portal
       CPPP bidder registration and Class 3 DSC
       GeM bid data fields · CPPP/GePNIC captcha-free listing notes



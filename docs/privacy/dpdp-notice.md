# DPDP consent notice — v1

Shown at signup to every user of a tenant in the `in` region and available in Settings →
Privacy. Accepting it records a row in `consents` (kind `dpdp`, this version, the
timestamp and the caller's IP) through `POST /api/v1/me/consents`. Bumping
`DPDP_NOTICE_VERSION` in the environment makes the next acceptance a new row; the old one
is never overwritten, so the trail shows exactly which text each user agreed to.

This notice is drafted for the Digital Personal Data Protection Act, 2023 and must be
reviewed by counsel before launch (SPEC section 14).

## Who we are

BidRadar ("we") is the **Data Fiduciary** for the personal data described below. You are
the **Data Principal**.

## What we collect and why

| Data | Why we need it | Lawful basis |
| --- | --- | --- |
| Name, work email, time zone, locale | To create your account and address you correctly | Consent (contract performance) |
| Organisation details you enter in the company profile (legal name, addresses, registrations, GSTIN/PAN, certifications, personnel) | To match tenders to your company and to draft bid documents | Consent |
| Files you upload (capability statements, past proposals, brochures) | To answer tenders with your own material | Consent |
| Usage records: which notices you opened, which drafts you exported, admin support access | Security, billing and the audit trail required of us | Legitimate use (security and legal obligation) |
| Billing contact and GSTIN | To raise a GST invoice for an Indian subscription | Legal obligation |
| Notification preferences and channel identifiers (email, Slack, WhatsApp number) | To send the alerts you asked for | Consent |

We do **not** collect portal passwords, digital signature certificate private keys or
SAM.gov login credentials, and we never automate a login or a bid submission on a portal.

## Where your data is stored

If your tenant chose the `in` region, your rows and files stay in India (Google Cloud
`asia-south1`, Mumbai). If it chose `us`, they stay in the United States. The region is
fixed at signup.

## Who else processes it

The sub-processors listed in [sub-processors.md](./sub-processors.md), for the purposes
stated there. Our LLM provider is contracted under zero-data-retention and no-training
terms.

## How long we keep it

For as long as your tenant is active, and then until the tenant is deleted. A tenant
owner can erase everything at any time with **Settings → Data export/delete** (`POST
/api/v1/tenant/delete`): every row and every uploaded file is removed. We keep the audit
log of who did what, because we are required to be able to show it.

## Your rights

You may ask us, at any time and free of charge, to:

- **access** the personal data we hold about you — answered immediately by
  `POST /api/v1/me/data-requests {"kind": "access"}`;
- **correct** it — `{"kind": "correction"}`, or edit it yourself in Settings;
- **erase** it — `{"kind": "erasure"}`;
- **withdraw consent** — withdrawing it is as easy as giving it; note that without
  consent we cannot run matching or drafting for you;
- **nominate** another individual to exercise these rights if you are incapacitated;
- **complain** to the Data Protection Board of India if we do not answer you in time.

We answer within the window published at `GET /api/v1/privacy`
(`DATA_REQUEST_SLA_DAYS`, 30 days by default), counted from the day we receive the
request. Every request gets a tracking row with its due date, visible at
`GET /api/v1/me/data-requests`.

## Grievance officer

Named at `GET /api/v1/privacy` (`GRIEVANCE_OFFICER_NAME`, `GRIEVANCE_OFFICER_EMAIL`).
Write to them for anything about this notice or about a request that was not answered.

## Automated processing

Fit scores, bid/no-bid recommendations and draft text are produced by automated systems
and large language models. They are decision **support**: no bid is submitted and no
decision is taken about you automatically, and every generated claim carries a citation
back to a record you supplied.

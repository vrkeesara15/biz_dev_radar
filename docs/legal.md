# Legal and compliance notes (SPEC 11)

Status: internal engineering notes. **Not legal advice.** Items marked **ACTION** need a
written opinion from counsel before the related feature ships commercially.

## 1. What we collect and how

BidRadar reads **public** procurement pages only, through `app/adapters/http.py`:

* `robots.txt` is fetched and honoured before any request; a `Disallow` for our path
  stops the run (`RobotsDisallowedError`). `mahatenders.gov.in` answers `Disallow: /`,
  so Maharashtra is registered (`gepnic_mh`) but **never crawled** - link-only.
* one request per second per `*.gov.in` host, exponential backoff, a `User-Agent` that
  names the product and a contact mailbox.
* no CAPTCHA solving, no portal login, no account automation, no bid submission. A page
  that needs a CAPTCHA is recorded as `detail_status = "manual"` with the portal's own
  search URL so a human can open it (CPPP tender detail, GePNIC expired DirectLinks).
* every fetched payload is archived verbatim (`raw/<source>/<date>/...`) so any record
  can be traced back to the page it came from.

## 2. Attribution and link-back (implemented)

Every rendering of a notice carries the source and a link back to the official page,
from one place: `app/core/disclaimers.py`.

    attribution_text(source_id, source_url)
        "Source: Tamil Nadu Tenders (tntenders.gov.in) · Official notice: https://..."
    record_footer(source_id, source_url)
        the same line plus "Verify every detail on the official portal before submitting."

The opportunities API returns both on every record (`attribution.text`,
`attribution.footer`, `disclaimer`). **Exports and notification emails must render
`record_footer`** rather than compose their own wording; that is the single place where
the portal display names live (`app/core/attribution.py`, kept in step with
`app/adapters/gepnic_configs.yaml` by `tests/unit/test_attribution.py`).

The portal is always authoritative: we never present our copy of a deadline, value or
eligibility clause as the official text.

## 3. Indian portal data

**ACTION - written legal opinion required before commercial resale of Indian portal
data.** Aggregating and re-publishing tender data from Indian government portals for a
fee raises questions we must not answer ourselves:

* the terms of use of CPPP (`eprocure.gov.in`), GeM (`gem.gov.in`) and each GePNIC state
  instance, which differ per portal and are not uniformly published;
* the Government Open Data Licence - India (GODL-India) and whether the tender listings
  fall under it;
* whether value-added derivatives (our summaries, eligibility extraction, scores) are a
  permitted use or a restricted re-publication;
* attribution wording each portal requires.

Until that opinion exists: India sources stay available inside the product with full
attribution and link-back, and no Indian portal data is sold as a standalone data feed
or redistributed in bulk to third parties.

## 4. Personal data

* **DPDP Act 2023 (India).** Tender notices carry named contact officers (name,
  designation, office phone, official e-mail). This is personal data under the DPDP Act
  even when it is published by the government. We treat it as follows: it is stored only
  as published, used only to let a bidder contact the buyer about that tender, never
  enriched, never used for marketing and never sold. Contact fields age out with the
  notice (`archive_at`). Before any feature that aggregates officers across notices, or
  exports them separately from their notice, get an opinion on notice/consent and on the
  Significant Data Fiduciary thresholds.
* **GDPR / US state privacy law** apply to our own users' data (accounts, profiles,
  uploads), not to the public notices; see the tenancy and RLS notes in SPEC 9.

## 5. Paid feeds

`app/adapters/paid_feeds.py` (HigherGov, GovSpend, BidNet, TenderTiger, Tender247,
BidAssist) is registered disabled. None may be enabled without a signed subscription and
a licence reference recorded for the tenant; several require visible attribution on
display. Redistribution of a paid feed's records to other tenants is not permitted.

## 6. AI output

Summaries, extractions and drafts are labelled (`AI-generated draft. Review before
use.`), carry page citations where they make numeric claims (GeM bid extraction), and
are never presented as the portal's text. Document and portal content is treated as
untrusted data in every prompt (`app/agents/prompting.py`).

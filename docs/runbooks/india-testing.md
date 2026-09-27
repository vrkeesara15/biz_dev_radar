# Runbook — the India testing checklist (staging-in)

SPEC 12 ends with a checklist that has to pass in **staging-in** (Cloud Run asia-south1,
live Indian sources, two pilot India tenants) before India launch, and the MVP acceptance
list repeats it as a gate: *"India checklist above passes in staging-in."*

Roughly half of it is code behaviour and is automated in
[`backend/tests/integration/test_india_checklist.py`](../../backend/tests/integration/test_india_checklist.py)
— one test named after the checklist row it covers. The other half cannot be automated
without a real BSP, a real mailbox, a real payment page, a real Indian ISP or a real
bidder, so it is a person following the steps below and pasting the evidence into the
sign-off table at the bottom.

Run the automated subset:

```bash
make india-check                                          # once the target lands (see below)
cd backend && uv run pytest tests/integration/test_india_checklist.py
```

> `make india-check` is not in the Makefile yet: another agent held uncommitted Makefile
> changes when this page was written, so the target was deferred rather than conflict with
> it. The target is one stanza:
>
> ```make
> .PHONY: india-check
> india-check:
> 	cd $(BACKEND) && $(UV) run pytest tests/integration/test_india_checklist.py
> ```
>
> Add it under the existing `acceptance` target and add `india-check` to `.PHONY` and the
> `help` line.

## Before you start

- A staging-in deploy of the release candidate (see [deploy.md](deploy.md)), healthy on
  `/healthz`, with the Indian adapters scheduled.
- Two pilot India tenants seeded, each with a completed company profile (Udyam number,
  GeM seller id, DSC, three fiscal years of INR revenue) and one owner who can actually
  sign and submit on the portals.
- Access to: the BSP console (Gupshup or Twilio), the AWS account that owns the SES
  identity, the Razorpay dashboard in **test mode**, `gcloud` against
  `bidradar-staging-in`, and `terraform` in `infra/terraform/envs/staging-in`.
- An evidence folder for the run (`india-checklist/<YYYY-MM-DD>/`). Every row below names
  what to put in it. Screenshots need the URL bar visible.

## The checklist

Status is what the last run recorded: **automated-green** means the named test passes in
CI on every merge; **manual-pending** means nobody has run it against staging-in yet.

| Item (SPEC 12) | Automated? | Owner role | Steps | Evidence to capture | Status |
| --- | --- | --- | --- | --- | --- |
| Dates: `DD-MM-YYYY` and `17-Jul-2026 08:23 PM` parse correctly | `test_dates_both_portal_formats_parse_as_ist` | Backend | Both portal spellings, plus `17/07/2026 20:23`, `17 July 2026 8:23 PM` and `17-07-2026 20:23:00 Hrs (IST)`, land on the same UTC instant through `core.dates.parse_in`; an impossible date is `None`, never an exception | CI run link | automated-green |
| Dates: IST shown | `test_dates_an_indian_deadline_is_shown_in_ist` | Backend | `core.display_time.tz_fields` renders `Jul 17, 8:23 PM IST` with `+05:30` on the ISO field | CI run link | automated-green |
| Dates: overnight US deadlines shown in IST | `test_dates_an_overnight_us_deadline_is_shown_in_ist` | Backend | 5:00 PM EDT Oct 14 renders `Oct 14, 5:00 PM EDT = Oct 15, 2:30 AM IST` — the date must roll over or an Indian bidder loses a day | CI run link | automated-green |
| Dates: a pilot tenant sees IST everywhere in the UI | manual | Pilot / QA | Open an Indian opportunity, its pursuit, the key-date list, one reminder email and one calendar invite. Every time is IST (or dual-zone for a US notice) and matches the portal page | Screenshots of the four surfaces | manual-pending |
| Money: INR lakh/crore formatting (`₹12,50,000`, `₹1.2 Cr`) | `test_money_inr_lakh_and_crore_formatting_round_trips` | Backend | `format_inr` for both forms, and `parse_inr` back to the same `Decimal`, including `Rs. 1.2 Cr`, `45 Lakh (s)` and `₹25,000/- (Rupees Twenty Five Thousand only)` | CI run link | automated-green |
| Money: EMD extracted | `test_money_emd_is_extracted_from_the_gem_bid_pdf_onto_the_row` | Backend / Agents | The golden GeM bid PDF is replayed through `FakeLLM` and `2,40,000` lands on `opportunities.emd_amount` and on `eligibility.emd_amount_inr` | CI run link | automated-green |
| Money: tender fee extracted | `test_money_tender_fee_is_stored_and_reaches_the_submission_checklist` (partial) | Backend / Agents | `parse_inr` reads the portal wording, `opportunities.tender_fee` stores it, and the SPEC 8 checklist turns it into a required payment item. **No Indian adapter fills the column from a live page yet** (OQ-138) — so on staging-in, check by hand that a CPPP tender showing a document fee carries one | CI run link, plus the portal page and the `/opportunities/{id}` JSON for one real tender | manual-pending |
| Content: Hindi/bilingual titles and PDFs (OCR `hin`) do not break parsing | `test_content_hindi_title_and_pdf_parse_and_chunk_with_devanagari_intact` | Backend | A bilingual title survives byte for byte, the Devanagari PDF parses to 3 pages, chunks keep `पात्रता शर्तें`, the Devanagari reference normalises to its digits, and the IN OCR language set is `eng+hin` | CI run link | automated-green |
| Content: a real scanned Hindi tender PDF OCRs on staging-in | manual | Backend / QA | Pick a scanned (image-only) Hindi PDF from a live GePNIC state portal, attach it to a pursuit, and read the parsed text. Tesseract with `hin` runs in the container (`backend/Dockerfile` installs `tesseract-ocr-hin`) — confirm the Devanagari came out, not mojibake | The source PDF, the parsed text, the document row's `status` and `pages` | manual-pending |
| Content: transliterated organisation names dedupe | `test_content_transliterated_organisation_names_dedupe_a_cppp_mirror` | Backend | "Govt. of Tamil Nadu" (CPPP mirror) and "Government of Tamil Nadu" (state portal) reach one `buyer_norm`, the richer row survives, and the CPPP link stays in `extra.also_from` | CI run link | automated-green |
| Content: live CPPP/state mirrors actually merge | manual | Backend / QA | After 48 h of live staging-in ingestion, list opportunities with `duplicate_of` set and spot-check ten of them; then list Indian buyers by `buyer_norm` and look for two spellings of one body that did **not** merge | The two queries and their output; any miss becomes a new entry in the transliteration table | manual-pending |
| MSME/Udyam exemption changes eligibility | `test_exemptions_msme_udyam_flips_the_same_criteria_from_fail_to_pass` | Backend | The same profile and the same criteria: `fail` without the exemption, `pass` with `allows_mse_exemption`, EMD waived, and a tender that does not offer the relaxation still fails and says so. Udyam "medium" is not an MSE | CI run link | automated-green |
| Startup (DPIIT) exemption changes eligibility | `test_exemptions_dpiit_startup_flips_the_same_criteria_from_fail_to_pass` | Backend | Same shape for `allows_startup_exemption`; when both statuses are held and both offered, MSE is reported first | CI run link | automated-green |
| Eligibility results read correctly to a pilot | manual | Pilot / Product | On a real GeM bid, compare the app's eligibility panel line by line with the bid document's eligibility clause. Every reason must be a sentence the bidder agrees with | Screenshot of the panel next to the document page | manual-pending |
| WhatsApp templates approved | manual | Ops / Growth | See [WhatsApp](#whatsapp-template-submission-approval-and-a-delivery-check) below | Screenshot of each template at status **Approved**, with its name and language | manual-pending |
| WhatsApp templates delivered | manual | Ops | See [WhatsApp](#whatsapp-template-submission-approval-and-a-delivery-check) below | The BSP message log entry and the `notification_deliveries` row at `sent`/`opened` | manual-pending |
| WhatsApp gating (IN tenant + verified number + approved template) | `test_notifications_whatsapp_is_offered_only_to_a_verified_indian_user`, `test_notifications_whatsapp_sends_only_pre_approved_templates` | Backend | All four gate combinations; and with no template name configured nothing is sent, ever, as free text | CI run link | automated-green |
| SES Mumbai: the region is `ap-south-1` for Indian tenants | `test_notifications_ses_is_mumbai_for_an_indian_tenant` | Backend | `ses_region_for` and `build_email_provider` pick `ap-south-1` for an IN tenant and `us-east-1` otherwise | CI run link | automated-green |
| SES Mumbai deliverability: SPF, DKIM, DMARC | manual | Ops / IT | See [SES Mumbai](#ses-mumbai-domain-verification-and-deliverability) below | `dig` output for all three records, the SES identity page, and the mail-tester score report | manual-pending |
| Razorpay: chosen for IN tenants, GST fields on the payload | `test_payments_razorpay_is_chosen_for_in_tenants_and_carries_the_gst_fields`, `test_payments_a_paid_invoice_carries_a_gst_breakdown_to_inspect` | Backend | `provider_for_region("in")` is Razorpay; the subscription payload carries `notes.gstin`, `notes.place_of_supply`, `notes.legal_name`; an `invoice.paid` event yields CGST+SGST intra-state and IGST inter-state, with the lines adding to the amount charged | CI run link | automated-green |
| Razorpay test payment with a GST invoice | manual | Finance / Ops | See [Razorpay](#razorpay-test-mode-payment-and-the-gst-invoice) below | The checkout page, the test payment id, the webhook delivery, the plan change in the app, and the invoice PDF with every GST field legible | manual-pending |
| Data stays in asia-south1: the code routes IN to the IN bucket | `test_residency_storage_routes_an_indian_tenant_to_the_indian_bucket` | Backend | `bucket_for_region` and `StorageRouter` give an IN tenant the Indian bucket and never the US one | CI run link | automated-green |
| Data stays in asia-south1: staging-in *declares* asia-south1 for DB and buckets | `test_residency_staging_in_declares_asia_south1_for_the_database_and_the_buckets` | Backend / Infra | The env's `region` is `asia-south1`, `residency = "in"`, the bucket, its CMEK key, Cloud SQL, its backup location and Secret Manager replication all follow `var.region`, and the `check "residency_matches_region"` block refuses a mismatched plan | CI run link | automated-green |
| Data stays in asia-south1: verify the *live* bucket and DB locations | manual | Infra | See [Residency](#residency-verifying-the-live-bucket-and-database-locations) below | The three command outputs, unedited | manual-pending |
| Latency from Indian ISPs (Jio, Airtel) p95 page load < 2.5 s | manual | Frontend / QA | See [Latency](#latency-from-indian-isps) below | The run table (p50/p75/p95 per page per network) and the exported report | manual-pending |
| Two pilot users run a real GeM bid and a CPPP tender end-to-end | manual | Pilot users + Product | See [Pilot runs](#the-two-pilot-end-to-end-runs) below | The two evidence packs listed there | manual-pending |

Related acceptance criteria that this run also proves (SPEC 12, "Acceptance criteria for MVP"):

| Acceptance criterion | Automated? | Owner role | Steps | Evidence | Status |
| --- | --- | --- | --- | --- | --- |
| A new CPPP or GeM tender matching an India test profile appears within 6 hours | manual | Backend / QA | Note the posting time of a fresh tender on the portal, then the `created_at` of its row and the notification timestamp. Repeat for three tenders across CPPP, GeM and one GePNIC state | The three (portal time, ingest time, notify time) triples | manual-pending |
| Clicking Pursue produces a compliance matrix + full draft + scorecard within 30 minutes, with page citations | manual | Pilot / Product | Part of the pilot runs below; time it from the click | Agent run id, wall-clock time, the matrix with citations | manual-pending |
| Deadline reminders fire on the ladder in the user's time zone; calendar events created | manual | QA | Part of the pilot runs below; check the 7d/3d/24h/4h/1h rungs land at IST-correct times and the `.ics` opens in the pilot's calendar | Reminder emails with headers, the calendar entry | manual-pending |
| DOCX/PDF/XLSX/ZIP exports open cleanly | manual | QA | Part of the pilot runs below; open each in Word, Acrobat and Excel (not just a viewer) | The four files | manual-pending |

## Manual procedures

### WhatsApp: template submission, approval and a delivery check

WhatsApp Business only permits business-initiated messages that use a template the BSP
and Meta have approved. BidRadar sends on exactly two events — `deadline_reminder` and
`high_fit_match` (SPEC 7) — and the template **names** are configuration
(`WHATSAPP_TEMPLATE_DEADLINE`, `WHATSAPP_TEMPLATE_HIGH_MATCH`), never code. An event with
no configured name is skipped, so a missing approval degrades to "no WhatsApp", not to a
broken send.

1. **Draft the two templates** in the BSP console (Gupshup: *Templates → Create*;
   Twilio: *Content Template Builder*). Category **Utility** (not Marketing — a
   deadline reminder is transactional, and Utility has the better delivery window).
   Language `en` unless `WHATSAPP_TEMPLATE_LANGUAGE` says otherwise. The variables are
   positional, filled by `app/notify/whatsapp.py::template_variables`, in this order:

   | Template | `{{1}}` | `{{2}}` | `{{3}}` | `{{4}}` |
   | --- | --- | --- | --- | --- |
   | deadline reminder | opportunity title | deadline, rendered dual-zone | time remaining | deep link |
   | high-fit match | opportunity title | buyer | score | deep link |

   Keep each variable under 200 characters (the channel clips them) and put no newline
   in one. Suggested bodies:

   > *Deadline reminder:* `BidRadar: "{{1}}" is due {{2}} ({{3}} left). Open: {{4}}`
   > *High-fit match:* `BidRadar: new high-fit tender "{{1}}" from {{2}}, score {{3}}. Open: {{4}}`

2. **Submit for approval.** Turnaround is usually minutes, occasionally a day. A
   rejection is nearly always category or a promotional-sounding body — reword and
   resubmit rather than arguing.
3. **Record the approved names** into the staging-in Secret Manager / env
   (`WHATSAPP_PROVIDER`, `WHATSAPP_TEMPLATE_DEADLINE`, `WHATSAPP_TEMPLATE_HIGH_MATCH`,
   the BSP credentials, `WHATSAPP_WEBHOOK_SECRET`) and redeploy.
4. **Verify the recipient.** The pilot user's `users.phone_e164` must be set **and**
   `phone_verified_at` non-null; the tenant's region must be `in`. All three gates are
   enforced in code — if the message is skipped, the delivery row's reason says which
   gate failed.
5. **Trigger a real send.** Easiest honest trigger: move a pursuit's key date so the 24 h
   rung is due, then run the reminder pass. Do not fabricate a notification row.
6. **Check delivery both ways.** In the BSP console the message must reach *delivered*
   (and *read* once the pilot opens it). In BidRadar, the BSP posts a receipt to
   `POST /api/v1/webhooks/whatsapp/{provider}` and the matching
   `notification_deliveries` row must move to `sent` and then `opened`, keyed on
   `provider_ref`. A receipt that never arrives means the webhook URL or the HMAC secret
   is wrong — check for 4xx in the BSP's webhook log before blaming the app.

Evidence: the approved-template screenshots, the BSP log line, and the
`notification_deliveries` row (id, status, `provider_ref`, timestamps).

### SES Mumbai: domain verification and deliverability

Indian tenants are served from SES **ap-south-1** so the message never leaves India
(`SES_REGION_IN`, asserted by the automated row above). Deliverability is DNS work:

1. **Create the identity** in SES ap-south-1 for the sending domain (the region matters:
   an identity verified in us-east-1 does nothing for Mumbai). Enable **Easy DKIM**
   (RSA 2048) and copy the three CNAMEs.
2. **Publish the DNS records** on the sending domain:

   | Record | Name | Value |
   | --- | --- | --- |
   | DKIM ×3 | `<token>._domainkey` | `<token>.dkim.amazonses.com` |
   | SPF | the domain (or the MAIL FROM subdomain) | `v=spf1 include:amazonses.com ~all` |
   | DMARC | `_dmarc` | `v=DMARC1; p=quarantine; rua=mailto:dmarc@<domain>; adkim=s; aspf=s` |

   Set a **custom MAIL FROM** subdomain (e.g. `mail.<domain>`) so SPF aligns; without it
   DMARC passes on DKIM alone, which is fragile.
3. **Wait for `Verified`** on the identity and `Success` on DKIM, then confirm from the
   outside — trusting the console alone has burned people:

   ```bash
   dig +short TXT <domain> | grep spf1
   dig +short TXT _dmarc.<domain>
   dig +short CNAME <token>._domainkey.<domain>
   aws ses get-identity-verification-attributes --region ap-south-1 --identities <domain>
   ```

4. **Leave the sandbox** for staging-in (a support request), or every pilot address has
   to be individually verified.
5. **Score the mail.** Send one real BidRadar notification (a digest is the worst case:
   most HTML, most links) to a fresh https://www.mail-tester.com address from staging-in.
   Target **10/10**; anything below 8 is a fail for this row. Then send to a Gmail, an
   Outlook and one Indian corporate domain and confirm it lands in **Inbox**, not
   Promotions or Spam. In Gmail, *Show original* must read `SPF: PASS`, `DKIM: PASS`,
   `DMARC: PASS`.
6. **Check the reputation dashboard** after the run: bounce rate < 5 %, complaint
   rate < 0.1 %.

Evidence: the `dig` output, the SES identity screenshot, the mail-tester report URL and
score, and the Gmail *Show original* header block.

### Razorpay: test-mode payment and the GST invoice

Stay in **test mode** throughout (keys `rzp_test_…`). Nothing here needs a real card.

1. **Configure staging-in**: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`,
   `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_PLAN_IDS` (`{"pro": "plan_…"}` — a plan with no
   configured id makes checkout 502 rather than guessing), `BILLING_GSTIN` (our supplier
   GSTIN) and `BILLING_GST_RATE_PCT` (18).
2. **Register the webhook** in the Razorpay dashboard at
   `https://<staging-in-api>/api/v1/webhooks/razorpay`, subscribing at least to
   `subscription.activated`, `subscription.charged`, `invoice.paid` and
   `subscription.cancelled`. Use the same secret as `RAZORPAY_WEBHOOK_SECRET` — the
   endpoint authenticates the HMAC over the raw body and writes nothing before it
   verifies.
3. **Check out as a pilot tenant.** Settings → Billing → Upgrade, filling GSTIN, place of
   supply and registered name. The app must show provider `razorpay` and currency `INR`,
   and redirect to a `rzp.io` hosted page. Checkout alone must **not** change the plan.
4. **Pay** with a Razorpay test card (`4111 1111 1111 1111`, any future expiry, any CVV,
   OTP `1234` on the test 3-D Secure page).
5. **Confirm the webhook did the work**, not the redirect: the dashboard's webhook log
   shows a 200; `billing_events` has one row for the event id (a redelivery must not
   create a second); `tenants.plan` is now `pro`; the app shows the new plan and limits.
6. **Inspect the GST invoice** in the Razorpay dashboard (*Invoices*) and the stored
   breakdown on the `invoice.paid` event. Both must show:
   - supplier GSTIN (ours) and recipient GSTIN (the tenant's);
   - place of supply, and the right split — **CGST + SGST** when the place of supply is
     our own state, **IGST** when it is not;
   - taxable value, tax rate (18 %), HSN/SAC code, invoice number, and a total where
     taxable + tax equals the amount actually charged;
   - currency INR.

   A missing or wrong HSN/SAC, or a supplier GSTIN we have not registered, is a finance
   blocker, not a bug to file later — see OQ-59.
7. **Cancel the test subscription** so the sandbox does not keep charging.

Evidence: checkout screenshot, payment id, webhook log entry, the `billing_events` row,
the plan change, and the invoice PDF.

### Residency: verifying the live bucket and database locations

The Terraform declaration is asserted automatically; this is the live check, and it is
the one that matters for DPDP.

```bash
cd infra/terraform/envs/staging-in
terraform output residency        # bucket_location, cmek_location, database_region, backup_location
terraform output residency_ok     # must be true
terraform output backups          # PITR days, retained backups, backup location

gcloud storage buckets describe gs://bidradar-staging-in-bidradar-staging-in \
  --project bidradar-staging-in --format='value(location,locationType,encryption.defaultKmsKeyName)'

gcloud sql instances describe bidradar-stg-in-pg \
  --project bidradar-staging-in \
  --format='value(region,gceZone,settings.backupConfiguration.location,settings.ipConfiguration.ipv4Enabled)'

gcloud secrets list --project bidradar-staging-in \
  --format='table(name,replication.userManaged.replicas[0].location)'
```

Every location must read `asia-south1` (the KMS key too — a bucket in Mumbai encrypted
with a US key is still a transfer). `ipv4Enabled` must be `False`: the database has no
public IP. Then confirm nothing writes elsewhere at runtime: upload a document as a pilot
tenant and check the object landed in the Indian bucket
(`gcloud storage ls gs://…/documents/ --recursive | tail`).

Evidence: the unedited output of all five commands.

### Latency from Indian ISPs

Target: **p95 page load < 2.5 s** on a consumer Indian connection.

Measure the four pages a pilot actually waits on: the opportunity list, one opportunity
detail, the pursuit board, and one packet page.

Two acceptable methods — do at least one of each city:

- **WebPageTest** (https://www.webpagetest.org) from **Mumbai** and **Delhi**, Chrome,
  "4G" and "Cable" profiles, **9 runs** first-view. Record the p95 of *Largest
  Contentful Paint* and of *Time to Interactive*; the SPEC number is page load, so also
  record `loadTime`.
- **Lighthouse** from a real Jio or Airtel connection (a phone tethered, or a laptop on a
  Jio Fiber/Airtel Xstream line in India), `--preset=desktop` and mobile, 5 runs each,
  logged in as a pilot user (so the pages have real data — an empty list is not a test).

Record, per page × per network × per city: p50, p75, p95, the run count, the date, the
release tag, and the largest single contributor from the waterfall. Note whether the
frontend was served warm (Cloud Run `min_instances = 1` for staging-in, so a cold start
should be rare — if you catch one, say so rather than discarding the run).

A fail here is usually one of: the API round-tripping from Mumbai to a US dependency, an
unbounded list query, or an uncached font. Attach the waterfall for the worst page.

Evidence: the run table and the exported WebPageTest/Lighthouse JSON or report links.

### The two pilot end-to-end runs

Two pilot users, one **GeM bid** and one **CPPP tender**, each carried from alert to a
reviewed package. **A human submits on the portal** — BidRadar never logs into a portal,
never solves a CAPTCHA and never submits (CLAUDE.md, SPEC 14). The run ends at a package
the bidder is willing to upload themselves.

Click path (identical for both; the differences are in the portal, not the app):

1. **Alert.** The pilot receives the match — email, in-app, and WhatsApp if the earlier
   rows passed. Note the timestamp against the portal's posting time.
2. **Opportunity.** Open it from the alert's deep link. Check: title (bilingual intact),
   buyer, deadline in IST, EMD and tender fee, the source attribution and the link back
   to the official portal, and the "verify every detail on the official portal" notice.
3. **Eligibility.** Read the eligibility panel and confirm MSE/startup exemptions are
   applied or correctly not offered.
4. **Pursue.** Click **Pursue**. Time the agent run: compliance matrix, draft and bid/no-bid
   scorecard within 30 minutes, every requirement citing a page.
5. **Compliance matrix.** Walk every row against the bid document. For the GeM bid, the
   checklist must include the GeM seller id and DSC rows; for CPPP, the CPPP enrolment,
   DSC-signed covers, EMD/BG and tender-fee rows.
6. **Dates.** Check the auto-created key dates (questions due, pre-bid meeting, internal
   draft/review/final, EMD/BG ready, DSC check, portal submission) are IST-correct, and
   that the calendar `.ics` opens in the pilot's calendar.
7. **Draft and review.** Fill every `[NEEDS INPUT: …]` placeholder, run the red-team
   review, and mark the draft final so the internal footer drops.
8. **Export.** Produce DOCX, PDF, XLSX and the ZIP packet; open each in Word, Acrobat and
   Excel.
9. **Submit on the portal — by hand.** The pilot uploads the covers, signs with their own
   Class 3 DSC on their own token, pays EMD and tender fee, and submits. Record the
   portal's acknowledgement number.
10. **Close the loop.** Move the pursuit to `submitted` in BidRadar and confirm the
    dashboard counts it.

Artifacts to keep per run: the alert (email or screenshot), the opportunity page
screenshot, the compliance matrix export, the final draft, all four export files, the
packet page screenshot, the portal acknowledgement (number only — no credentials), and a
short note of every place the pilot had to correct the app.

## Sign-off

One table per staging-in run. A run passes only when every row above is green and both
pilot runs reached a submitted bid.

| Date | Release tag | Run by | Automated rows | Manual rows passed | Failures / follow-ups |
| --- | --- | --- | --- | --- | --- |
| _(not yet run)_ | | | `pytest tests/integration/test_india_checklist.py` | | staging-in has never been deployed |

## What this page deliberately does not cover

- **Load** (50k × 200 in under 10 minutes, search p95 < 500 ms) — that is SPEC 12's load
  row and has its own harness.
- **Tenant isolation, agent evals and the Playwright UI flows** — they gate every merge
  in CI rather than an India run.
- **Portal automation of any kind.** No login, no CAPTCHA, no auto-submission, in testing
  as in production.

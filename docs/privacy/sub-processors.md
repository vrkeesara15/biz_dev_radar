# Sub-processors

BidRadar uses the third parties below to deliver the service (SPEC section 11). This page
is the public list referenced by the privacy policy and by the DPDP consent notice, and it
is served as JSON at `GET /api/v1/privacy` so the product and this file can never drift
apart (`tests/unit/test_privacy_core.py` fails if they do).

A tenant's data stays in the residency region it chose at signup (`us` or `in`). A
sub-processor marked as optional only receives data when the tenant enables that channel.

| Sub-processor | Purpose | Data shared | Location | Notes |
| --- | --- | --- | --- | --- |
| Anthropic | LLM inference for summaries, drafting and analysis agents | Solicitation text, company profile excerpts, prompts and completions | United States | Contracted under zero-data-retention / no-training terms on the organisation's Claude API account (SPEC 11). |
| Voyage AI | Text embeddings for matching and knowledge-base retrieval | Notice text and knowledge-base chunks | United States | — |
| Google Cloud Platform | Application hosting, Postgres, object storage, logging and tracing | All tenant data, stored in the tenant's residency region | United States (us) and India, asia-south1 Mumbai (in) | Regional buckets and databases; CMEK at rest. |
| Amazon Web Services (SES) | Transactional and digest email delivery | Recipient email address, name and message content | United States and India (ap-south-1) | — |
| SendGrid (Twilio) | Fallback email delivery | Recipient email address, name and message content | United States | — |
| Slack | Alert and approval notifications into a tenant's workspace | Notice titles, scores and links; the notifying user's name | United States | Only for tenants that install the Slack app. |
| Twilio / Gupshup | WhatsApp Business API delivery | Recipient phone number and message content | United States (Twilio) and India (Gupshup) | Only for tenants that enable WhatsApp alerts. |
| Stripe | Subscription billing and invoicing for US tenants | Billing contact, email and payment method (held by Stripe, never by us) | United States | — |
| Razorpay | Subscription billing and GST invoicing for Indian tenants | Billing contact, email, GSTIN and payment method (held by Razorpay) | India | — |
| Sentry | Error monitoring | Stack traces and request metadata with PII scrubbed before sending | United States | — |
| Langfuse | LLM trace and cost observability | Agent prompts, completions, token counts and cost | European Union | — |

## Changes

We give notice before adding a sub-processor that receives personal data. The canonical
list lives in `backend/app/core/privacy.py` (`SUB_PROCESSORS`); edit it there and
regenerate this table, never the other way round.

## Contact

Questions about this list, or about a data-principal request, go to the grievance officer
published at `GET /api/v1/privacy` (`GRIEVANCE_OFFICER_NAME` / `GRIEVANCE_OFFICER_EMAIL`).

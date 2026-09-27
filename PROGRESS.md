# PROGRESS.md — append-only build log

Format per entry: task id · what changed · how verified.

## Open questions

- OQ-1 (§14) Product name/domain: keeping the placeholder "BidRadar" everywhere; rename is a find/replace later.
- OQ-2 (§14) Internal tenant profile seed: seeding a placeholder internal tenant with empty profile; the owner fills UEI/NAICS/past performance via the wizard.
- OQ-3 (§14) SAM.gov key type: code assumes a single non-federal personal key per environment (`SAM_API_KEY`), quota-aware limiter defaults to 10 req/day headroom; a system account is a config change only.
- OQ-4 (§14) Indian state portals: Telangana, Karnataka, Maharashtra as proposed; IREPS/defproc shipped as documented stubs.
- OQ-5 (§14) SLED: no HigherGov licence assumed; SLED ships as a documented adapter stub with the paid-feed adapter interface.
- OQ-6 (§14) Hosting: GCP Cloud Run as specified (Terraform); Railway not implemented.
- OQ-7 (§14) Embeddings: Voyage `voyage-3` (1024-dim) default behind `EmbeddingProvider`; no in-region LLM endpoint for India (config flag reserved).
- OQ-8 (§13.4) The spec says the first loop iteration should stop after writing tasks.json for human review. The operator asked for autonomous completion, so the loop continues into M0 without waiting; tasks.json remains open for review and edits at any time.
- OQ-9 (§6 stage 3) The spec does not name a model class for the match rationale. Using Sonnet-class (`LLM_MODEL_RATIONALE`) as the cost-safe default; changeable in config.
- OQ-10 Local dev machine has no ClamAV or Tesseract binaries; both are behind pluggable interfaces with a no-op/fake in tests and real implementations in the Docker image.

## Log

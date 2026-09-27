# Golden GeM bid documents (SPEC 12 agent evals)

Synthetic bid PDFs in the layout of a real GeM bid document (bidplus.gem.gov.in refused connections from the build host, PROGRESS.md OQ-14) with hand-labelled expected values (`*.expected.json`) and the recorded extractor answers replayed by FakeLLM (`*.llm.json`). Regenerate the PDFs with `uv run python backend/tests/adapters/fixtures/gem/make_fixtures.py`; replace them with real bid PDFs (and re-label) as soon as a live capture is possible.

Pass bar (SPEC 12): exact match >= 90% on turnover, EMD and experience.

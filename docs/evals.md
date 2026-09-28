# Agent evals (SPEC 12)

`make eval` runs the eval suite and prints the pass-bar table in its summary. It never
touches the network.

```
make eval          # backend/tests/evals: the golden set, the injection suite and the
                   # grounding check. The pass-bar table is printed in the summary.

cd backend && uv run python ../evals/run.py   # the same measurements, standalone;
                                              # exits non-zero below any bar
```

## The pass bars

| metric | measured over | bar |
| --- | --- | --- |
| requirement extraction recall | 20 golden solicitations | ≥ 90% |
| requirement extraction precision | the same | ≥ 85% |
| page citations | every stored requirement carries a document + page | 100% |
| page accuracy | a matched requirement cites the labelled page | 100% |
| eligibility exact match | US: NAICS, set-aside, SAM. India: turnover, EMD, experience | ≥ 90% |
| fabricated company facts | after the red team's single auto-revision | 0 |

Each item must clear the recall, precision and citation bars **on its own**, and the set
must clear them in aggregate — a strong item cannot carry a weak one.

## What is in the golden set

`evals/golden/<region>/<item>/` holds `notice.pdf`, `labels.json` (hand-labelled
requirements with page and type, plus the eligibility fields) and `recorded.json` (the
recorded extractor answer replayed through `FakeLLM`).

- **US (10)**: an IRS sources-sought notice, a DoD RFP with Sections L and M, a GSA
  schedule RFQ, a VA sources-sought, an EPA combined synopsis/solicitation, an NSF grant
  NOFO, a city (SLED) RFP, an IDIQ task order, one rendered the way an OCR pass reads a
  scan, and one carrying a prompt-injection line in its own text.
- **India (10)**: five GeM bids, three CPPP (eProcure) tenders and two state GePNIC
  tenders, in the Hindi/English mix those portals publish. Every Indian item labels the
  three numbers SPEC 12 names: average annual turnover, EMD and years of experience.
- `evals/golden/drafting/` holds the recorded drafting fixture the fabricated-fact count
  uses: first drafts that carry unsupported claims and the red team's recorded revision.
- `evals/golden/in/gem/` is M3-04's separate GeM eligibility golden set (bid PDFs with
  `*.expected.json` labels), scored by `tests/evals/test_gem_extraction_golden.py`.

Every notice is **synthetic** — written for this eval in the shape of the real thing, not
a copy of any published notice.

### Regenerating

```
cd backend && uv run python ../evals/golden/make_golden.py
cd backend && uv run python ../evals/golden/us/irs_sources_sought/make_notice.py
```

The recorded answers are generated from the labels but are deliberately not copies of
them: each requirement is restated in the extractor's own words, one label per item is
missed, a real but non-binding sentence is over-extracted, one obligation is repeated and
two items are emitted that the citation validator must reject. A replay would make the
bars a tautology; `tests/evals/test_golden.py` asserts that recall and precision stay
strictly below 100%.

## Prompt injection

`evals/injection/` holds one JSON case per place an attacker controls text that reaches a
model: a solicitation page, a parsed attachment chunk, a GeM ATC, an RFI question, a
notice title and an award-history description. `tests/evals/test_injection.py` asserts,
for every implemented agent's real prompt builder, that the safety preamble is in the
system prompt and the attacker's text appears only inside an `<untrusted>` block; and,
for the agents that run without a database, that a model which *obeys* the injection
still produces the clean baseline output. See `evals/injection/README.md`.

## Running against the live model

The default is recorded answers, so `make eval` is safe on a laptop and in CI without
keys. To run the extractor against the real model instead:

```
cd backend
export BIDRADAR_LIVE_EVAL=1
export ANTHROPIC_API_KEY=...          # never committed; read from the environment
uv run python ../evals/run.py
uv run pytest tests/evals -p no:cacheprovider
```

The bars are the same either way, and the table's last line says which mode ran. A live
run costs real tokens against the Opus-class model for every item, so it is a deliberate
act, not part of `make eval`.

"""SPEC 6 stage-2 signals that need the database (M4-03).

    vector = await ensure_opportunity_embedding(session, opportunity, embeddings=provider)
    signals = await compute_signals(session, profile, opportunity, embeddings=provider)
    outcome = evaluate(profile, match_opportunity_from_row(opportunity), now,
                       **signals.as_kwargs())

Three signals live here because they need SQL; the other five are pure (core.matching).

* semantic similarity = the best cosine between `opportunities.embedding` and the
  profile's SERVICE_LINE `kb_chunks` (pgvector: `1 - (embedding <=> :vec)`);
* past-performance relevance = the same against PAST_PERFORMANCE chunks;
* keyword match = Postgres `ts_rank_cd` of each weighted include keyword against one
  tsvector built from the notice's title (weight A), summary + description (B) and its
  parsed document chunks (C), combined as a weight-average of the per-term ranks.

Normalising ts_rank_cd (OQ-91): the raw cover-density rank is unbounded (it grows with
the number of occurrences), so each per-term rank uses Postgres's own normalisation flag
32 -- `rank / (rank + 1)` -- rather than dividing by the rank of the profile's keyword
string against itself. Flag 32 is scale-free and needs no reference document, and the
field weights give the curve a readable calibration: one occurrence in the title scores
0.5, one in the description 0.29, one inside a parsed document 0.17, and a term repeated
throughout the notice approaches 1.

`session` is the TENANT's session: RLS scopes `kb_chunks` to the profile's tenant, while
`opportunities` and `document_chunks` are global (public notices).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

import structlog
from sqlalchemy import bindparam, func, literal, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Text

from app.core.matching.score import SignalValue
from app.core.matching.types import KeywordWeight, MatchProfile
from app.models import KBChunk, Opportunity
from app.services.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    embeddings_available,
    get_embeddings,
)
from app.services.knowledge_base import SourceType
from app.services.opportunity_embeddings import (
    embed_opportunity,
    first_document_chunk,
    opportunity_text,
)

log = structlog.get_logger(__name__)

# Text of the parsed solicitation documents that feeds the keyword tsvector. A tsvector
# is limited to 1 MB, and the first ~120k characters of a solicitation already carry the
# scope; the rest is terms and conditions.
KEYWORD_DOC_CHARS = 120_000
# ts_rank_cd normalisation flag: 32 = rank / (rank + 1) (see the module docstring).
TS_RANK_NORMALISATION = 32
FTS_CONFIG = "english"

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HALF = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class PrecomputedSignals:
    """The three DB-backed signals, ready for core.matching.engine.evaluate."""

    semantic: SignalValue | None = None
    keyword: SignalValue | None = None
    past_performance: SignalValue | None = None

    def as_kwargs(self) -> dict[str, SignalValue | None]:
        return {
            "semantic": self.semantic,
            "keyword": self.keyword,
            "past_performance": self.past_performance,
        }


# --- opportunity embedding ---------------------------------------------------------------


async def ensure_opportunity_embedding(
    session: AsyncSession,
    opportunity: Opportunity,
    *,
    embeddings: EmbeddingProvider | None = None,
) -> list[float] | None:
    """The notice's vector, embedding it on demand when the column is still NULL (a notice
    ingested before M1-12, or while the provider was unavailable). None when it cannot be
    computed here -- the caller reports the signal as unknown rather than failing."""
    stored = opportunity.embedding
    if stored is not None:
        return [float(v) for v in stored]
    provider = embeddings or get_embeddings()
    if not embeddings_available(provider):
        log.info("matching.embedding_unavailable", opportunity_id=str(opportunity.id))
        return None
    if not await embed_opportunity(session, opportunity, embeddings=provider):
        return None
    vector = opportunity.embedding
    return None if vector is None else [float(v) for v in vector]


# --- cosine signals ------------------------------------------------------------------------


def _clamp(value: float) -> Decimal:
    """Cosine similarity is -1..1; a signal is 0..1, so a negative similarity is 0."""
    number = Decimal(str(round(value, 6)))
    return max(_ZERO, min(_ONE, number))


async def best_cosine(
    session: AsyncSession,
    profile_id: uuid.UUID,
    vector: list[float],
    source_type: SourceType,
) -> tuple[Decimal, KBChunk] | None:
    """The closest chunk of that source type and its cosine similarity (RLS-scoped)."""
    distance = KBChunk.embedding.cosine_distance(vector).label("distance")
    row = (
        await session.execute(
            select(KBChunk, distance)
            .where(
                KBChunk.profile_id == profile_id,
                KBChunk.source_type == source_type.value,
            )
            .order_by(distance, KBChunk.source_id, KBChunk.chunk_index)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    chunk: KBChunk = row[0]
    return _clamp(1.0 - float(row[1])), chunk


async def _cosine_signal(
    session: AsyncSession,
    profile_id: uuid.UUID,
    vector: list[float] | None,
    *,
    source_type: SourceType,
    note: str,
    empty_note: str,
) -> SignalValue:
    if vector is None:
        return SignalValue.unknown("no opportunity embedding")
    best = await best_cosine(session, profile_id, vector, source_type)
    if best is None:
        return SignalValue.unknown(empty_note)
    score, chunk = best
    return SignalValue.of(
        score,
        note,
        source=source_type.value,
        source_id=str(chunk.source_id),
        chunk_index=chunk.chunk_index,
    )


async def semantic_signal(
    session: AsyncSession, profile_id: uuid.UUID, vector: list[float] | None
) -> SignalValue:
    """SPEC 6: max cosine of the notice embedding vs the profile's service lines."""
    return await _cosine_signal(
        session,
        profile_id,
        vector,
        source_type=SourceType.SERVICE_LINE,
        note="max cosine vs service lines",
        empty_note="no service lines indexed",
    )


async def past_performance_signal(
    session: AsyncSession, profile_id: uuid.UUID, vector: list[float] | None
) -> SignalValue:
    """SPEC 6: best cosine of the notice embedding vs past-performance records."""
    return await _cosine_signal(
        session,
        profile_id,
        vector,
        source_type=SourceType.PAST_PERFORMANCE,
        note="best cosine vs past performance",
        empty_note="no past performance indexed",
    )


# --- keyword signal --------------------------------------------------------------------------

# One tsvector per notice (title A, summary + description B, parsed documents C) ranked
# against every include keyword, for a whole batch of notices in ONE round trip
# (`score_batch` scores a profile against thousands of notices). `left(...)` caps the
# document text so the 1 MB tsvector limit can never be hit.
_KEYWORD_SQL = text(
    f"""
WITH docs AS (
    SELECT od.opportunity_id AS oid,
           left(string_agg(dc.text, ' ' ORDER BY od.created_at, od.id, dc.chunk_index),
                {KEYWORD_DOC_CHARS}) AS body
    FROM document_chunks dc
    JOIN opportunity_documents od ON od.id = dc.document_id
    WHERE od.opportunity_id = ANY(:ids)
    GROUP BY od.opportunity_id
), vec AS (
    SELECT o.id AS oid,
           setweight(to_tsvector('{FTS_CONFIG}'::regconfig, coalesce(o.title, '')), 'A')
        || setweight(
               to_tsvector(
                   '{FTS_CONFIG}'::regconfig,
                   coalesce(o.summary_ai, '') || ' ' || coalesce(o.description_text, '')
               ),
               'B'
           )
        || setweight(
               to_tsvector('{FTS_CONFIG}'::regconfig, coalesce(docs.body, '')), 'C'
           ) AS v
    FROM opportunities o
    LEFT JOIN docs ON docs.oid = o.id
    WHERE o.id = ANY(:ids)
)
SELECT vec.oid,
       t.ord,
       ts_rank_cd(vec.v, plainto_tsquery('{FTS_CONFIG}'::regconfig, t.term),
                  {TS_RANK_NORMALISATION}) AS rank
FROM vec, unnest(:terms) WITH ORDINALITY AS t(term, ord)
"""
).bindparams(
    bindparam("terms", type_=ARRAY(Text())),
    bindparam("ids", type_=ARRAY(PGUUID(as_uuid=True))),
)


def live_terms(keywords: Sequence[KeywordWeight]) -> list[KeywordWeight]:
    return [k for k in keywords if k.term and k.term.strip()]


async def keyword_ranks(
    session: AsyncSession,
    opportunity_ids: Sequence[uuid.UUID],
    terms: Sequence[str],
) -> dict[uuid.UUID, dict[int, float]]:
    """{opportunity id: {1-based term index: normalised ts_rank_cd}} in one query."""
    if not opportunity_ids or not terms:
        return {}
    rows = (
        await session.execute(
            _KEYWORD_SQL, {"ids": list(opportunity_ids), "terms": [t.strip() for t in terms]}
        )
    ).all()
    out: dict[uuid.UUID, dict[int, float]] = {}
    for row in rows:
        oid = cast(uuid.UUID, row[0])
        out.setdefault(oid, {})[int(str(row[1]))] = (
            float(str(row[2])) if row[2] is not None else 0.0
        )
    return out


def build_keyword_signal(
    keywords: Sequence[KeywordWeight], ranks: dict[int, float] | None
) -> SignalValue:
    """Weighted include keywords over title + description + parsed docs, 0..1.

    Each term's normalised ts_rank_cd is combined as a weighted average using the
    profile's own keyword weights (`profile_keywords.weight`). A profile with no include
    keywords has nothing to match on, so the signal is unknown (0.5) rather than 0: the
    owner never expressed a preference (OQ-90).
    """
    live = live_terms(keywords)
    if not live:
        return SignalValue.unknown("no include keywords")
    if ranks is None:  # the notice disappeared between the load and the score
        return SignalValue.unknown("notice not found")
    detail: list[dict[str, Any]] = []
    total_weight = _ZERO
    total = _ZERO
    for index, keyword in enumerate(live, start=1):
        score = _clamp(ranks.get(index, 0.0))
        weight = max(_ZERO, Decimal(str(keyword.weight)))
        total_weight += weight
        total += score * weight
        detail.append({"term": keyword.term, "weight": float(weight), "score": float(score)})
    detail.sort(key=lambda entry: (-entry["score"], entry["term"]))
    if total_weight == _ZERO:  # every keyword carries weight 0: nothing to say
        return SignalValue.unknown("include keywords all weigh 0")
    raw = _clamp(float(total / total_weight))
    plural = "" if len(live) == 1 else "s"
    return SignalValue.of(raw, f"{len(live)} include keyword{plural}", terms=detail)


async def keyword_signal(
    session: AsyncSession,
    opportunity_id: uuid.UUID,
    keywords: tuple[KeywordWeight, ...],
) -> SignalValue:
    live = live_terms(keywords)
    if not live:
        return SignalValue.unknown("no include keywords")
    ranks = await keyword_ranks(session, [opportunity_id], [k.term for k in live])
    return build_keyword_signal(keywords, ranks.get(opportunity_id))


# --- batch (one query per signal for a whole page of notices) -------------------------------


async def batch_cosine(
    session: AsyncSession,
    profile_id: uuid.UUID,
    opportunity_ids: Sequence[uuid.UUID],
    source_type: SourceType,
) -> dict[uuid.UUID, Decimal]:
    """Best cosine per notice against the profile's chunks of that source type, in ONE
    query (`MIN(kb.embedding <=> o.embedding)` grouped by notice)."""
    if not opportunity_ids:
        return {}
    nearest = func.min(KBChunk.embedding.cosine_distance(Opportunity.embedding))
    rows = (
        await session.execute(
            select(Opportunity.id, nearest)
            .select_from(Opportunity)
            .join(KBChunk, literal(True))
            .where(
                Opportunity.id.in_(list(opportunity_ids)),
                Opportunity.embedding.is_not(None),
                KBChunk.profile_id == profile_id,
                KBChunk.source_type == source_type.value,
            )
            .group_by(Opportunity.id)
        )
    ).all()
    return {cast(uuid.UUID, row[0]): _clamp(1.0 - float(str(row[1]))) for row in rows}


async def ensure_embeddings(
    session: AsyncSession,
    rows: Sequence[Opportunity],
    *,
    embeddings: EmbeddingProvider | None = None,
) -> int:
    """Embed every notice in the batch whose vector is still NULL, in one provider call.
    Returns how many were embedded (0 when the provider is unavailable)."""
    missing = [row for row in rows if row.embedding is None]
    if not missing:
        return 0
    provider = embeddings or get_embeddings()
    if not embeddings_available(provider):
        log.info("matching.embedding_unavailable", count=len(missing))
        return 0
    texts = []
    for row in missing:
        first_chunk = None
        if not (row.description_text or "").strip():
            first_chunk = await first_document_chunk(session, row.id)
        texts.append(opportunity_text(row, first_chunk))
    try:
        vectors = await provider.embed(texts, input_type="document")
    except EmbeddingError as exc:
        log.warning("matching.batch_embed_failed", count=len(missing), error=str(exc))
        return 0
    for row, vector in zip(missing, vectors, strict=True):
        row.embedding = vector
    await session.flush()
    return len(missing)


async def batch_signals(
    session: AsyncSession,
    profile: MatchProfile,
    rows: Sequence[Opportunity],
    *,
    embeddings: EmbeddingProvider | None = None,
) -> dict[uuid.UUID, PrecomputedSignals]:
    """The three DB-backed signals for a whole page of notices: three queries, not 3xN."""
    if not rows:
        return {}
    await ensure_embeddings(session, rows, embeddings=embeddings)
    ids = [row.id for row in rows]
    profile_id = uuid.UUID(profile.id) if profile.id else None
    semantic_by_id: dict[uuid.UUID, Decimal] = {}
    past_by_id: dict[uuid.UUID, Decimal] = {}
    service_lines = past_performance = 0
    if profile_id is not None:
        counts = dict(
            (
                await session.execute(
                    select(KBChunk.source_type, func.count())
                    .where(KBChunk.profile_id == profile_id)
                    .group_by(KBChunk.source_type)
                )
            ).all()
        )
        service_lines = int(counts.get(SourceType.SERVICE_LINE.value, 0))
        past_performance = int(counts.get(SourceType.PAST_PERFORMANCE.value, 0))
        if service_lines:
            semantic_by_id = await batch_cosine(session, profile_id, ids, SourceType.SERVICE_LINE)
        if past_performance:
            past_by_id = await batch_cosine(session, profile_id, ids, SourceType.PAST_PERFORMANCE)
    live = live_terms(profile.include_keywords)
    ranks = await keyword_ranks(session, ids, [k.term for k in live]) if live else {}
    out: dict[uuid.UUID, PrecomputedSignals] = {}
    for row in rows:
        out[row.id] = PrecomputedSignals(
            semantic=_batch_cosine_signal(
                semantic_by_id.get(row.id),
                profile_id is not None and service_lines > 0,
                row.embedding is not None,
                note="max cosine vs service lines",
                empty_note="no service lines indexed",
            ),
            past_performance=_batch_cosine_signal(
                past_by_id.get(row.id),
                profile_id is not None and past_performance > 0,
                row.embedding is not None,
                note="best cosine vs past performance",
                empty_note="no past performance indexed",
            ),
            keyword=build_keyword_signal(profile.include_keywords, ranks.get(row.id, {})),
        )
    return out


def _batch_cosine_signal(
    score: Decimal | None,
    indexed: bool,
    embedded: bool,
    *,
    note: str,
    empty_note: str,
) -> SignalValue:
    if not indexed:
        return SignalValue.unknown(empty_note)
    if not embedded or score is None:
        return SignalValue.unknown("no opportunity embedding")
    return SignalValue.of(score, note)


# --- everything at once -----------------------------------------------------------------------


async def compute_signals(
    session: AsyncSession,
    profile: MatchProfile,
    opportunity: Opportunity,
    *,
    embeddings: EmbeddingProvider | None = None,
    vector: list[float] | None = None,
) -> PrecomputedSignals:
    """The three DB-backed signals for one (profile, notice) pair."""
    profile_id = uuid.UUID(profile.id) if profile.id else None
    if vector is None:
        vector = await ensure_opportunity_embedding(session, opportunity, embeddings=embeddings)
    if profile_id is None:
        semantic = SignalValue.unknown("profile not stored")
        past = SignalValue.unknown("profile not stored")
    else:
        semantic = await semantic_signal(session, profile_id, vector)
        past = await past_performance_signal(session, profile_id, vector)
    keyword = await keyword_signal(session, opportunity.id, profile.include_keywords)
    return PrecomputedSignals(semantic=semantic, keyword=keyword, past_performance=past)

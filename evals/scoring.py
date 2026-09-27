"""Eval scoring (SPEC 12 pass bars). Pure; imported by backend/tests/evals.

    score = score_requirements(predicted_texts, labelled_texts)
    score.recall >= 0.90 and score.precision >= 0.85

Matching is greedy one-to-one on rapidfuzz token_set_ratio >= THRESHOLD (85): a predicted
requirement counts once, a label is matched at most once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

THRESHOLD = 85


@dataclass(slots=True)
class Score:
    matched: list[tuple[int, int, float]] = field(default_factory=list)  # (pred, label, ratio)
    missed_labels: list[int] = field(default_factory=list)
    extra_predictions: list[int] = field(default_factory=list)
    label_count: int = 0
    prediction_count: int = 0

    @property
    def recall(self) -> float:
        return len(self.matched) / self.label_count if self.label_count else 0.0

    @property
    def precision(self) -> float:
        return len(self.matched) / self.prediction_count if self.prediction_count else 0.0

    def summary(self) -> str:
        return (
            f"recall {self.recall:.1%} ({len(self.matched)}/{self.label_count}), "
            f"precision {self.precision:.1%} ({len(self.matched)}/{self.prediction_count}), "
            f"missed {len(self.missed_labels)}, extra {len(self.extra_predictions)}"
        )


def score_requirements(
    predicted: list[str], labelled: list[str], *, threshold: int = THRESHOLD
) -> Score:
    score = Score(label_count=len(labelled), prediction_count=len(predicted))
    pairs: list[tuple[float, int, int]] = []
    for i, pred in enumerate(predicted):
        for j, label in enumerate(labelled):
            ratio = fuzz.token_set_ratio(pred, label)
            if ratio >= threshold:
                pairs.append((ratio, i, j))
    used_pred: set[int] = set()
    used_label: set[int] = set()
    for ratio, i, j in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
        if i in used_pred or j in used_label:
            continue
        used_pred.add(i)
        used_label.add(j)
        score.matched.append((i, j, ratio))
    score.missed_labels = [j for j in range(len(labelled)) if j not in used_label]
    score.extra_predictions = [i for i in range(len(predicted)) if i not in used_pred]
    return score


def page_accuracy(
    predicted: list[tuple[str, int]], labelled: list[tuple[str, int]], score: Score
) -> float:
    """Share of matched pairs whose predicted page equals the labelled page."""
    if not score.matched:
        return 0.0
    hits = sum(1 for i, j, _ in score.matched if predicted[i][1] == labelled[j][1])
    return hits / len(score.matched)

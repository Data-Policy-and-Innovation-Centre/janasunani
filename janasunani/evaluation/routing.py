"""Leakage-safe evaluation for local incidence-based routing.

This evaluates where cases were historically sent.  It must not be described
as outcome optimization: disposal time and citizen benefit are absent by
construction because they are confounded by case difficulty and office
selection.  The model here is a cheap empirical-Bayes backoff over the live
features (category and district), with an optional secondary benchmark for
subcategory when a future classifier supplies it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import tempfile
from typing import Iterable, Mapping, Sequence

from janasunani.evaluation.classification import (
    ScoredExample,
    assert_group_disjoint,
    classification_metrics,
    metrics_by_language,
)


INCIDENCE_ARTIFACT_SCHEMA_VERSION = "routing-incidence-v2"
INCIDENCE_ARTIFACT_FILENAME = "routing_incidence.json"
INCIDENCE_ARTIFACT_MIN_SUPPORT = 3
INCIDENCE_ARTIFACT_MAX_BYTES = 50 * 1024 * 1024
INCIDENCE_SERVING_MIN_SUPPORT = 10
INCIDENCE_SERVING_MIN_CONCENTRATION = 0.50
INCIDENCE_SERVING_MIN_MARGIN = 0.10

_ARTIFACT_TOP_LEVEL_KEYS = {
    "schema_version",
    "kind",
    "objective",
    "outcome_optimized",
    "parameters",
    "departments",
    "counts",
    "privacy",
    "checksum",
}
_COUNT_ENTRY_KEYS = {"department", "count"}
_TABLE_ROW_KEYS = {
    "category": {"category", "counts"},
    "category_district": {"category", "district", "counts"},
    "subcategory": {"category", "subcategory", "counts"},
    "full": {"category", "subcategory", "district", "counts"},
}


@dataclass(frozen=True)
class RouteRecord:
    item_id: str
    group_id: str
    observed_on: date
    category: str
    department: str
    split: str
    district: str | None = None
    subcategory: str | None = None
    language: str = "unknown"
    weight: int = 1


@dataclass(frozen=True)
class IncidencePrediction:
    """One aggregate prediction and the historical cell supporting it."""

    probabilities: dict[str, float]
    support: int
    concentration: float
    width: str


def _norm(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def validate_route_records(records: Sequence[RouteRecord]) -> None:
    if not records:
        raise ValueError("routing benchmark is empty")
    ids = [record.item_id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("item_id values must be unique")
    if {record.split for record in records} != {"train", "validation", "test"}:
        raise ValueError("routing benchmark requires train, validation, and test")
    for record in records:
        if record.split not in {"train", "validation", "test"}:
            raise ValueError(f"invalid split {record.split!r}")
        if not record.item_id or not record.group_id:
            raise ValueError("item_id and group_id must be non-empty")
        if not _norm(record.category) or not _norm(record.department):
            raise ValueError("category and department must be non-empty")
        if not record.language:
            raise ValueError("language must be non-empty")
        if (
            isinstance(record.weight, bool)
            or not isinstance(record.weight, int)
            or record.weight < 1
        ):
            raise ValueError("weight must be a positive integer")
    assert_group_disjoint((record.group_id, record.split) for record in records)

    train_dates = [record.observed_on for record in records if record.split == "train"]
    validation_dates = [
        record.observed_on for record in records if record.split == "validation"
    ]
    test_dates = [record.observed_on for record in records if record.split == "test"]
    if max(train_dates) >= min(validation_dates):
        raise ValueError("training must end before validation begins")
    if max(validation_dates) >= min(test_dates):
        raise ValueError("validation must end before test begins")


class IncidenceRouter:
    """Smoothed hierarchical distributions over historical destinations."""

    def __init__(
        self,
        *,
        alpha: float,
        use_subcategory: bool = False,
        serving_min_support: int = INCIDENCE_SERVING_MIN_SUPPORT,
        serving_min_concentration: float = INCIDENCE_SERVING_MIN_CONCENTRATION,
        serving_min_margin: float = INCIDENCE_SERVING_MIN_MARGIN,
    ) -> None:
        if not math.isfinite(alpha) or alpha <= 0.0:
            raise ValueError("alpha must be positive and finite")
        if (
            isinstance(serving_min_support, bool)
            or not isinstance(serving_min_support, int)
            or serving_min_support < INCIDENCE_ARTIFACT_MIN_SUPPORT
        ):
            raise ValueError(
                "serving_min_support must be an integer at least the privacy floor"
            )
        for value, name in (
            (serving_min_concentration, "serving_min_concentration"),
            (serving_min_margin, "serving_min_margin"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError(f"{name} must be finite and in [0, 1]")
        self.alpha = alpha
        self.use_subcategory = use_subcategory
        self.serving_min_support = serving_min_support
        self.serving_min_concentration = float(serving_min_concentration)
        self.serving_min_margin = float(serving_min_margin)
        self.departments: tuple[str, ...] = ()
        self._global: Counter[str] = Counter()
        self._category: dict[str, Counter[str]] = defaultdict(Counter)
        self._category_district: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self._subcategory: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self._full: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)

    def fit(self, records: Sequence[RouteRecord]) -> "IncidenceRouter":
        if not records:
            raise ValueError("at least one training record is required")
        global_counts: Counter[str] = Counter()
        category_counts: dict[str, Counter[str]] = defaultdict(Counter)
        category_district_counts: dict[tuple[str, str], Counter[str]] = defaultdict(
            Counter
        )
        subcategory_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        full_counts: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
        for record in records:
            category = _norm(record.category)
            subcategory = _norm(record.subcategory)
            district = _norm(record.district)
            department = record.department.strip()
            if not category or not department:
                raise ValueError("category and department must be non-empty")
            if (
                isinstance(record.weight, bool)
                or not isinstance(record.weight, int)
                or record.weight < 1
            ):
                raise ValueError("weight must be a positive integer")
            global_counts[department] += record.weight
            category_counts[category][department] += record.weight
            if district:
                category_district_counts[(category, district)][department] += record.weight
            if subcategory:
                subcategory_counts[(category, subcategory)][department] += record.weight
            if subcategory and district:
                full_counts[(category, subcategory, district)][department] += record.weight
        self._global = global_counts
        self._category = category_counts
        self._category_district = category_district_counts
        self._subcategory = subcategory_counts
        self._full = full_counts
        self.departments = tuple(sorted(global_counts))
        return self

    def _update(
        self, prior: Mapping[str, float], counts: Mapping[str, int]
    ) -> dict[str, float]:
        support = sum(counts.values())
        if support == 0:
            return dict(prior)
        denominator = support + self.alpha
        return {
            department: (
                counts.get(department, 0) + self.alpha * prior[department]
            )
            / denominator
            for department in self.departments
        }

    def predict_proba(
        self,
        *,
        category: str,
        district: str | None = None,
        subcategory: str | None = None,
    ) -> dict[str, float]:
        if not self.departments:
            raise RuntimeError("router must be fitted before prediction")
        total = sum(self._global.values())
        probability = {
            department: self._global[department] / total
            for department in self.departments
        }
        category_key = _norm(category)
        district_key = _norm(district)
        subcategory_key = _norm(subcategory)
        probability = self._update(probability, self._category.get(category_key, {}))
        if district_key:
            probability = self._update(
                probability,
                self._category_district.get((category_key, district_key), {}),
            )
        if self.use_subcategory and subcategory_key:
            probability = self._update(
                probability,
                self._subcategory.get((category_key, subcategory_key), {}),
            )
            if district_key:
                probability = self._update(
                    probability,
                    self._full.get(
                        (category_key, subcategory_key, district_key), {}
                    ),
                )
        return probability

    def predict_with_evidence(
        self,
        *,
        category: str,
        district: str | None = None,
        subcategory: str | None = None,
    ) -> IncidencePrediction | None:
        """Predict only when a category aggregate exists, with honest evidence.

        ``predict_proba`` retains global backoff for evaluation. Serving is
        stricter: an unseen category has no comparable-case cell, so this
        method returns ``None`` and the provider falls through to the governed
        crosswalk/rules ladder. The evidence describes the deepest aggregate
        cell that actually updated the posterior, never the global prior.
        """

        probabilities = self.predict_proba(
            category=category,
            district=district,
            subcategory=subcategory,
        )
        category_key = _norm(category)
        district_key = _norm(district)
        subcategory_key = _norm(subcategory)
        category_counts = self._category.get(category_key)
        if not category_counts:
            return None
        matches: list[tuple[str, Mapping[str, int]]] = [
            ("category", category_counts)
        ]
        if district_key:
            district_counts = self._category_district.get(
                (category_key, district_key)
            )
            if district_counts:
                matches.append(("category+district", district_counts))
        if self.use_subcategory and subcategory_key:
            subcategory_counts = self._subcategory.get(
                (category_key, subcategory_key)
            )
            if subcategory_counts:
                matches.append(("category+subcategory", subcategory_counts))
            if district_key:
                full_counts = self._full.get(
                    (category_key, subcategory_key, district_key)
                )
                if full_counts:
                    matches.append(
                        ("category+subcategory+district", full_counts)
                    )

        predicted = max(probabilities, key=probabilities.get)
        ranked_probabilities = sorted(probabilities.values(), reverse=True)
        probability_margin = ranked_probabilities[0] - (
            ranked_probabilities[1] if len(ranked_probabilities) > 1 else 0.0
        )
        if probability_margin < self.serving_min_margin:
            return None
        # Do not let a sparse deeper cell alter the posterior and then cite a
        # broader, better-supported cell as its evidence. The deepest cell that
        # actually updated the posterior must itself clear every serving gate;
        # otherwise the provider falls back.
        width, counts = matches[-1]
        if (
            sum(counts.values()) < self.serving_min_support
            or counts.get(predicted, 0) == 0
        ):
            return None
        support = sum(counts.values())
        concentration = counts[predicted] / support
        if concentration < self.serving_min_concentration:
            return None
        return IncidencePrediction(
            probabilities=probabilities,
            support=support,
            concentration=concentration,
            width=width,
        )


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _artifact_checksum(payload_without_checksum: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_json_bytes(payload_without_checksum)
    ).hexdigest()


def _artifact_file(path: Path) -> Path:
    return path if path.suffix.lower() == ".json" else path / INCIDENCE_ARTIFACT_FILENAME


def _counter_entries(counts: Mapping[str, int]) -> list[dict[str, object]]:
    return [
        {"department": department, "count": int(count)}
        for department, count in sorted(counts.items())
        if count > 0
    ]


def _table_rows(
    table: Mapping[object, Mapping[str, int]],
    fields: Sequence[str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw_key, counts in table.items():
        if sum(counts.values()) < INCIDENCE_ARTIFACT_MIN_SUPPORT:
            continue
        key = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        row = {field: value for field, value in zip(fields, key, strict=True)}
        row["counts"] = _counter_entries(counts)
        rows.append(row)
    return sorted(rows, key=lambda row: tuple(str(row[field]) for field in fields))


def incidence_router_artifact(router: IncidenceRouter) -> dict[str, object]:
    """Return the checksummed, aggregate-only serving artifact for ``router``.

    Only normalized taxonomy/district labels and counts are serialized. Route
    record IDs, group IDs, dates, language, and grievance text have no field in
    this schema. Keys with fewer than three historical cases are suppressed.
    """

    if not router.departments or not router._global:
        raise ValueError("router must be fitted before it can be serialized")
    payload: dict[str, object] = {
        "schema_version": INCIDENCE_ARTIFACT_SCHEMA_VERSION,
        "kind": "routing_incidence",
        "objective": "historical_incidence_only",
        "outcome_optimized": False,
        "parameters": {
            "alpha": router.alpha,
            "use_subcategory": router.use_subcategory,
            "serving_min_support": router.serving_min_support,
            "serving_min_concentration": router.serving_min_concentration,
            "serving_min_margin": router.serving_min_margin,
        },
        "departments": list(router.departments),
        "counts": {
            "global": _counter_entries(router._global),
            "category": _table_rows(router._category, ("category",)),
            "category_district": _table_rows(
                router._category_district, ("category", "district")
            ),
            "subcategory": (
                _table_rows(router._subcategory, ("category", "subcategory"))
                if router.use_subcategory
                else []
            ),
            "full": (
                _table_rows(
                    router._full,
                    ("category", "subcategory", "district"),
                )
                if router.use_subcategory
                else []
            ),
        },
        "privacy": {
            "contains_citizen_text_or_identifiers": False,
            "minimum_key_support": INCIDENCE_ARTIFACT_MIN_SUPPORT,
        },
    }
    payload["checksum"] = _artifact_checksum(payload)
    # Validate the artifact through the same path serving will use before any
    # bytes reach disk. This catches accidental schema drift at build time.
    _router_from_artifact(payload)
    return payload


def save_incidence_router(router: IncidenceRouter, path: Path) -> Path:
    """Atomically write one immutable governed artifact and return its file."""

    artifact = _artifact_file(Path(path))
    payload = incidence_router_artifact(router)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    if artifact.exists():
        raise FileExistsError(artifact)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{artifact.name}.", dir=artifact.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and fails if another process has
        # already published this immutable artifact path; os.replace would
        # silently overwrite that release in the race between exists() and
        # publication.
        os.link(temporary, artifact)
        temporary.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return artifact


def _valid_label(value: object, *, normalized: bool) -> bool:
    if not isinstance(value, str) or not value or len(value) > 160:
        return False
    if value != value.strip() or any(ord(character) < 32 for character in value):
        return False
    return not normalized or value == _norm(value)


def _parse_counter(
    value: object,
    *,
    departments: set[str],
    require_all_departments: bool = False,
) -> Counter[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("counts must be a non-empty list")
    counter: Counter[str] = Counter()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != _COUNT_ENTRY_KEYS:
            raise ValueError("invalid count entry")
        department = entry["department"]
        count = entry["count"]
        if department not in departments or department in counter:
            raise ValueError("unknown or duplicate department")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("count must be a positive integer")
        counter[department] = count
    if require_all_departments and set(counter) != departments:
        raise ValueError("global counts must cover every department")
    return counter


def _parse_table(
    value: object,
    *,
    name: str,
    fields: Sequence[str],
    departments: set[str],
) -> dict[object, Counter[str]]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    table: dict[object, Counter[str]] = {}
    for row in value:
        if not isinstance(row, dict) or set(row) != _TABLE_ROW_KEYS[name]:
            raise ValueError(f"invalid {name} row")
        labels = tuple(row[field] for field in fields)
        if not all(_valid_label(label, normalized=True) for label in labels):
            raise ValueError(f"invalid {name} key")
        key: object = labels[0] if len(labels) == 1 else labels
        if key in table:
            raise ValueError(f"duplicate {name} key")
        counter = _parse_counter(row["counts"], departments=departments)
        if sum(counter.values()) < INCIDENCE_ARTIFACT_MIN_SUPPORT:
            raise ValueError(f"{name} row is below the privacy support floor")
        table[key] = counter
    return table


def _router_from_artifact(payload: object) -> IncidenceRouter:
    if not isinstance(payload, dict) or set(payload) != _ARTIFACT_TOP_LEVEL_KEYS:
        raise ValueError("invalid incidence artifact top-level schema")
    if payload["schema_version"] != INCIDENCE_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("unsupported incidence artifact schema")
    if payload["kind"] != "routing_incidence":
        raise ValueError("invalid incidence artifact kind")
    if payload["objective"] != "historical_incidence_only":
        raise ValueError("invalid incidence objective")
    if payload["outcome_optimized"] is not False:
        raise ValueError("incidence artifact must declare outcome_optimized=false")

    checksum = payload["checksum"]
    if not isinstance(checksum, str) or not checksum.startswith("sha256:"):
        raise ValueError("invalid incidence artifact checksum")
    unsigned = dict(payload)
    unsigned.pop("checksum")
    if checksum != _artifact_checksum(unsigned):
        raise ValueError("incidence artifact checksum mismatch")

    parameters = payload["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != {
        "alpha",
        "use_subcategory",
        "serving_min_support",
        "serving_min_concentration",
        "serving_min_margin",
    }:
        raise ValueError("invalid incidence parameters")
    alpha = parameters["alpha"]
    use_subcategory = parameters["use_subcategory"]
    serving_min_support = parameters["serving_min_support"]
    serving_min_concentration = parameters["serving_min_concentration"]
    serving_min_margin = parameters["serving_min_margin"]
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise ValueError("alpha must be numeric")
    if not math.isfinite(float(alpha)) or float(alpha) <= 0.0:
        raise ValueError("alpha must be positive and finite")
    if not isinstance(use_subcategory, bool):
        raise ValueError("use_subcategory must be boolean")

    raw_departments = payload["departments"]
    if not isinstance(raw_departments, list) or not raw_departments:
        raise ValueError("departments must be a non-empty list")
    if not all(_valid_label(value, normalized=False) for value in raw_departments):
        raise ValueError("invalid department label")
    if raw_departments != sorted(set(raw_departments)):
        raise ValueError("departments must be unique and sorted")
    departments = set(raw_departments)

    privacy = payload["privacy"]
    if privacy != {
        "contains_citizen_text_or_identifiers": False,
        "minimum_key_support": INCIDENCE_ARTIFACT_MIN_SUPPORT,
    }:
        raise ValueError("invalid incidence privacy declaration")
    counts = payload["counts"]
    if not isinstance(counts, dict) or set(counts) != {
        "global",
        "category",
        "category_district",
        "subcategory",
        "full",
    }:
        raise ValueError("invalid incidence counts schema")

    global_counts = _parse_counter(
        counts["global"],
        departments=departments,
        require_all_departments=True,
    )
    category = _parse_table(
        counts["category"],
        name="category",
        fields=("category",),
        departments=departments,
    )
    if not category:
        raise ValueError("incidence artifact has no supported category cells")
    category_district = _parse_table(
        counts["category_district"],
        name="category_district",
        fields=("category", "district"),
        departments=departments,
    )
    subcategory = _parse_table(
        counts["subcategory"],
        name="subcategory",
        fields=("category", "subcategory"),
        departments=departments,
    )
    full = _parse_table(
        counts["full"],
        name="full",
        fields=("category", "subcategory", "district"),
        departments=departments,
    )
    if not use_subcategory and (subcategory or full):
        raise ValueError("non-subcategory artifact contains unreachable tables")

    router = IncidenceRouter(
        alpha=float(alpha),
        use_subcategory=use_subcategory,
        serving_min_support=serving_min_support,
        serving_min_concentration=serving_min_concentration,
        serving_min_margin=serving_min_margin,
    )
    router.departments = tuple(raw_departments)
    router._global = global_counts
    router._category = defaultdict(Counter, category)
    router._category_district = defaultdict(Counter, category_district)
    router._subcategory = defaultdict(Counter, subcategory)
    router._full = defaultdict(Counter, full)
    return router


def load_incidence_router(path: Path) -> IncidenceRouter | None:
    """Load a checksummed artifact without raising or making network calls."""

    try:
        artifact = _artifact_file(Path(path))
        if not artifact.is_file():
            return None
        if artifact.stat().st_size <= 0 or artifact.stat().st_size > INCIDENCE_ARTIFACT_MAX_BYTES:
            return None

        def reject_constant(value: str) -> object:
            raise ValueError(f"invalid JSON constant {value}")

        payload = json.loads(
            artifact.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
        )
        return _router_from_artifact(payload)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _score(
    router: IncidenceRouter,
    records: Sequence[RouteRecord],
    *,
    expected_labels: Sequence[str] | None = None,
) -> list[ScoredExample]:
    departments = set(router.departments)
    unseen = sorted({record.department for record in records}.difference(departments))
    labels = (
        tuple(expected_labels)
        if expected_labels is not None
        else tuple(sorted(departments.union(unseen)))
    )
    examples: list[ScoredExample] = []
    for record in records:
        probability = router.predict_proba(
            category=record.category,
            district=record.district,
            subcategory=record.subcategory,
        )
        complete = {label: probability.get(label, 0.0) for label in labels}
        examples.append(
            ScoredExample(
                item_id=record.item_id,
                gold_label=record.department,
                probabilities=complete,
                group_id=record.group_id,
                language=record.language,
                weight=record.weight,
            )
        )
    return examples


def _suppress_non_cluster_robust_intervals(
    metrics: Mapping[str, object],
) -> dict[str, object]:
    """Remove weighted Wilson bounds that assume independent complaints.

    Historical routing inputs are aggregate cells whose integer weights expand
    correlated complaints. Point metrics remain useful historical-agreement
    summaries, but treating every weighted complaint as an independent trial
    produces falsely narrow intervals. Keep the field names visible with
    ``None`` so downstream reports cannot silently mistake absence for a parser
    failure, and state what must replace them.
    """

    result = dict(metrics)
    result["accuracy_interval"] = None
    result["selective_accuracy_interval"] = None
    result["interval_status"] = (
        "suppressed_not_cluster_robust_for_weighted_route_cells"
    )
    result["interval_requirement"] = (
        "district_time_or_campaign_block_uncertainty_required"
    )
    return result


def _hard_backoff_score(
    train: Sequence[RouteRecord],
    records: Sequence[RouteRecord],
    *,
    expected_labels: Sequence[str] | None = None,
) -> list[ScoredExample]:
    """Unsmooth category+district -> category -> global incidence baseline."""

    global_counts: Counter[str] = Counter()
    by_category: dict[str, Counter[str]] = defaultdict(Counter)
    by_district: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for record in train:
        category = _norm(record.category)
        global_counts[record.department] += record.weight
        by_category[category][record.department] += record.weight
        if _norm(record.district):
            by_district[(category, _norm(record.district))][record.department] += record.weight
    labels = (
        tuple(expected_labels)
        if expected_labels is not None
        else tuple(
            sorted(set(global_counts).union(record.department for record in records))
        )
    )
    examples = []
    for record in records:
        counts = (
            by_district.get((_norm(record.category), _norm(record.district)))
            or by_category.get(_norm(record.category))
            or global_counts
        )
        total = sum(counts.values())
        probability = {
            label: counts.get(label, 0) / total
            for label in labels
        }
        examples.append(
            ScoredExample(
                item_id=record.item_id,
                gold_label=record.department,
                probabilities=probability,
                group_id=record.group_id,
                language=record.language,
                weight=record.weight,
            )
        )
    return examples


@dataclass
class RoutingBenchmark:
    router: IncidenceRouter
    report: dict[str, object]


def benchmark_incidence_router(
    records: Sequence[RouteRecord],
    *,
    alpha_values: Sequence[float] = (3.0, 10.0, 30.0, 100.0),
    history_year_values: Sequence[int | None] = (None,),
    use_subcategory: bool = False,
    serving_min_support: int = INCIDENCE_SERVING_MIN_SUPPORT,
    serving_min_concentration: float = INCIDENCE_SERVING_MIN_CONCENTRATION,
    serving_min_margin: float = INCIDENCE_SERVING_MIN_MARGIN,
    artifact_dir: Path | None = None,
) -> RoutingBenchmark:
    """Select smoothing on chronological validation, then score test once."""

    validate_route_records(records)
    if not alpha_values or any(
        not math.isfinite(alpha) or alpha <= 0.0 for alpha in alpha_values
    ):
        raise ValueError("alpha_values must contain positive finite values")
    if not history_year_values or any(
        value is not None
        and (isinstance(value, bool) or not isinstance(value, int) or value < 1)
        for value in history_year_values
    ):
        raise ValueError("history_year_values must contain positive integers or None")
    train = [record for record in records if record.split == "train"]
    validation = [record for record in records if record.split == "validation"]
    test = [record for record in records if record.split == "test"]
    expected_labels = tuple(
        sorted({record.department for record in (*train, *validation, *test)})
    )

    def recent(rows: Sequence[RouteRecord], years: int | None) -> list[RouteRecord]:
        if years is None:
            return list(rows)
        latest = max(record.observed_on.year for record in rows)
        first = latest - years + 1
        return [record for record in rows if record.observed_on.year >= first]

    candidates: list[
        tuple[float, int | None, IncidenceRouter, dict[str, object], int]
    ] = []
    for history_years in history_year_values:
        candidate_train = recent(train, history_years)
        for alpha in alpha_values:
            router = IncidenceRouter(
                alpha=alpha,
                use_subcategory=use_subcategory,
                serving_min_support=serving_min_support,
                serving_min_concentration=serving_min_concentration,
                serving_min_margin=serving_min_margin,
            ).fit(candidate_train)
            metrics = _suppress_non_cluster_robust_intervals(
                classification_metrics(
                    _score(router, validation, expected_labels=expected_labels),
                    expected_labels=expected_labels,
                    top_k=(1, 3, 5),
                )
            )
            candidates.append(
                (
                    alpha,
                    history_years,
                    router,
                    metrics,
                    sum(record.weight for record in candidate_train),
                )
            )
    alpha, history_years, _, validation_metrics, validation_fit_cases = max(
        candidates,
        key=lambda row: (
            float(row[3]["accuracy"]),
            float(row[3]["top_k_accuracy"]["3"]),
            -float(row[3]["log_loss"]),
            -row[0],
        ),
    )

    # Hyperparameters are chosen on validation, then the final candidate is
    # refitted on all pre-test history.  The untouched test period is still
    # scored exactly once; withholding validation from the final fit would
    # make the deployed historical lookup needlessly stale.
    pre_test = recent([*train, *validation], history_years)
    fitted_router = IncidenceRouter(
        alpha=alpha,
        use_subcategory=use_subcategory,
        serving_min_support=serving_min_support,
        serving_min_concentration=serving_min_concentration,
        serving_min_margin=serving_min_margin,
    ).fit(pre_test)
    # Score the exact privacy-suppressed shape serving will load, rather than
    # the richer in-memory counters used during fitting. This round trip is
    # deliberately in-memory; publishing remains explicit and immutable below.
    router = _router_from_artifact(incidence_router_artifact(fitted_router))
    test_examples = _score(router, test, expected_labels=expected_labels)
    test_metrics = _suppress_non_cluster_robust_intervals(
        classification_metrics(
            test_examples,
            expected_labels=expected_labels,
            top_k=(1, 3, 5),
        )
    )
    hard_metrics = _suppress_non_cluster_robust_intervals(
        classification_metrics(
            _hard_backoff_score(pre_test, test, expected_labels=expected_labels),
            expected_labels=expected_labels,
            top_k=(1, 3, 5),
        )
    )
    unseen = sorted(
        {record.department for record in (*validation, *test)}
        .difference(router.departments)
    )
    serving_eligible = sum(
        record.weight
        for record in test
        if router.predict_with_evidence(
            category=record.category,
            district=record.district,
            subcategory=record.subcategory,
        )
        is not None
    )
    test_cases = sum(record.weight for record in test)
    report: dict[str, object] = {
        "objective": "historical_incidence_only",
        "outcome_optimized": False,
        "live_feature_shape": (
            "category+subcategory+district"
            if use_subcategory
            else "category+district"
        ),
        "selected_alpha": alpha,
        "selected_history_years": history_years,
        "validation_fit_cases": validation_fit_cases,
        "final_fit_cases": sum(record.weight for record in pre_test),
        "final_fit": "train_plus_validation_before_untouched_test",
        "evaluation_router": "serialized_reloaded_privacy_suppressed_artifact",
        "serving_gates": {
            "minimum_support": router.serving_min_support,
            "minimum_concentration": router.serving_min_concentration,
            "minimum_top1_margin": router.serving_min_margin,
            "eligible_test_cases": serving_eligible,
            "eligible_test_coverage": serving_eligible / test_cases,
            "fallback_test_cases": test_cases - serving_eligible,
        },
        "split_counts": {
            "train": sum(record.weight for record in train),
            "validation": sum(record.weight for record in validation),
            "test": sum(record.weight for record in test),
        },
        "validation": validation_metrics,
        "test": test_metrics,
        "test_by_language": {
            language: _suppress_non_cluster_robust_intervals(metrics)
            for language, metrics in metrics_by_language(
                test_examples,
                expected_labels=expected_labels,
                top_k=(1, 3, 5),
            ).items()
        },
        "hard_backoff_test": hard_metrics,
        "unseen_departments": unseen,
        "candidate_validation": [
            {
                "alpha": candidate_alpha,
                "history_years": candidate_history_years,
                "accuracy": metrics["accuracy"],
                "top3_accuracy": metrics["top_k_accuracy"]["3"],
                "log_loss": metrics["log_loss"],
            }
            for candidate_alpha, candidate_history_years, _, metrics, _ in candidates
        ],
        "limitations": [
            "predicts where similar cases were sent, not where they resolve best",
            "chronological holdout measures drift but does not remove policy bias",
            "subcategory benchmark is non-live until the classifier supplies it",
            "weighted Wilson intervals are suppressed because aggregate route cells are not independent trials",
        ],
    }
    if artifact_dir is not None:
        artifact_path = save_incidence_router(router, artifact_dir)
        artifact_payload = incidence_router_artifact(router)
        report["serving_artifact"] = {
            "path": str(artifact_path),
            "schema_version": INCIDENCE_ARTIFACT_SCHEMA_VERSION,
            "checksum": artifact_payload["checksum"],
            "contains_citizen_text_or_identifiers": False,
            "outcome_optimized": False,
        }
    return RoutingBenchmark(router=router, report=report)


def temporal_split_counts(records: Iterable[RouteRecord]) -> dict[str, int]:
    """Small helper for manifest diagnostics without selecting any text."""

    return dict(sorted(Counter(record.split for record in records).items()))

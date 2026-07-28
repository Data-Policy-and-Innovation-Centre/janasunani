"""Verify a corrected PII gold file, and show what the human pass changed.

The gold file holds real citizen text, so it is DVC-tracked and never enters
git. That makes the usual review impossible: the PR is a `.dvc` pointer, and the
content cannot be pasted into a diff. This reports **statistics and structure**
so the labelling can be reviewed without anyone reading the corpus, and without
citizen text reaching a terminal, a log, or a PR comment.

Nothing here prints gold text unless `--show-samples` is passed explicitly, and
even then only entity surface forms, capped and truncated. Default output is
counts only.

Two checks matter most:

1. **Structural validity.** Offsets in range, non-empty, non-overlapping, labels
   recognised. A malformed gold file silently scores wrong rather than failing.
2. **Did the human pass actually happen.** The draft is pre-annotated by the
   production analyzer, so evaluating an unedited draft scores ~100% recall by
   construction (see `scripts/bootstrap_pii_gold.py`). Comparing corrected
   against draft turns "was this labelled?" into a number: spans added, deleted,
   re-bounded, relabelled. Near-zero changes means the measurement is void.

Usage:
    uv run --extra pipeline-core python scripts/verify_pii_gold.py \
        --gold data/external/pii_gold.jsonl \
        [--draft data/output/pii_gold_draft_n50.jsonl] [--json] [--show-samples]

Exit status is non-zero if any structural check fails, so CI or a pre-merge hook
can gate on it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Mirrors the typed tokens the production tagger emits
# (janasunani/pipeline/stages/pii_tagger.py).
KNOWN_ENTITIES = {"NAME", "PHONE", "EMAIL", "AADHAAR", "PAN"}
ENTITY_KEYS = ("entity", "label", "type", "entity_type")


@dataclass
class Span:
    start: int
    end: int
    entity: str

    def key(self) -> tuple[int, int, str]:
        return (self.start, self.end, self.entity)

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass
class Record:
    id: str
    text: str
    spans: list[Span]


@dataclass
class Report:
    records: int = 0
    spans: int = 0
    by_entity: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    empty_records: int = 0
    unlabelled_records: int = 0

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _entity_of(raw: dict[str, Any]) -> str:
    for key in ENTITY_KEYS:
        if key in raw and raw[key]:
            return str(raw[key]).strip().upper()
    raise KeyError("span has no entity/label/type/entity_type")


def load(path: Path) -> list[Record]:
    """Parse the gold JSONL. Raises on malformed lines rather than skipping."""
    records: list[Record] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON: {exc}") from exc
            if not isinstance(raw.get("text"), str):
                raise ValueError(f"{path}:{number}: 'text' missing or not a string")
            spans = [
                Span(int(s["start"]), int(s["end"]), _entity_of(s))
                for s in raw.get("entities", [])
            ]
            records.append(
                Record(id=str(raw.get("id") or f"line-{number}"), text=raw["text"], spans=spans)
            )
    return records


def check(records: Iterable[Record]) -> Report:
    report = Report()
    seen_ids: set[str] = set()

    for record in records:
        report.records += 1
        if record.id in seen_ids:
            report.fail(f"{record.id}: duplicate record id")
        seen_ids.add(record.id)

        if not record.text.strip():
            report.empty_records += 1
            report.warn(f"{record.id}: empty text")
        if not record.spans:
            report.unlabelled_records += 1

        length = len(record.text)
        for span in record.spans:
            report.spans += 1
            report.by_entity[span.entity] += 1

            if span.entity not in KNOWN_ENTITIES:
                report.fail(f"{record.id}: unrecognised entity {span.entity!r}")
            if span.start < 0 or span.end > length:
                report.fail(
                    f"{record.id}: span [{span.start},{span.end}) outside text of length {length}"
                )
                continue
            if span.start >= span.end:
                report.fail(f"{record.id}: empty or inverted span [{span.start},{span.end})")
                continue
            surface = record.text[span.start : span.end]
            if not surface.strip():
                report.fail(f"{record.id}: span [{span.start},{span.end}) is whitespace only")
            elif surface != surface.strip():
                # Leading/trailing whitespace shifts exact-match scoring without
                # changing what a human meant to mark.
                report.warn(f"{record.id}: span [{span.start},{span.end}) has loose boundaries")

        ordered = sorted(record.spans, key=lambda s: (s.start, s.end))
        for left, right in zip(ordered, ordered[1:]):
            if left.overlaps(right):
                report.fail(
                    f"{record.id}: overlapping spans [{left.start},{left.end}) "
                    f"and [{right.start},{right.end})"
                )

    return report


def diff(draft: list[Record], gold: list[Record]) -> dict[str, Any]:
    """Summarise the human pass: what was added, removed, re-bounded, relabelled."""
    draft_by_id = {r.id: r for r in draft}
    gold_by_id = {r.id: r for r in gold}
    shared = sorted(set(draft_by_id) & set(gold_by_id))

    added = removed = kept = rebounded = relabelled = 0
    added_by_entity: Counter = Counter()
    removed_by_entity: Counter = Counter()
    touched_records = 0
    text_mismatches: list[str] = []

    for record_id in shared:
        # Offsets index into the text. If the annotator edited the text itself,
        # every span in that record is silently misaligned and the diff below is
        # meaningless, so this is an error rather than a warning.
        if draft_by_id[record_id].text != gold_by_id[record_id].text:
            text_mismatches.append(record_id)

        before = {s.key() for s in draft_by_id[record_id].spans}
        after = {s.key() for s in gold_by_id[record_id].spans}
        new, gone = after - before, before - after
        kept += len(after & before)
        rebounded_here = relabelled_here = 0

        # A span whose offsets moved but whose label stayed, and vice versa, are
        # corrections rather than an add plus a delete. Match them up so the
        # counts describe what the annotator did.
        by_label: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        for key in gone:
            by_label[key[2]].append(key)
        for key in sorted(new):
            candidates = by_label.get(key[2], [])
            overlap = next(
                (c for c in candidates if c[0] < key[1] and key[0] < c[1]),
                None,
            )
            if overlap is not None:
                candidates.remove(overlap)
                rebounded += 1
                rebounded_here += 1
                new = new - {key}
                gone = gone - {overlap}

        spans_before = {(s.start, s.end): s.entity for s in draft_by_id[record_id].spans}
        for key in sorted(new):
            if (key[0], key[1]) in spans_before and spans_before[(key[0], key[1])] != key[2]:
                relabelled += 1
                relabelled_here += 1
                new = new - {key}
                gone = gone - {(key[0], key[1], spans_before[(key[0], key[1])])}

        added += len(new)
        removed += len(gone)
        for key in new:
            added_by_entity[key[2]] += 1
        for key in gone:
            removed_by_entity[key[2]] += 1
        # A record fixed only by re-bounding or relabelling was still worked on:
        # those matches were removed from `new`/`gone` above, so count them too.
        if new or gone or rebounded_here or relabelled_here:
            touched_records += 1

    return {
        "records_compared": len(shared),
        "records_only_in_draft": sorted(set(draft_by_id) - set(gold_by_id)),
        "records_only_in_gold": sorted(set(gold_by_id) - set(draft_by_id)),
        "text_mismatches": text_mismatches,
        "records_touched": touched_records,
        "spans_added": added,
        "spans_removed": removed,
        "spans_rebounded": rebounded,
        "spans_relabelled": relabelled,
        "spans_unchanged": kept,
        "added_by_entity": dict(added_by_entity),
        "removed_by_entity": dict(removed_by_entity),
    }


def _samples(records: list[Record], limit: int = 5, width: int = 24) -> list[str]:
    """Truncated entity surface forms, for spot-checking boundaries. Opt-in."""
    out = []
    for record in records:
        for span in record.spans:
            if 0 <= span.start < span.end <= len(record.text):
                surface = record.text[span.start : span.end].replace("\n", " ")
                out.append(f"{span.entity}: {surface[:width]!r}")
            if len(out) >= limit:
                return out
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True, help="Corrected gold JSONL")
    parser.add_argument("--draft", type=Path, help="Pre-annotated draft, to diff against")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument(
        "--show-samples",
        action="store_true",
        help=(
            "Print a few truncated entity surface forms. REVEALS CITIZEN PII: "
            "interactive use only, never in CI, a hook, or any shared/logged workflow"
        ),
    )
    args = parser.parse_args()

    gold = load(args.gold)
    report = check(gold)
    payload: dict[str, Any] = {
        "gold": str(args.gold),
        "records": report.records,
        "spans": report.spans,
        "by_entity": dict(sorted(report.by_entity.items())),
        "records_without_spans": report.unlabelled_records,
        "errors": report.errors,
        "warnings": report.warnings,
    }

    if args.draft:
        human = diff(load(args.draft), gold)
        payload["human_pass"] = human
        # Completeness is part of correctness: a gold file missing pages, or whose
        # text was edited out from under its offsets, scores wrong rather than
        # failing. Both are hard errors so a pre-merge gate catches them.
        if human["records_only_in_draft"]:
            missing = human["records_only_in_draft"]
            report.fail(
                f"{len(missing)} draft record(s) absent from gold "
                f"(first: {missing[:3]}). Every drafted page must be adjudicated."
            )
        if human["records_only_in_gold"]:
            extra = human["records_only_in_gold"]
            report.fail(
                f"{len(extra)} gold record(s) not present in the draft "
                f"(first: {extra[:3]}). Ids must match the drafted set."
            )
        if human["text_mismatches"]:
            bad = human["text_mismatches"]
            report.fail(
                f"{len(bad)} record(s) whose text differs between draft and gold "
                f"(first: {bad[:3]}). Offsets index into the text, so editing it "
                f"silently invalidates every span in that record."
            )
        payload["errors"] = report.errors

    if args.show_samples:
        payload["samples"] = _samples(gold)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"gold:    {args.gold}")
        print(f"records: {report.records}   spans: {report.spans}")
        for entity, count in sorted(report.by_entity.items()):
            print(f"  {entity:<8} {count}")
        if report.unlabelled_records:
            print(f"records with no spans: {report.unlabelled_records}")

        human = payload.get("human_pass")
        if human:
            print("\nhuman pass vs draft:")
            print(f"  records compared : {human['records_compared']}")
            print(f"  records touched  : {human['records_touched']}")
            print(f"  spans added      : {human['spans_added']}  {human['added_by_entity']}")
            print(f"  spans removed    : {human['spans_removed']}  {human['removed_by_entity']}")
            print(f"  spans re-bounded : {human['spans_rebounded']}")
            print(f"  spans relabelled : {human['spans_relabelled']}")
            print(f"  spans unchanged  : {human['spans_unchanged']}")
            changed = (
                human["spans_added"]
                + human["spans_removed"]
                + human["spans_rebounded"]
                + human["spans_relabelled"]
            )
            if changed == 0:
                print(
                    "\n  WARNING: the corrected file is identical to the draft.\n"
                    "  The draft is analyzer output, so evaluating it scores ~100% recall\n"
                    "  by construction. The measurement would be void."
                )
            elif human["spans_added"] == 0:
                print(
                    "\n  WARNING: no spans were ADDED. Added spans are the recall misses,\n"
                    "  which is the number the scorecard exists to report. Deletions alone\n"
                    "  measure precision only."
                )

        if args.show_samples:
            print("\nsamples (citizen text):")
            for line in payload["samples"]:
                print(f"  {line}")

        for warning in report.warnings[:20]:
            print(f"warning: {warning}")
        if len(report.warnings) > 20:
            print(f"... and {len(report.warnings) - 20} more warnings")
        for error in report.errors:
            print(f"ERROR: {error}")

    if report.errors:
        sys.exit(1)


if __name__ == "__main__":
    main()

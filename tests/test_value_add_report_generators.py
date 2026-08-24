from pathlib import Path
import sys
import zipfile

from janasunani.evaluation.value_add_benchmark_facts import BenchmarkFacts

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import create_officer_brief as officer  # noqa: E402
import create_public_systems_capability_brief as public  # noqa: E402
from docx_archive import CANONICAL_ZIP_TIMESTAMP, main as canonicalize_main  # noqa: E402
import update_value_add_report as report  # noqa: E402


def benchmark_facts() -> BenchmarkFacts:
    latency_stat = {
        "n": 4,
        "n_clusters": 2,
        "mean_seconds": 2.5,
        "se_seconds": 0.1,
        "p50": 2.3,
        "p90": 3.0,
        "p95": 3.2,
    }
    return BenchmarkFacts(
        bundle_id="test-bundle-1234567890",
        publication_ready=False,
        latency={
            "input_paths": {
                "text": {"e2e": latency_stat},
                "document": {"e2e": {**latency_stat, "p50": 8.5}},
            },
            "stages": {"e2e": latency_stat},
            "processor_startup_seconds": 1.2,
            "attempts": 8,
            "completed_attempts": 8,
            "failed_attempts": 0,
        },
        actionability={
            "selected_candidate": "tfidf",
            "release_eligible": False,
            "n": 50,
            "accuracy": 0.82,
            "actual_review": 20,
            "flagged": 23,
            "confusion": {
                "true_review": 18,
                "false_review": 5,
                "true_actionable": 30,
            },
        },
        categorization={
            "accuracy": 0.4514,
            "top_k_accuracy": {"3": 0.6904},
            "macro_f1": 0.42,
            "n": 208267,
        },
        weak_labels={
            "eligible_ticket_labels": {"valid_single_label": 106683},
            "office_variation": {"max_total_variation": 0.522},
        },
        pii={
            "overall": {
                "gold": 50,
                "predicted": 80,
                "overlap_hits": 42,
                "exact_hits": 35,
                "overlap_recall": 0.84,
                "exact_recall": 0.7,
            },
            "coverage": {"overlap_recall": 0.86},
            "by_entity": {
                entity: {"overlap_recall": value}
                for entity, value in {
                    "PHONE": 0.9,
                    "AADHAAR": 0.8,
                    "EMAIL": 0.75,
                    "NAME": 0.7,
                }.items()
            },
            "excluded_by_policy": 2,
        },
        routing_all={
            "accuracy": 0.4514,
            "top_k_accuracy": {"3": 0.6904},
            "n": 208267,
        },
        routing_informative={
            "accuracy": 0.5496,
            "top_k_accuracy": {"3": 0.7968},
            "n": 120000,
        },
        routing_outcome={
            "validation_2024": {
                "support": {"n_evaluated": 100},
                "tau_0": {
                    "ridge_top_three": {
                        "delta_dm": 14.0,
                        "delta_aipw": 12.4,
                        "aipw_se": 2.0,
                        "ess_over_n": 0.5,
                    },
                    "gbm_top_three": {
                        "delta_dm": 28.0,
                        "delta_aipw": 26.77,
                        "aipw_se": 3.0,
                        "ess_over_n": 0.4,
                    },
                },
            },
            "test_2025": {
                "support": {"n_evaluated": 80},
                "tau_0": {
                    "ridge_top_three": {
                        "delta_dm": -1.0,
                        "delta_aipw": -2.35,
                        "aipw_se": 2.0,
                        "ess_over_n": 0.5,
                    },
                    "gbm_top_three": {
                        "delta_dm": 1.0,
                        "delta_aipw": 0.15,
                        "aipw_se": 3.0,
                        "ess_over_n": 0.4,
                    },
                },
            },
            "robustness_ladder_2024": {
                "rungs": {
                    rung: {
                        "n_validation": 100,
                        "delta": delta,
                        "delta_evaluation_se": 0.01,
                    }
                    for rung, delta in {
                        "R0_binary_completers": 0.0305,
                        "R1_proxy_actionable_completers": 0.0002,
                        "R2_proxy_actionable_restricted": 0.0002,
                        "R3_proxy_actionable_restricted_ipcw": 0.0002,
                    }.items()
                }
            },
        },
        summary={
            "critical_fact_recall": {"successes": 55, "n": 84},
            "usable_without_edit_rate": {"successes": 8},
            "pii_leak_case_rate": {"successes": 4},
            "generated_n": 26,
        },
        section_status={
            "speed": {"available_required": 1, "required": 1},
            "accuracy": {"available_required": 6, "required": 6},
            "impact": {"available_required": 0, "required": 2},
        },
    )


def test_report_generators_create_reviewable_bundle_backed_markdown(
    tmp_path, monkeypatch
):
    facts = benchmark_facts()
    for module in (officer, public, report):
        monkeypatch.setattr(module, "load_benchmark_facts", lambda _path: facts)

    officer_path = tmp_path / "officer.md"
    public_path = tmp_path / "public.md"
    report_path = tmp_path / "report.md"
    officer.create_brief(officer_path, benchmark_bundle=Path("unused.json"))
    public.create_brief(public_path, benchmark_bundle=Path("unused.json"))
    report.create_report(report_path, benchmark_bundle=Path("unused.json"))

    for output in (officer_path, public_path, report_path):
        text = output.read_text(encoding="utf-8")
        assert output.stat().st_size > 1_000
        assert "test-bundle-1234" in text
        assert text.startswith("---\n")


def test_docx_cli_produces_reproducible_archives(tmp_path):
    first = tmp_path / "first.docx"
    second = tmp_path / "second.docx"
    for path, timestamp in (
        (first, (2025, 1, 2, 3, 4, 6)),
        (second, (2026, 7, 8, 9, 10, 12)),
    ):
        with zipfile.ZipFile(path, "w") as archive:
            member = zipfile.ZipInfo("word/document.xml", date_time=timestamp)
            archive.writestr(member, b"<document>same bytes</document>")

    assert canonicalize_main([str(first), str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert {member.date_time for member in archive.infolist()} == {
            CANONICAL_ZIP_TIMESTAMP
        }

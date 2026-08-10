"""Prepare resolution or finalize a frontier-adjudicated actionability gold set."""

from __future__ import annotations

import argparse
from pathlib import Path

from janasunani.evaluation.adjudication import finalize_gold, prepare_resolution


def _add_provenance_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--protocol-version", default="unavailable")
    parser.add_argument("--rubric-version", default="unavailable")
    parser.add_argument("--prompt-sha256", default="unavailable")
    parser.add_argument("--judge-a-model", default="unavailable")
    parser.add_argument("--judge-b-model", default="unavailable")
    parser.add_argument("--resolver-model", default="unavailable")
    parser.add_argument("--inference-environment", default="unavailable")
    parser.add_argument("--egress-policy", default="unavailable")
    parser.add_argument("--retention-policy", default="unavailable")


def _provenance(args: argparse.Namespace) -> dict[str, str]:
    return {
        "protocol_version": args.protocol_version,
        "rubric_version": args.rubric_version,
        "prompt_sha256": args.prompt_sha256,
        "judge_a_model": args.judge_a_model,
        "judge_b_model": args.judge_b_model,
        "resolver_model": args.resolver_model,
        "inference_environment": args.inference_environment,
        "egress_policy": args.egress_policy,
        "retention_policy": args.retention_policy,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--sample", type=Path, required=True)
    prepare.add_argument("--judge-a", type=Path, required=True)
    prepare.add_argument("--judge-b", type=Path, required=True)
    prepare.add_argument("--consensus", type=Path, required=True)
    prepare.add_argument("--resolver-input", type=Path, required=True)
    prepare.add_argument("--report", type=Path, required=True)
    _add_provenance_arguments(prepare)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--sample", type=Path, required=True)
    finalize.add_argument("--consensus", type=Path, required=True)
    finalize.add_argument("--resolver", type=Path, required=True)
    finalize.add_argument("--gold", type=Path, required=True)
    finalize.add_argument("--manifest", type=Path, required=True)
    finalize.add_argument("--sample-manifest", type=Path)
    _add_provenance_arguments(finalize)

    args = parser.parse_args()
    if args.command == "prepare":
        prepare_resolution(
            args.sample,
            args.judge_a,
            args.judge_b,
            consensus_path=args.consensus,
            resolver_input_path=args.resolver_input,
            report_path=args.report,
            provenance=_provenance(args),
        )
    else:
        finalize_gold(
            args.sample,
            args.consensus,
            args.resolver,
            gold_path=args.gold,
            manifest_path=args.manifest,
            sample_manifest_path=args.sample_manifest,
            provenance=_provenance(args),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

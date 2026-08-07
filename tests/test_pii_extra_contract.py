"""Dependency contracts for the lightweight PII environment (issue #134)."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.markers import Marker


ROOT_DIR = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = ROOT_DIR / "pyproject.toml"
UV_LOCK_PATH = ROOT_DIR / "uv.lock"
DVC_PATH = ROOT_DIR / "dvc.yaml"
DVC_LOCK_PATH = ROOT_DIR / "dvc.lock"
PRESIDIO_ANALYZER_REQUIREMENT = "presidio-analyzer==2.2.363"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT_PATH.read_text())


def test_pii_extra_is_separate_from_the_legacy_numpy_pipeline_contract():
    """Presidio/spaCy must not inherit the non-PII stages' numpy<2 pin."""
    extras = _pyproject()["project"]["optional-dependencies"]
    pii = set(extras["pii"])
    pipeline_core = set(extras["pipeline-core"])

    assert pii == {
        "numpy>=2,<2.5",
        PRESIDIO_ANALYZER_REQUIREMENT,
        "presidio-anonymizer>=2.2,<3",
        "spacy>=3.8,<3.9",
        (
            "en-core-web-sm @ https://github.com/explosion/spacy-models/"
            "releases/download/en_core_web_sm-3.8.0/"
            "en_core_web_sm-3.8.0-py3-none-any.whl"
        ),
    }
    assert "numpy<2" in pipeline_core, "the legacy non-PII pin is intentional"
    assert pii.isdisjoint(pipeline_core)
    assert pii - {"numpy>=2,<2.5"} <= set(extras["demo"]), (
        "the live demo must retain Presidio/spaCy"
    )
    conflicts = _pyproject()["tool"]["uv"]["conflicts"]
    assert [{"extra": "pii"}, {"extra": "pipeline-core"}] in conflicts
    assert [{"extra": "pii"}, {"extra": "demo"}] in conflicts


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_pii_and_demo_use_the_same_frozen_analyzer_release():
    """Gold evaluation and production redaction must run identical analyzer code."""
    extras = _pyproject()["project"]["optional-dependencies"]
    assert PRESIDIO_ANALYZER_REQUIREMENT in extras["pii"]
    assert PRESIDIO_ANALYZER_REQUIREMENT in extras["demo"]

    pii_version = _requirements_for_linux("pii").get("presidio-analyzer")
    demo_version = _requirements_for_linux("demo").get("presidio-analyzer")
    assert pii_version == demo_version == "2.2.363"


def _option(tokens: list[str], name: str) -> str:
    return tokens[tokens.index(name) + 1]


def _stages(tokens: list[str]) -> list[str]:
    start = tokens.index("--stages") + 1
    end = tokens.index("--workers", start)
    return tokens[start:end]


def test_dvc_sample_runs_each_stage_in_its_compatible_extra():
    """The DVC sample must cross env boundaries via one shared artifact DB."""
    pipeline_sample = yaml.safe_load(DVC_PATH.read_text())["stages"]["pipeline-sample"]
    commands = [shlex.split(command) for command in pipeline_sample["cmd"].split("&&")]

    expected = [
        ("pipeline-core", ["format_classifier", "ocr_extraction"]),
        ("pii", ["pii_tagger"]),
    ]
    assert [(_option(command, "--extra"), _stages(command)) for command in commands] == expected
    assert {_option(command, "--db") for command in commands} == {
        "data/processed/pipeline-sample.sqlite"
    }
    assert {_option(command, "--input") for command in commands} == {
        "data/raw/documents-sample"
    }
    assert {_option(command, "--models") for command in commands} == {"models"}

    assert set(pipeline_sample["deps"]) >= {
        "janasunani/pipeline/stages/format_classifier",
        "janasunani/pipeline/stages/ocr_extraction",
        "janasunani/pipeline/stages/pii_tagger.py",
    }


def test_dvc_sample_lock_command_matches_the_stage_definition():
    """A checkout must not advertise an artifact from an obsolete environment."""
    stage_command = yaml.safe_load(DVC_PATH.read_text())["stages"]["pipeline-sample"][
        "cmd"
    ]
    locked_command = yaml.safe_load(DVC_LOCK_PATH.read_text())["stages"][
        "pipeline-sample"
    ]["cmd"]

    assert locked_command == stage_command


def _requirements_for_linux(extra: str) -> dict[str, str]:
    """Evaluate a frozen extra's lock selection for the Python 3.13 CPU box."""
    proc = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--extra",
            extra,
            "--no-dev",
            "--no-hashes",
            "--no-emit-project",
        ],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    linux_py313 = {
        "sys_platform": "linux",
        "platform_system": "Linux",
        "os_name": "posix",
        "platform_machine": "x86_64",
        "python_version": "3.13",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
    }
    resolved: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        requirement, _, marker = line.partition(";")
        if marker.strip() and not Marker(marker.strip()).evaluate(linux_py313):
            continue
        name, _, version = requirement.strip().partition("==")
        resolved[re.split(r"[\[ ]", name)[0].lower()] = version.strip()
    return resolved


def _locked_package(lock: str, name: str, version: str) -> str:
    package = re.search(
        rf'\[\[package\]\]\nname = "{re.escape(name)}"\n'
        rf'version = "{re.escape(version)}".*?(?=\n\[\[package\]\]|\Z)',
        lock,
        re.S,
    )
    assert package, f"{name} {version} is missing from uv.lock"
    return package.group(0)


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_pii_extra_uses_a_linux_cp313_numpy_wheel():
    """Frozen PII dependencies must install on the compiler-free CPU box."""
    resolved = _requirements_for_linux("pii")
    numpy_version = resolved.get("numpy")
    assert numpy_version and numpy_version != "1.26.4", (
        "the PII extra selected numpy 1.26.4, which has no CPython 3.13 wheel"
    )

    lock = UV_LOCK_PATH.read_text()
    numpy_package = _locked_package(lock, "numpy", numpy_version)
    assert re.search(
        rf"numpy-{re.escape(numpy_version)}-cp313-cp313-manylinux[^\" ]*_x86_64\.whl",
        numpy_package,
    ), f"numpy {numpy_version} has no locked Linux x86_64 CPython 3.13 wheel"

    # A package with only platform-specific wheels is native. Check every one
    # selected by the PII export, rather than assuming numpy is the sole C/Rust
    # extension (spaCy's blis/cymem/preshed/srsly/thinc are native too).
    native: dict[str, list[str]] = {}
    for name, version in resolved.items():
        if not version:
            continue
        wheels = re.findall(r'url = "([^\"]+\.whl)"', _locked_package(lock, name, version))
        if wheels and not any(re.search(r"-py\d+(?:\.py\d+)?-none-any\.whl$", w) for w in wheels):
            native[name] = wheels

    assert native, "the PII export unexpectedly contains no native packages"
    compatible = re.compile(
        r"(?:cp313-cp313|cp3(?:[6-9]|1[0-3])-abi3|py3-none)-manylinux[^/]*_x86_64\.whl$"
    )
    missing = sorted(
        name for name, wheels in native.items() if not any(compatible.search(w) for w in wheels)
    )
    assert not missing, (
        "native packages without a locked Linux x86_64 CPython 3.13-compatible "
        f"wheel: {missing}"
    )

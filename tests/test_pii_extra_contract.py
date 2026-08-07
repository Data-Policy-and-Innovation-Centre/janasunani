"""Dependency contracts for the lightweight PII environment (issue #134)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.markers import Marker


ROOT_DIR = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = ROOT_DIR / "pyproject.toml"
UV_LOCK_PATH = ROOT_DIR / "uv.lock"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT_PATH.read_text())


def test_pii_extra_is_separate_from_the_legacy_numpy_pipeline_contract():
    """Presidio/spaCy must not inherit the non-PII stages' numpy<2 pin."""
    extras = _pyproject()["project"]["optional-dependencies"]
    pii = set(extras["pii"])
    pipeline_core = set(extras["pipeline-core"])

    assert pii == {
        "numpy>=2",
        "presidio-analyzer>=2.2,<3",
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
    assert pii - {"numpy>=2"} <= set(extras["demo"]), (
        "the live demo must retain Presidio/spaCy"
    )
    conflicts = _pyproject()["tool"]["uv"]["conflicts"]
    assert [{"extra": "pii"}, {"extra": "pipeline-core"}] in conflicts
    assert [{"extra": "pii"}, {"extra": "demo"}] in conflicts


def _pii_requirements_for_linux() -> dict[str, str]:
    """Evaluate the frozen PII lock selection for the Python 3.13 CPU box."""
    proc = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--extra",
            "pii",
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
    resolved = _pii_requirements_for_linux()
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

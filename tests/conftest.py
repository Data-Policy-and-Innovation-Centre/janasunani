"""Shared test setup.

macOS/arm64: when spaCy (blis) initializes its OpenMP pool before xgboost
loads (alphabetical test-file order: pii before pipeline), xgboost segfaults.
Single-threaded OMP in tests sidesteps the collision. Production runs are
unaffected — the pipeline's canonical stage order loads xgboost (format
classifier) before spaCy (PII), which does not crash.
"""

import os
import sys

import pytest

if sys.platform == "darwin":
    os.environ.setdefault("OMP_NUM_THREADS", "1")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_oltp_probe: exempt this test from the autouse "
        "_no_ambient_oltp_probe neutralization below, for tests that "
        "genuinely exercise the real _probe_oltp_connection (#119)",
    )


@pytest.fixture(autouse=True)
def _no_ambient_oltp_probe(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Neutralizes `janasunani.inference.service._probe_oltp_connection` for
    every test, so `preflight()` never opens a real database connection.

    `_oltp_check()` (aab4d8e/c97bdb6) calls the real, timeout-bounded
    `_probe_oltp_connection` whenever `OLTP_DB_URL` resolves to something
    explicit -- a shell-exported env var, or a root `.env` via `Settings`.
    That live probe is the CLI's intended behavior (`janasunani-demo-
    preflight`), not pytest's: AGENTS.md/tests/README.md are explicit that
    this suite must never point at production Postgres, and most tests that
    call `preflight()` are exercising something unrelated (model artifacts,
    OCR binaries) and never stub the probe themselves. The probe is
    read-only, so this is not the table-dropping hazard those docs warn
    about elsewhere -- the actual problem is narrower: a routine `pytest`
    run silently following whatever `OLTP_DB_URL` a developer's own `.env`
    happens to name, connecting to it (or eating the 5s timeout) for tests
    that have nothing to do with OLTP, with no signal that it happened.

    Autouse, here in the top-level conftest, rather than patched only at
    today's call sites: a new test written next month that calls
    `preflight()` inherits this by construction, without needing to know to
    stub the probe itself. `test_preflight_never_opens_a_real_connection_
    even_with_ambient_oltp_db_url` (tests/test_inference_live_builder.py)
    proves this holds even when OLTP_DB_URL is set in the environment.

    Tests that genuinely want the real `_probe_oltp_connection` -- its own
    direct tests, exercising a throwaway sqlite file or a deliberately
    refused loopback connection -- opt out with `@pytest.mark.real_oltp_probe`.
    A test's own explicit `monkeypatch.setattr(service,
    "_probe_oltp_connection", ...)` for a specific fake outcome (success or
    a chosen failure) does not need the marker: it runs after this fixture's
    setup and overrides it the same way any later `monkeypatch.setattr` call
    on the same target does.
    """
    if request.node.get_closest_marker("real_oltp_probe"):
        yield
        return
    try:
        from janasunani.inference import service
    except Exception:
        # service (or an optional dependency of it) is not importable in
        # this test environment -- nothing to neutralize, and nothing that
        # could reach a real database either.
        yield
        return
    monkeypatch.setattr(
        service,
        "_probe_oltp_connection",
        lambda url, timeout=service._OLTP_PROBE_TIMEOUT_S: None,
    )
    yield

"""Tests for janasunani.pipeline.cli: argument parsing (already covered in
test_pipeline.py) and main()'s dispatch/validation, which was previously
untested."""

import pytest

from janasunani.pipeline import cli
from janasunani.pipeline.config import PipelineConfig


def test_main_init_db_calls_initialize_database(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "pipeline.sqlite"
    calls = []
    monkeypatch.setattr(cli, "initialize_database", lambda p: calls.append(p))
    monkeypatch.setattr("sys.argv", ["prog", "init-db", "--db", str(db_path)])

    assert cli.main() == 0
    assert calls == [db_path]
    assert f"Initialized database: {db_path}" in capsys.readouterr().out


def test_main_run_builds_config_and_calls_run_pipeline(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "run_pipeline", lambda config: captured.setdefault("config", config))
    monkeypatch.setattr(
        "sys.argv",
        [
            "prog",
            "run",
            "--input",
            str(tmp_path / "in"),
            "--db",
            str(tmp_path / "p.sqlite"),
            "--models",
            str(tmp_path / "models"),
            "--stages",
            "ocr_extraction",
            "--ocr-engine",
            "pytesseract",
            "--worker-id",
            "1",
            "--num-workers",
            "3",
        ],
    )

    assert cli.main() == 0
    config = captured["config"]
    assert isinstance(config, PipelineConfig)
    assert config.stages == ("ocr_extraction",)
    assert config.worker_id == 1
    assert config.num_workers == 3
    assert config.ocr_engine == "pytesseract"


def test_main_run_rejects_num_workers_below_one(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_pipeline", lambda config: pytest.fail("should not run"))
    monkeypatch.setattr(
        "sys.argv",
        ["prog", "run", "--db", str(tmp_path / "p.sqlite"), "--num-workers", "0"],
    )
    with pytest.raises(SystemExit, match="--num-workers must be >= 1"):
        cli.main()


def test_main_run_rejects_worker_id_out_of_range(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_pipeline", lambda config: pytest.fail("should not run"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "prog",
            "run",
            "--db",
            str(tmp_path / "p.sqlite"),
            "--worker-id",
            "5",
            "--num-workers",
            "2",
        ],
    )
    with pytest.raises(SystemExit, match=r"--worker-id \(5\) must be in \[0, --num-workers=2\)"):
        cli.main()

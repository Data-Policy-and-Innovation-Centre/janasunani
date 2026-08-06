"""Tests for scripts/check_dvc_lock.py.

Synthetic dvc.yaml / changed-files only; no DVC remote or data needed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("check_dvc_lock")


class TestIsDepMatch:
    def test_exact_file_match(self):
        assert check._is_dep_match("janasunani/pipeline/pipeline.py", "janasunani/pipeline/pipeline.py")

    def test_directory_prefix_match(self):
        assert check._is_dep_match("janasunani/pipeline/stages/format_classifier/model.py", "janasunani/pipeline/stages/format_classifier")

    def test_directory_with_trailing_slash(self):
        assert check._is_dep_match("janasunani/pipeline/stages/format_classifier/model.py", "janasunani/pipeline/stages/format_classifier/")

    def test_not_a_match(self):
        assert not check._is_dep_match("janasunani/pipeline/dedup.py", "janasunani/pipeline/stages/pii_tagger.py")
        assert not check._is_dep_match("janasunani/pipeline/dedup.py", "janasunani/pipeline/stages/format_classifier")
        assert not check._is_dep_match("janasunani/olap/materialize.py", "janasunani/pipeline/pipeline.py")

    def test_no_false_prefix(self):
        # dedup.py should not match dedup_index.py prefix
        assert not check._is_dep_match("janasunani/pipeline/dedup_index.py", "janasunani/pipeline/dedup.py")

    def test_exact_dep_not_prefix_of_longer(self):
        assert not check._is_dep_match("janasunani/pipeline/pipeline.py.bak", "janasunani/pipeline/pipeline.py")


class TestParseDeps:
    def test_parses_new_narrowed_deps(self, tmp_path):
        yaml_text = """
stages:
  pipeline-sample:
    deps:
      - data/raw/documents-sample
      - models/format_classifier/page_split_v3.0_doc_split.pkl
      - janasunani/pipeline/pipeline.py
      - janasunani/pipeline/stages/format_classifier
      - janasunani/pipeline/stages/pii_tagger.py
    outs:
      - data/processed/pipeline-sample.sqlite
"""
        p = tmp_path / "dvc.yaml"
        p.write_text(yaml_text)
        deps = check._parse_deps_via_yaml(p)
        # Should find all 5 deps via yaml or regex fallback
        assert "janasunani/pipeline/pipeline.py" in deps
        assert "janasunani/pipeline/stages/format_classifier" in deps

    def test_covers_materialize_stage(self, tmp_path):
        yaml_text = """
stages:
  materialize:
    deps:
      - data/oltp/janasunani.db
      - janasunani/olap/materialize.py
    outs:
      - data/interim/complaints.parquet
"""
        p = tmp_path / "dvc.yaml"
        p.write_text(yaml_text)
        deps = check._parse_deps_via_yaml(p)
        assert "janasunani/olap/materialize.py" in deps


class TestMainLogic:
    def _run(self, tmp_path, dvc_yaml_text, changed, lock_changed):
        p = tmp_path / "dvc.yaml"
        p.write_text(dvc_yaml_text)
        # Mock _changed_files by passing via stdin simulation: we test the core
        # matching logic directly
        deps = check._parse_deps_via_yaml(p)
        matched = []
        for c in changed:
            for dep in deps:
                if check._is_dep_match(c, dep):
                    matched.append(c)
                    break
        lock_in_changed = "dvc.lock" in changed if lock_changed is None else lock_changed
        # Replicate main's decision
        if matched and not lock_in_changed:
            return 1
        return 0

    def test_pipeline_file_without_lock_fails(self, tmp_path):
        yaml_text = """
stages:
  pipeline-sample:
    deps:
      - janasunani/pipeline/pipeline.py
      - janasunani/pipeline/stages/format_classifier
      - janasunani/pipeline/stages/pii_tagger.py
"""
        assert self._run(tmp_path, yaml_text, ["janasunani/pipeline/stages/pii_tagger.py"], False) == 1

    def test_pipeline_file_with_lock_passes(self, tmp_path):
        yaml_text = """
stages:
  pipeline-sample:
    deps:
      - janasunani/pipeline/stages/pii_tagger.py
"""
        assert self._run(tmp_path, yaml_text, ["janasunani/pipeline/stages/pii_tagger.py", "dvc.lock"], True) == 0

    def test_format_classifier_subfile_without_lock_fails(self, tmp_path):
        yaml_text = """
stages:
  pipeline-sample:
    deps:
      - janasunani/pipeline/stages/format_classifier
"""
        assert self._run(tmp_path, yaml_text, ["janasunani/pipeline/stages/format_classifier/model.py"], False) == 1

    def test_unrelated_file_without_lock_passes(self, tmp_path):
        yaml_text = """
stages:
  pipeline-sample:
    deps:
      - janasunani/pipeline/stages/pii_tagger.py
      - janasunani/pipeline/stages/format_classifier
"""
        # dedup.py is NOT a dep after narrowing, so no failure
        assert self._run(tmp_path, yaml_text, ["janasunani/pipeline/dedup.py"], False) == 0
        assert self._run(tmp_path, yaml_text, ["janasunani/pipeline/redact_grievance.py"], False) == 0
        assert self._run(tmp_path, yaml_text, ["janasunani/pipeline/dedup_index.py"], False) == 0

    def test_no_changed_files_passes(self, tmp_path):
        yaml_text = """
stages:
  pipeline-sample:
    deps:
      - janasunani/pipeline/pipeline.py
"""
        assert self._run(tmp_path, yaml_text, [], False) == 0

    def test_cli_with_stdin_mode(self, tmp_path, capsys):
        yaml_text = """
stages:
  pipeline-sample:
    deps:
      - janasunani/pipeline/stages/pii_tagger.py
"""
        p = tmp_path / "dvc.yaml"
        p.write_text(yaml_text)
        # Simulate stdin mode: changed files via stdin, lock not present -> should exit 1
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, str(_SCRIPTS / "check_dvc_lock.py"), "--stdin", "--dvc-yaml", str(p)],
            input="janasunani/pipeline/stages/pii_tagger.py\n",
            text=True,
            capture_output=True,
        )
        assert proc.returncode == 1
        # With lock, passes
        proc2 = subprocess.run(
            [sys.executable, str(_SCRIPTS / "check_dvc_lock.py"), "--stdin", "--dvc-yaml", str(p)],
            input="janasunani/pipeline/stages/pii_tagger.py\ndvc.lock\n",
            text=True,
            capture_output=True,
        )
        assert proc2.returncode == 0

    def test_new_narrowed_deps_are_detected(self, tmp_path):
        yaml_text = Path("/tmp/janasunani-fix-87-work/dvc.yaml").read_text()
        p = tmp_path / "dvc.yaml"
        p.write_text(yaml_text)
        deps = check._parse_deps_via_yaml(p)
        assert "janasunani/pipeline/pipeline.py" in deps
        assert "janasunani/pipeline/stages/format_classifier" in deps
        assert "janasunani/pipeline/stages/ocr_extraction" in deps
        assert "janasunani/pipeline/stages/pii_tagger.py" in deps
        # Whole directory should NOT be a dep anymore
        assert "janasunani/pipeline" not in deps

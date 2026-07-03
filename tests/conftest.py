"""Shared test setup.

macOS/arm64: when spaCy (blis) initializes its OpenMP pool before xgboost
loads (alphabetical test-file order: pii before pipeline), xgboost segfaults.
Single-threaded OMP in tests sidesteps the collision. Production runs are
unaffected — the pipeline's canonical stage order loads xgboost (format
classifier) before spaCy (PII), which does not crash.
"""

import os
import sys

if sys.platform == "darwin":
    os.environ.setdefault("OMP_NUM_THREADS", "1")

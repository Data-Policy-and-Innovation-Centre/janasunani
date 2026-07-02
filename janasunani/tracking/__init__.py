"""Experiment/model tracking: local file-based MLflow tracking + model registry,
dual-tracked with DVC (MLflow owns run/metric/registry metadata; DVC versions the
artifact bytes, referenced from MLflow via path + content-hash tags)."""

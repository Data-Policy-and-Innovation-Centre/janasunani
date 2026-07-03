# Format classifier model

The trained classifier `.pkl` file is in this directory.

Expected: a pickle file containing a dict with these keys:
- `classifiers`     — per-target trained estimators
- `label_encoders`  — per-target sklearn LabelEncoders (or MultiLabelBinarizer for language)
- `feature_columns` — list of feature names in training order
- `lang_encoder`    — LabelEncoder for predominant_lang
- `scaler`          — StandardScaler fitted on training features
- `version`         — string version tag

The pipeline picks the first `.pkl` it finds here (sorted), so if you
have multiple versions, name them so the right one sorts first
(e.g. by suffixing a date or version number).

Large model files should NOT be checked into git — see `.gitignore`.

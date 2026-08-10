# Model and parameter registry

This is the human-readable model inventory. When activated, the serving source
of truth is an immutable `janasunani.release/v1` manifest produced by
`janasunani-model-release`. The materializer downloads the artifact attached to
an approved MLflow model version, requires DVC provenance tags, hashes the
downloaded bytes and stores them locally. Existing DVC mirrors remain the final
serving fallback; the materializer does not itself pull or verify bytes from DVC.

## Current and candidate models

| Stage | Incumbent / candidate | Artifact or endpoint | Important parameterization | Evidence status |
|---|---|---|---|---|
| Format | `format_classifier_v3` | Exact operator/release artifact or an unambiguous one-file DVC mirror | Hand-engineered image features; multiple pickles fail closed instead of selecting lexicographically | Incumbent; no governed scorecard |
| Page type | ViT `DPIC-Pipeline/vit_type_classifier` | Prefer DVC `models/page_type_classifier/vit_type_classifier`; public ID is legacy fallback | RGB image, argmax label | Incumbent; no governed scorecard |
| OCR, CPU | pytesseract | Local binary | Sauvola preprocessing; language/config recorded by run | Incumbent; Sarvam paired comparator |
| OCR, GPU candidate | `deepseek-ai/DeepSeek-OCR` | Must be locally mirrored and revision-pinned for production | BF16, eager attention, prompt `Free OCR. Oriya or English.`, base 1024, image 640, crop on, repetition 1.2, no-repeat 3, temperature 0.7 | Candidate; no governed scorecard |
| OCR/extract hosted | Sarvam Vision 1.5 | Authorized hosted endpoint | Then-published list price: Digitise ₹0.50/page, Extract ₹1/page; actual billing unavailable; schema `v1`; 10 submissions/min; 5 s polling; 3 attempts | Cached divergence/coverage only; no latency or accuracy claim |
| PII | Presidio + project recognizers | Local code/model dependencies | Entity-specific recognizers; redacted text is authoritative downstream | Recall measured; precision and language splits incomplete |
| Summary | `facebook/bart-large-cnn` family | Pinned local release/DVC artifact; remote ID requires explicit development opt-in | Input 1024 tokens, output 20–100, 4 beams | Incumbent; current quality unmeasured |
| Category incumbent | MuRIL classifier | DVC `models/categorizer` | English-only gate; grievance + redacted page text | Benchmark pending governed gold |
| Category CPU candidate | word+character hashing + probabilistic SGD | Checksummed local artifact (to be promoted only after gold evaluation) | Validation-selected alpha and abstention; group-disjoint split | Harness implemented; no release claim |
| Actionability review candidate | word+character TF-IDF logistic regression | Aggregate evaluation evidence only; binary candidate is intentionally not exported into the five-class serving slot | Binary actionable-vs-review objective; validation-selected `C=1`, threshold `0.4226656541`; validation: 91.67% accuracy, 100% review recall, 66.67% precision, 10.00% actionable-review rate (n=60); five-class reasons retained only for evaluation breakdowns; weak labels train-only | Frontier-adjudicated development test: 91.67% accuracy, 100% review recall, 73.68% precision (n=60); no `out_of_scope` support; not release-eligible or serving-compatible |
| Actionability encoder probe | frozen local MuRIL + balanced logistic probe | Existing local categorizer bytes, fingerprint `sha256:8a94d0e…` | Masked-mean pooling, L2 normalization, 256 tokens, frozen encoder, validation-selected `C=10`, threshold `0.5500819087`; validation: 90.00% accuracy, 80.00% review recall, 66.67% precision, 8.00% actionable-review rate (n=60) | Development diagnostic: 81.67% accuracy / 64.29% review recall; did not beat TF-IDF |
| Actionability English diagnostic | frozen `all-MiniLM-L6-v2` revision `c9745ed…` + balanced logistic probe | Cached local snapshot only | Masked-mean pooling, L2 normalization, 256 tokens, frozen encoder, validation-selected `C=10`, threshold `0.3047001064`; validation: 90.00% accuracy, 80.00% review recall, 66.67% precision, 8.00% actionable-review rate (n=60) | Development diagnostic: 83.33% accuracy / 71.43% review recall; English-oriented and did not beat TF-IDF |
| Spam guardrail | `spam-v1.1-bounded` rules | Local code | Bounded advisory score; no auto-reject | Exact regression evidence only |
| Routing candidate | empirical-Bayes destination incidence | Checksummed local artifact to be added | Category+district live; validation-selected smoothing/history; hierarchical backoff | Developmental chronological holdout |

## What every release entry pins

Local release entries require a name, provider, immutable resolved version,
trust tier, artifact path and SHA-256; materialized MLflow entries additionally
require DVC path/hash tags. Hosted entries instead pin an endpoint and observed
model ID. Parameters, schemas, benchmark run, dataset snapshot and gold-set ID
are supported provenance fields and must be populated for a reviewed release,
although the schema currently permits some of them to be absent. Sarvam calls
record safe returned provider/model metadata. Scheduled comparisons of that
metadata against the release entry are a planned control, not yet implemented.

## Serving different versions safely

1. Train/evaluate and log the run to MLflow with its DVC and benchmark
   provenance.
2. Review the frozen scorecard; alias promotion is a separate operator action.
3. Copy [`deploy/model-release.example.json`](../deploy/model-release.example.json),
   replace every review placeholder, then run
   `janasunani-model-release materialize --spec <approved.json>
   --release-root models/releases --activate`. It resolves each alias to an
   immutable version, requires DVC tags, downloads to a versioned directory,
   hashes the bytes and atomically activates the manifest.
4. Serving resolves `JANASUNANI_<NAME>_ARTIFACT`, then the active manifest,
   then the DVC mirror. It imports no MLflow client and makes no network call.
5. Roll back with `janasunani-model-release activate
   models/releases/<old-release>/release-manifest.json`. Activation revalidates
   every local checksum before switching the pointer.

The release/materialization code and local resolution order are implemented;
no reviewed production manifest has yet been activated. An operator override
remains first for recovery, but health/preflight must make that override visible
before the registry platform is considered complete.

The actionability serving seam requires probabilities for all five taxonomy
labels. The current frontier benchmark trained a binary review classifier
because the development sample had no defensible `out_of_scope` example.
Consequently it is evidence about candidate discrimination, not an artifact
that can be promoted. A serving artifact requires officer-confirmed support for
all five classes and a newly frozen release test.

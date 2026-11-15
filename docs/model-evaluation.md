# Model evaluation

The deployed shared two-computer model is `tray-two-device-v1`. Its
computer-grouped selection CV reached 87.88% accuracy on the original PC and
96.77% on the second PC, with 0/32 genuine windows falsely flagged. Overall
impostor recall was 84.38%, below the earlier 85% recall target. Both computer
pairs were used in model/cutoff selection and final fitting, so these are not
untouched final-test results. See the [model card](multimodal-model.md).

## Archived keyboard pilot: model-selection cross-validation

The existing real dataset contains 60 genuine windows from four sessions and 30
second-user windows from two sessions. It represents one enrolled person and one
consenting second person on one device. Datasets, per-window predictions, deployment
identities, and credentials remain outside Git.

Two-fold stratified group cross-validation keeps each complete session in one
fold, fits preprocessing and the classifier on training folds only, and scores
each of the 90 windows once out of fold. The selected classifier is logistic
regression (`C=0.1`, balanced weights), using hold-time coefficient of variation
and press overlap with `log1p` and standard scaling. Each fold's cutoff comes from
its genuine training scores at the empirical 98th percentile.

| Selection CV metric | Result |
| --- | --- |
| Accuracy | 96.67% (87/90 correct) |
| Balanced accuracy | 95.83% |
| Second-user precision | 96.55% |
| Second-user recall | 93.33% (28/30 detected) |
| F1 | 0.9492 |
| Pooled threshold-centered margin AUROC | 0.9928 |
| Mean per-fold AUROC | 0.9933 |
| Average precision | 0.9861 |
| Genuine false rejection rate | 1.67% (1/60) |
| Second-user false acceptance rate | 6.67% (2/30) |

The final saved model was subsequently fitted on all 90 labeled windows remotely.
Its training diagnostics are separate from the out-of-fold metrics above.
Feature/model selection used these CV results, so the estimates can be optimistic.
They are not an untouched independent final test, a population false-alarm bound,
or evidence of performance on other users or devices. Window counts are correlated
within sessions. Checked-in synthetic samples only test integration.

## Separate baseline-comparison workflow

The comparison tool is `python -m backend.ml.evaluate`. Run it only on the
designated training host with `ML_ALLOW_TRAINING=1`, using the locked backend
environment. It fits Isolation Forest and a median/MAD baseline on identical
training sessions, calibrates separate thresholds on a genuine validation session,
and scores untouched genuine holdout sessions plus optional consenting second-user
sessions. It does not change or activate the production model.

From the repository root on that host:

```sh
uv run --project backend --locked python -m backend.ml.evaluate genuine.jsonl \
  --validation-session SESSION_B --holdout-session SESSION_C \
  --impostor guest.jsonl --data-kind consented --output docs/model-evaluation.md
```

Replace session placeholders with collected IDs. All remaining genuine sessions
are training sessions. Repeat `--holdout-session` for multiple holdout sessions.
Inputs must be aggregate-only schema-v1 JSONL from one device, with unique
session/sequence pairs. Guest sessions must have different IDs and use that same
device. Training and validation require enrollment mode and the existing minimum
of 20 and 10 activity-rich windows respectively. Obtain consent before collection
and processing; keep datasets outside the repository.

Omit `--impostor` when unavailable; FAR and recall will remain unavailable rather
than zero. Use `--data-kind synthetic` for generated fixtures. The label is an
operator declaration, not automatic consent or provenance verification.

The generated report replaces this file and includes thresholds, selected features,
baseline parameters, dependency versions, split session IDs, window counts, and
insufficient-data counts. Review session IDs for private information before sharing
the report. Fitted Isolation Forest artifacts are temporary and removed after the
comparison. Baseline statistics are included for reproducibility.

FRR and FAR are window-level rates over usable windows. Low-activity windows are
excluded and counted separately. Both thresholds use the genuine calibration score
at rank `ceil(0.95 * n)`, with ties treated as normal. This limits empirical calibration
alerts to at most 5%; it does not guarantee the same rate on fresh sessions.
The baseline averages absolute robust z-scores using
1.4826 times training MAD; varying features with zero MAD use training standard
deviation. Near-constant features use the same training-only mask as Isolation
Forest. No additional scaler is needed for Isolation Forest.

Small or overlapping window sets are correlated, and their window counts are not
independent sample counts. Evaluate separate real sessions before making detection
claims. User-switch latency and session-level verification remain separate work
in issue #141.

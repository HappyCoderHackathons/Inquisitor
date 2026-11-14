# Model evaluation

No real-data evaluation has been recorded. FRR, FAR, recall, and detection latency
are unavailable. Checked-in sample windows are synthetic integration fixtures and
do not establish detection accuracy.

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
excluded and counted separately. Both thresholds use the validation 95th percentile
with ties treated as normal. The baseline averages absolute robust z-scores using
1.4826 times training MAD; varying features with zero MAD use training standard
deviation. Near-constant features use the same training-only mask as Isolation
Forest. No additional scaler is needed for Isolation Forest.

Small or overlapping window sets are correlated, and their window counts are not
independent sample counts. Evaluate separate real sessions before making detection
claims. User-switch latency and session-level verification remain separate work
in issue #141.

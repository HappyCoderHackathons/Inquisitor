# Archived model weights

These numeric JSON artifacts are retired from deployment. The active artifact is
[`backend/artifacts/tray-two-device-v1.json`](../backend/artifacts/tray-two-device-v1.json).
Archived versions remain readable for comparison and legacy-format regression tests;
they are not loaded automatically or selected as active models.

- `labeled-overlap-v1.json`: original keyboard-only labeled pilot.
- `labeled-overlap-v2.json`: the same pilot with genuine-baseline explanations.
- `tray-timing-v1.json`: initial two-feature tray candidate.
- `tray-timing-v2.json`: six-feature candidate fitted on the original computer only.

These are learned numeric weights, not raw collection data or service-created
joblib files. Deployment identities, credentials, and model-store directories
remain private on the designated host. Historical accuracy estimates apply only
to their documented datasets and evaluation roles.

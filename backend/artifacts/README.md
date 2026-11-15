# Deployed numeric weights

`tray-two-device-v1.json` is the deployed shared keyboard/mouse model fitted on
64 windows from four original sessions: the same genuine and impostor users on
two PCs. Its canonical SHA-256 is
`de37af16d102b847bb4651a081c8d79d6906ca2f6249e58d8bab0d600d246567`.

See the [model card](../../docs/multimodal-model.md) for schema-v4 input,
selection-CV results, and limitations. The artifact contains learned coefficients,
scaler parameters, the cutoff, genuine-baseline statistics, and summary counts.
It contains no raw events, credentials, or account/device/session identifiers.
Inference does not fit a model or load an uploaded pickle.

Prior numeric models are retired under [`archive_models/`](../../archive_models/)
and are not selected automatically. Legacy-format tests read those archived files.
The hash uses parsed JSON with sorted keys and compact separators, making it
independent of indentation and line endings. The API exposes the same hash.

# Published weights

`tray-two-device-v1.json` is a shared keyboard/mouse candidate fitted on 64 windows
from four original sessions: the same genuine and impostor users on two PCs.
The previous published model remains deployed. Its canonical SHA-256 is
`de37af16d102b847bb4651a081c8d79d6906ca2f6249e58d8bab0d600d246567`.
See the [model card](../../docs/multimodal-model.md) for features, model-selection
results, limitations, and required schema-v4 input. The artifact contains numeric
weights and summary counts, with no raw events, credentials, or device/account IDs.

`labeled-overlap-v1.json` is the selected per-user logistic regression fitted on
60 genuine and 30 consenting second-user aggregate windows. It includes all numeric
parameters required for inference: derived feature order, scaler mean/scale,
coefficients, intercept, and the fixed threshold. No fitting is required to load it.

These trained numeric weights are intentionally published. Raw enrollment data,
personal/deployment identifiers, API tokens, and SSH credentials are not included.
The model is a two-person pilot, not a general-purpose identity classifier. See
[model evaluation](../../docs/model-evaluation.md) for selection-CV results and limits.

`labeled-overlap-v2.json` is the deployed version. It was retrained on the model
host from the same 90 windows and has identical weights, scaler, and threshold. It
adds `genuine_baseline`, the median and 1.4826 x MAD of the 60 genuine windows'
derived features, which alert explanations use for per-feature robust z-scores.

| Version | Canonical SHA-256 |
| --- | --- |
| v1 | `27906b92e1e795dd18b8aeaa95b47bc8a66579684f63ee9aecd04f16443a4d6f` |
| v2 (deployed) | `d947af06823eaf65021ab817a790c929091414ad70c2f8c26c3411c51bc112b4` |

Each hash covers parsed JSON serialized with sorted keys and compact separators, so line
endings and indentation do not change the identifier. The API exposes the same hash.

Create another version using the trusted-artifact export command in the
[deployment guide](../deploy/README.md). Keep earlier versions for rollback.

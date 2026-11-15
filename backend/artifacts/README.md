# Published weights

`labeled-overlap-v1.json` is the selected per-user logistic regression fitted on
60 genuine and 30 consenting second-user aggregate windows. It includes all numeric
parameters required for inference: derived feature order, scaler mean/scale,
coefficients, intercept, and the fixed threshold. No fitting is required to load it.

These trained numeric weights are intentionally published. Raw enrollment data,
personal/deployment identifiers, API tokens, and SSH credentials are not included.
The model is a two-person pilot, not a general-purpose identity classifier. See
[model evaluation](../../docs/model-evaluation.md) for selection-CV results and limits.

The artifact's canonical SHA-256 is
`27906b92e1e795dd18b8aeaa95b47bc8a66579684f63ee9aecd04f16443a4d6f`.
It hashes parsed JSON serialized with sorted keys and compact separators, so line
endings and indentation do not change the identifier. The API exposes the same hash.

Create another version using the trusted-artifact export command in the
[deployment guide](../deploy/README.md). Keep earlier versions for rollback.

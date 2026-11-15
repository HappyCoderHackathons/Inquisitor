# Threat model

This section states what Inquisitor is designed to resist, what it does not claim, and
where residual risk remains. It complements [architecture decisions](architecture.md) and
[privacy](privacy.md).

## Assets

- Account credentials and device pairing tokens
- Aggregate keyboard and optional mouse timing features per user
- Per-user trained model artifacts and inference results
- Hosted relay and dashboard session integrity

## Adversaries and scenarios

| Threat | Description | Mitigation |
| --- | --- | --- |
| Forgery | Another person replays or synthesizes windows without matching behavior | Device-scoped auth, schema validation, per-user models; no identity guarantee |
| Replay | Attacker resubmits stored aggregate windows | Per window device/session/sequence uniqueness; no raw event capture |
| Local tampering | Malware alters the agent or browser before upload | Visible agent control, HTTPS/WSS, server-side validation; client compromise is accepted risk |
| Similar users | Consenting second person with overlapping timing | Pilot uses consenting second-user data only; other users are out of scope |
| Data leakage | Sensitive content leaves the device | No key identities, coordinates, or ordered events in storage or ML payloads |

## Residual and accepted risks

- A compromised Windows session can send crafted aggregates until revocation.
- Shared PCs without lock-step user separation can blur session boundaries.
- Training metrics on consenting second-user sessions do not establish population accuracy.
- Hosted and ML components have not completed full production hardening or pen testing.

## Operator controls

Users can pause the agent. The ML service and local arcade support account data deletion
and enrollment reset; the hosted dashboard does not expose deletion or device revocation
yet. Monitoring retention is configurable on the ML service.
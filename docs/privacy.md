# Privacy

Inquisitor collects aggregate timing features, not typed content or pointer targets.

## What is collected

- Keyboard timing statistics per rolling window (press counts, hold variability, overlap)
- Optional mouse motion and click timing aggregates when the user enables mouse collection
- Account email and device metadata needed for pairing and authorization

## What is not collected

- Key identities, typed characters, or ordered key sequences
- Absolute pointer coordinates, click targets, or window titles
- Raw event logs outside transient in-memory pairing

## Storage and retention

Enrollment windows support model training. Monitoring windows, inference results, and
alerts follow the configured retention period on the ML service. `DELETE /me/data` on the
ML service and the local arcade removes an account's windows, results, alerts, and models.
The hosted dashboard does not expose deletion yet.

## Remote processing

Training and inference run on the designated remote ML host. Enrollment windows leave the
user's device only after explicit onboarding. Credentials for that host never ship in the
client binary.
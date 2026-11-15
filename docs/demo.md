# Demo runbook

## Before judging

1. Confirm Better Auth, MongoDB, and ML service environment variables on the hosted app.
2. Start the Node auth service and Flask app per [README](../README.md).
3. Pair the Windows agent with a fresh code and finish arcade onboarding rounds.
4. Train the model and verify monitoring returns `NORMAL` on your own input.
5. Prepare a consenting second-user segment or labeled replay fallback per [model evaluation](model-evaluation.md).

## Live demo (about 2 to 3 minutes)

1. Sign in and show device paired / monitoring active.
2. Explain aggregate-only capture in one sentence.
3. Play or type briefly to show a stable `NORMAL` state.
4. Switch to the consenting second user or replay clearly labeled windows.
5. Show an `UNUSUAL` alert and read the short explanation text.
6. Pause the agent to show user control.

## Fallback

If the network or ML host fails, use the arcade replay path with the replay banner visible.
State that replay proves integration, not field accuracy.

## After demo

Revoke test device tokens or delete demo account data if the machine is shared.
# Demo runbook

## Before judging

Work through this checklist on the demo machine, in order.

Environment:

- [ ] Laptop on mains power, with sleep and screen lock disabled for the session.
- [ ] Network checked: the hosted app loads over HTTPS, and a second network (hotspot) is ready.
- [ ] Better Auth, MongoDB, and ML service environment variables set on the hosted app.
- [ ] Node auth service and Flask app started per the [README](../README.md). Sign-in works.
- [ ] ML host reachable from the hosted app (scoring a window returns a result, not `502`).

Enrollment and test data:

- [ ] Windows agent paired with a fresh code. Keep the device ID and token from the
      `POST /api/agent/pair` response for the replay fallback.
- [ ] Arcade onboarding rounds finished, model trained, and monitoring returns `NORMAL`
      on your own input.
- [ ] A held-out genuine session, recorded after training and not used to fit it.
- [ ] A consenting second user available in person, or their aggregate-only replay file
      saved outside the repository (see [replaying recorded windows](../sample-data/README.md#replaying-recorded-windows)).
- [ ] One raw window JSON ready to show, such as the first line of the replay file or the
      `valid` example in [`shared/fixtures/feature-window-v1.json`](../shared/fixtures/feature-window-v1.json).
- [ ] The replay command below tested once against the hosted app, with its output saved.

## Live demo (about 2 to 3 minutes)

1. Sign in and show device paired / monitoring active.
2. Explain aggregate-only capture in one sentence, and show the raw window JSON.
3. Play or type briefly to show a stable `NORMAL` state.
4. Switch to the consenting second user or replay clearly labeled windows.
5. Show an `UNUSUAL` result and read the short explanation text.
6. Pause the agent to show user control.

## Fallback

If the network or ML host fails, use the arcade replay path with the replay banner visible.
State that replay proves integration, not field accuracy.

## After demo

Revoke test device tokens or delete demo account data if the machine is shared.

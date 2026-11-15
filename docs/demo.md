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
4. Switch to the consenting second user, or run the replay fallback below.
5. Show an `UNUSUAL` result and read the short explanation text.
6. Pause the agent to show user control.

## Fallback

If live capture or the agent fails but the hosted app and ML host are up, replay recorded
windows through the normal scoring path:

```powershell
$env:INQUISITOR_DEVICE_TOKEN = "<token from pairing>"
uv run --project backend python scripts/replay_windows.py C:\demo\guest.jsonl `
  --url https://<hosted app> --device-id <device ID from pairing> --speed 4
```

Every output line reads `REPLAY (recorded windows, not live input)`. Keep the terminal
visible and say that replay proves integration, not live detection or field accuracy.
At `--speed 4`, 30-second windows post about every 8 seconds; check the timing during rehearsal.

If the hosted app or ML host is down, replay cannot score either. Switch to the hotspot
and retry. If that fails, present the recorded rehearsal output and the
[model evaluation](model-evaluation.md) report, and say they are recorded results.

## After demo

Revoke test device tokens or delete demo account data if the machine is shared.

# Deployed shared two-computer model

`tray-two-device-v1` is one logistic regression model for Ian (genuine) versus
Colin (impostor), fitted on both computers. It uses no user, device, file, session,
or window-position identifiers as classifier inputs. It is the deployed published
and active model for the configured pilot identities. Earlier numeric models are
retired under `archive_models/` in the repository and are not selected by the service.

## Data and input handling

The original pair of recordings is on Ian's PC; the second pair is on Colin's
PC. Each file is one original collection session, giving four sessions total.
The operator confirmed one physical keyboard was used for the second pair.
Both second-PC files contain two logical keyboard streams. Every secondary
press is within 20 ms of a primary press, with median delays around 6.5 ms.
The larger stream was selected explicitly to avoid counting shadow reports twice.

| Recording | Selected keyboard source | Complete windows |
| --- | --- | --- |
| user-ian-device-ian.csv | kbd0 | 17 |
| user-colin-device-ian.csv | kbd0 | 16 |
| user-ian-device-colin.csv | kbd1 | 15 |
| user-colin-device-colin.csv | kbd0 | 16 |

Convert locally, passing the paired or privately configured PC identity:

```powershell
uv run --project backend --locked python -m backend.ml.tray_csv capture.csv --device-id AUTHORIZED_DEVICE --keyboard-source PRIMARY_SOURCE --mouse --mode enrollment --output .private/windows.jsonl
```

`--mouse` is a separate opt-in. Without it the converter retains schema-v3
keyboard-only behavior. Multiple keyboards require explicit source selection;
missing selected sources, multiple mouse sources, malformed rows, and clock
resets fail rather than being merged silently. Choose the raw-input source for
each real recording, not a source inferred from the person's label.

Schema v4 contains only aggregate numeric features and window metadata. Windows
are nonoverlapping 30-second intervals anchored to the first event; partial tails
are excluded. Motion deltas are summed into 20 ms bins, then reduced to speed
statistics, turning statistics, and path straightness. A turn is counted only
across bins at most 250 ms apart. Speeds retain raw-count units, not pixel units.
Only their coefficient of variation enters the classifier. Uniform scaling of
mouse counts therefore leaves the selected motion features unchanged; this does
not prove invariance to every sensor, polling rate, or acceleration setting.

Inter-press intervals use successive primary-source presses, not release-to-press
`flight_ms`. Holds crossing a window boundary are excluded. A window is usable
with at least 30 presses and completed holds, or at least ten nonzero mouse bins.
All 64 complete windows in this dataset met that rule. There are 32 genuine and
32 impostor windows. No raw controls, ordered events, absolute pointer positions,
or click targets are included in model requests or the exported weights.

## Model and cutoff

The model uses hold-time mean/spread, inter-press mean/spread, their two
coefficients of variation, keyboard press rate, click rate, click-interval
variability, speed variability, turning mean/spread, and path straightness.
It applies `log1p` and a training-fitted scaler, then balanced logistic regression
with `C=3`. The alert cutoff is the empirical 98th percentile of genuine training
scores plus 0.35 genuine training-score standard deviations. An alert requires a
score strictly above this cutoff.

Simple keyboard-only, mouse-only, combined, and nonlinear candidates were
compared. A more conservative cutoff was selected to meet the latest request
for at least 85% accuracy per computer with at most 2% genuine false positives.
The final single model was fitted on all four development recordings remotely.
No fitting occurred on the Windows client. The weights and cutoff are frozen.

## Results and limits

Two computer-grouped folds trained on one PC's complete recording pair and
scored the other PC's complete pair. Scaling and cutoff calculation used only
each fold's training PC. The feature/model/cutoff selection used these results,
so they are selection CV, not an independent final test. The final model's
training metrics happen to have the same aggregate counts as these CV results.

| Computer scored | Accuracy | Genuine false positives | Impostors detected |
| --- | --- | --- | --- |
| Ian's PC | 87.88% (29/33) | 0/17 | 12/16 (75%) |
| Colin's PC | 96.77% (30/31) | 0/15 | 15/16 (93.75%) |
| Combined | 92.19% (59/64) | 0/32 | 27/32 (84.38%) |

Combined precision is 100% and F1 is 91.53%. The observed accuracy and false-positive
limits are met on both development pairs. The earlier 85% recall target is not
met overall, and recall is only 75% on Ian's PC. Lowering false positives increases
missed impostor windows. Zero observed false positives across 32 correlated windows
does not establish a population false-positive rate of 2% or less. Both computers
are now development data; new sessions on each PC are required for independent
validation. Performance on new people, activities, and computers is unverified.

## API and authorization

Training uses the existing `POST /models/train` envelope with genuine `windows`,
`impostor_windows`, `profile: "tray-multimodal"`, and an explicit `device_ids`
allowlist. The caller's device must be in that list, and each enrolled device
needs genuine and impostor examples. Both classes need 20 windows across two
disjoint original sessions. The trusted application backend must verify every
listed device belongs to the authenticated account before forwarding a request;
do not accept a browser-provided device list as authorization.

One owned model ID can score either enrolled device using the existing
`/models/{id}/score` endpoint and can be selected independently for each device
through existing active-model endpoints. Other accounts, unlisted devices, and
window/header device mismatches are rejected. Test data's second-PC identity is
a private evaluation alias, not evidence of completed pairing or provisioned
device credentials. Application pairing and actual agent transport remain separate.

Existing v1/v2/v3 contracts and archived artifacts remain supported by their original
profiles. A multimodal model receiving an older keyboard-only window reports
`INSUFFICIENT_DATA`; it does not invent missing mouse features. Existing score,
threshold, delta, state, and explanation fields remain intact. The published
artifact is bound to its private owned model ID so both allowlisted devices can
use `/model` and `/score`. Training remains
disabled in the serving process. Automatic tray upload, browser calibration
conversion, and end-to-end native monitoring have not been implemented here.

All 37 backend tests passed on the designated host, including training, export,
reload, two-device ownership, reset/deletion, and archived-format compatibility.
Published and active-model scoring matched the saved numeric artifact exactly
on all 64 development windows. Both configured device identities were checked
after restart, and older runtime models were retired to the private host archive.

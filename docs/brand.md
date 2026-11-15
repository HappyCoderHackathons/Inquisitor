# Inquisitor Brand

**Status:** Draft for review

## Brand direction

Inquisitor should feel focused, capable, calm, and respectful of the user's
privacy. The visual identity uses a near-black foundation with purple for
primary actions and green for recurring highlights and positive states. Keep
the interface clear and welcoming rather than ominous or surveillance-themed.

## Color palette

These are proposed starting values. Confirm them against the product UI and
accessibility checks before treating them as final brand colors.

| Token | Value | Use |
| --- | --- | --- |
| Background | `#09070D` | Main page and app background |
| Surface | `#14111A` | Cards, panels, and navigation |
| Raised surface | `#211B2A` | Modals, menus, and elevated controls |
| Border | `#393243` | Dividers and control boundaries |
| Primary purple | `#7C3AED` | Primary buttons, selected controls, and key links |
| Light purple | `#C4B5FD` | Purple text, icons, and highlights on dark surfaces |
| Green accent | `#A3E635` | Recurring highlights, focus details, and positive feedback |
| Light green | `#D9F99D` | Supporting green text and icons on dark surfaces |
| Primary text | `#F7F4FC` | Headlines and body text |
| Secondary text | `#B9B3C4` | Supporting labels and descriptions |
| Error | `#FB7185` | Errors and destructive warnings |
| Caution | `#FBBF24` | Warnings that are not errors |

### Color use

- Keep most of each screen near-black; use purple for primary actions and green
  for recurring highlights and clear interaction cues.
- Use semantic error and warning colors consistently. Do not rely on color
  alone to communicate status; pair it with text, an icon, or another cue.
- Use `#7C3AED` with light text for primary buttons. Use `#C4B5FD` and
  `#A3E635` for foreground details on dark surfaces.
- Check text, focus indicators, hover states, and disabled states for contrast
  before shipping. The proposed palette is not a substitute for testing.

## Typography

Suggested starting type roles, consistent with the arcade prototype:

- **Headings:** Space Grotesk, with a sans-serif fallback.
- **Body and controls:** DM Sans, with a sans-serif fallback.
- **Metrics and technical labels:** DM Mono, with a monospace fallback.

Use readable body sizes and reserve letter-spaced uppercase text for short
labels. Confirm font availability, loading behavior, and licensing before
production use.

## Interface style

- Prefer simple layouts, clear hierarchy, and generous spacing over decorative
  effects.
- Use restrained purple and green highlights with subtle borders to distinguish
  surfaces.
- Keep icons simple and consistent in stroke weight. Do not use an icon as the
  only label for an important action.
- Make keyboard focus visible, provide hover and pressed states, and respect
  reduced-motion preferences.
- Keep opt-in, pause, and exit controls easy to find.

## Voice and product language

- Be direct, calm, and specific. Explain what the product is doing and what the
  user can control.
- Describe results as behavioral signals or verification prompts; do not imply
  that a score proves identity or that the system is infallible.
- Make data collection and processing claims match the actual implementation.
  Avoid blanket terms such as "completely secure" or "nothing is collected"
  unless they have been verified for the relevant feature.

## Logo and imagery

The logo and illustration system have not been defined yet. Create a
recognizable wordmark and a compact mark that remain legible on both near-black
and light surfaces. Prefer original, abstract shapes related to patterns,
signals, or interaction; avoid imagery that suggests surveillance or guaranteed
identity detection.

A draft compact mark lives in `docs/icons/`: a biretta over keystroke timing bars.

| File | Use |
| --- | --- |
| `inquisitor-icon.svg` | App icon on a dark tile; PNGs at 16 to 1024 px in `png/` |
| `inquisitor-mark.svg` | Transparent mark for dark surfaces |
| `inquisitor-mark-light.svg` | Transparent mark for light surfaces |
| `inquisitor-mark-mono.svg` | Single color, inherits `currentColor` |

## Decisions needed to finalize this guide

- Confirm the primary audience and the product surfaces this brand covers
  (desktop app, website, documentation, or all three).
- Approve or adjust the proposed color values after checking them in real screens
  and testing contrast.
- Decide whether a light theme is required in addition to the dark default.
- Choose or commission the wordmark, compact logo, and any product illustrations.
- Confirm production fonts and how they will be delivered.
- Agree on preferred product terms for enrollment, verification, alerts, and
  privacy controls.
- Review all privacy and security wording against implemented behavior.
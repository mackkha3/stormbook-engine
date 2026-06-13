# intake-form — tests to add (later prompt)

No tests in this build (per spec). When tests are added, cover:

## URL parameter parsing
- Missing `client_id` → fatal error renders, form view hidden.
- `color=%23B91C1C` decoded → applied to `--primary`.
- Invalid `color` (e.g. `red`, `#zzz`) → falls back to default.
- Unknown `source` → defaults to `direct`; valid `source` flows through to payload.
- `logo` URL that 404s → logo stays hidden (no broken-image icon).

## Phone formatting & normalization
- Typing `9035550199` reformats to `(903) 555-0199`.
- Pasting `+19035550199` normalizes correctly on submit.
- Backspacing across formatting characters does not lose digits.
- Submit with <10 digits shows inline error, blocks POST.

## Field validation
- First/last name require min 2 chars trimmed (whitespace-only rejected).
- Email empty → valid. Email malformed → inline error.
- Damage type unselected → inline error.
- Photos: >5 files rejected. Non jpg/png rejected. >5MB rejected.

## Payload shape
- `submitPayload` is called with exactly the keys from spec:
  `client_id, source, first_name, last_name, phone, email, address,
   damage_type, photo_count, submitted_at`.
- `phone` is E.164 (`+1XXXXXXXXXX`).
- `submitted_at` is ISO 8601 UTC.
- Empty email → `null`, not empty string.

## Submit behavior
- Mock mode: no network request, payload logged, success screen renders.
- Real mode: POST to `${apiBaseUrl}/intake` with JSON body.
- 12-second timeout via AbortController triggers AbortError path.
- 5xx response shows inline error, re-enables form, preserves field values.
- 4xx response shows inline error.

## Accessibility
- All inputs have associated `<label>`.
- Invalid fields get `aria-invalid="true"` and `aria-describedby` pointed at error text.
- Focus moves to first invalid field on submit.
- Color contrast on text + button + error states hits WCAG AA.

## Tooling suggestion
- Vitest + jsdom for unit tests on `form.js` (extract pure helpers:
  `formatPhoneAsTyped`, `normalizePhoneE164`, `parseParams`, `validate`).
- Playwright smoke for end-to-end: load URL with params → fill → submit
  → success screen.

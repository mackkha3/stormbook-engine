# demo-form

The operator-facing form Mack fills out during a Zoom discovery call. It
POSTs to [`apps/demo-relay`](../demo-relay/), which fires a real SMS to the
phone in his hand while the prospect watches.

Not to be confused with `apps/intake-form` — that's the homeowner-facing
production form. This one has more fields because the operator is
customizing per-prospect live: contractor persona, homeowner details, and
the two appointment slots being offered.

Disposable. Delete this workspace together with `apps/demo-relay` when
pilot #3 signs and Voice AI ships.

## Local development

```bash
npm install                        # from the repo root
npm run dev --workspace @stormbook/demo-form
```

Serves on <http://localhost:4323>. All fields arrive pre-populated
(Mike / Henderson Roofing / Jane / 123 Elm St / Hail / Tue 10:00 AM /
Thu 2:00 PM) except the phone number — type that fresh every time.

Point it at a relay by creating `config.json` next to `config.example.json`:

```json
{ "relayUrl": "http://localhost:3001" }
```

`config.json` is gitignored-by-convention (it holds the environment-specific
URL); `config.example.json` ships the production relay URL and is the
fallback if `config.json` is absent.

## Netlify deployment

1. Netlify → Add new site → Import from Git → this repo.
2. **Base directory**: `apps/demo-form`
   **Build command**: leave empty (static site, no build step)
   **Publish directory**: `apps/demo-form`
3. Deploy. Netlify serves `index.html` at the generated domain.
4. Domain settings → Add custom domain → `demo.stormbook.ai`. Netlify prints
   the DNS records; add a CNAME at your registrar pointing
   `demo` → `<site>.netlify.app`. HTTPS provisions automatically.
5. Create `config.json` in this directory with the production relay URL and
   commit it, **or** rely on `config.example.json` if the URL matches.
6. In the relay's Railway variables, set
   `CORS_ORIGINS=https://demo.stormbook.ai` — the browser blocks the POST
   otherwise, and it fails silently-looking mid-call.

Verify after deploy: load the site, open devtools, fire one SMS to your own
phone. A CORS failure shows up in the console immediately.

## Behavior

- Every field pre-populated on load, so a demo starts with one tap
- Submit → "Firing SMS…" spinner → confirmation screen with the timestamp
  and the exact message body the relay sent
- **Fire another** returns to the form with all state intact
- On error: inline message, form untouched — no retyping during a live call
- Slot A and Slot B must be different days. Same-day slots make the reply
  ambiguous, the relay classifies it `UNMATCHED`, and the demo stalls on a
  dead beat.

The yellow banner is not decoration. It tells the prospect the SMS is real,
which is the entire point of the demo.

## Design

`styles.css` is `apps/intake-form/styles.css` verbatim, plus a clearly
marked "Demo rig additions" block at the bottom (banner, fieldsets, slot
grid, message preview). Keep it that way — the demo should look like the
product.

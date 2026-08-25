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

## Deploying to Netlify (drag-and-drop)

Faster than the Git flow when you just need a URL on the iPad before a
visit. `netlify.toml` in this directory pins `command = ""` and
`publish = "."`, so autodetect can't invent a build step that fails.

```bash
cd apps/demo-form
zip -r ../demo-form.zip . -x '*.DS_Store'
```

1. Open <https://app.netlify.com/drop>.
2. Drop `demo-form.zip` on the page. Netlify unpacks and serves it at a
   generated `*.netlify.app` domain.
3. Optional custom domain: Site settings → Domain management → Add custom
   domain → `demo.stormbook.ai`. Add the CNAME Netlify prints at your
   registrar (`demo` → `<site>.netlify.app`). HTTPS provisions on its own.
4. In the relay's Railway variables, set `CORS_ORIGINS` to the exact origin
   Netlify gave you. Without it the browser blocks the POST, and mid-call it
   looks like the relay is simply dead.

Redeploying is the same drop onto the same site — the URL, and therefore the
bookmark, survives.

## Using the tokenized URL

`POST /fire` on the relay is bearer-token protected. Without the token the
relay answers 401 and the form shows "Access denied — token missing or
invalid." That's what keeps a public URL from burning the Twilio balance.

**1. Generate a token**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**2. Arm the relay.** Railway → the demo-relay service → Variables → add
`DEMO_ACCESS_TOKEN` with that value. Railway redeploys; wait for it to go
green before testing. Leaving the variable unset or blank disables auth
entirely, which is the intended local-dev behavior and *not* what you want
in production.

**3. Bookmark the tokenized URL** on the iPad:

```
https://<netlify-url>/?t=<token>
```

The form reads `t` at load and sends it as `Authorization: Bearer <token>`
on submit. It is never rendered into the page. Load the site without `?t=`
and a red strip appears under the yellow banner — that's the reminder you
opened the wrong bookmark, before you're in front of a prospect.

**4. Rotating.** Generate a new token, update `DEMO_ACCESS_TOKEN` in
Railway, wait for the redeploy, then replace the iPad bookmark. Order
matters: the old bookmark starts returning 401 the moment Railway restarts,
so rotate between visits, not during one.

**Security rules**

- Never commit the token — not in `config.json`, not in `netlify.toml`, not
  in a build-time env var. It lives in Railway and in the bookmark, nowhere
  else.
- Never screenshot or screen-share the URL bar with the token visible. If it
  ever lands in a recording, rotate immediately.
- Never share the bookmark. Each recipient is one more person who can spend
  the Twilio balance.
- The relay logs auth failures (`fire_auth_failed`) with the source IP and
  request ID, never the token. Repeated failures in the Railway logs mean
  the URL leaked — rotate.

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

# intake-form

Mobile-first, single-page HTML form for Stormbook intake. Pure HTML +
CSS + vanilla JS — no build step, no framework, no CDN fonts. Loads in
under a second on 3G and hosts statically on Netlify.

## Files

```
apps/intake-form/
├── index.html           # markup
├── styles.css           # styles (system fonts, primary color via CSS var)
├── form.js              # validation, phone formatting, submit, branding
├── config.example.json  # default config (mock: true, public API URL)
├── package.json         # npm scripts only; no dependencies
└── README.md
```

## Run locally

The form is a static page; any local web server works.

```bash
# from repo root
npm run dev --workspace @stormbook/intake-form

# or directly
cd apps/intake-form
npm run dev
```

That serves the form at `http://localhost:4321/` and opens a demo URL
preloaded with branding params and `source=meta`.

You can also just double-click `index.html` to open it from
`file://` — branding params still work; the form runs in **mock**
mode and skips fetching `config.json` over the file protocol.

### Verifying the submit payload (mock mode)

`config.example.json` ships with `"mock": true`. In mock mode the
form **does not POST** — it logs the payload object to the
DevTools console and shows the success screen. Open DevTools →
Console and look for:

```
[stormbook-intake] MOCK submit — payload: { client_id: …, source: …, … }
```

## URL parameters

| Param        | Required | Notes                                                                 |
| ------------ | -------- | --------------------------------------------------------------------- |
| `client_id`  | yes      | Contractor identifier. Missing → renders fatal error.                 |
| `company`    | no       | Company name shown in headline + confirmation.                        |
| `color`      | no       | Hex (e.g. `%23B91C1C` for `#B91C1C`). Invalid → falls back to default. |
| `logo`       | no       | Full URL to client logo. 404 / load failure is hidden silently.       |
| `phone`      | no       | Fallback phone shown in error states ("Or call …").                   |
| `source`     | no       | `meta` / `angi` / `direct` / `web`. Unknown → defaults to `direct`.   |

Test URL:

```
http://localhost:4321/?client_id=henderson-roofing&company=Henderson+Roofing&color=%23B91C1C&phone=903-555-0199&source=meta
```

## Deploy to Netlify

The form is a static site. Two options:

**Option A — drag-and-drop**

1. Zip the contents of `apps/intake-form/` (not the folder itself).
2. Drop the zip onto `https://app.netlify.com/drop`.
3. Set the production URL (e.g. `intake.stormbook.ai`) to that site.

**Option B — git deploy**

In Netlify, create a new site from this repo with these settings:

- Base directory: `apps/intake-form`
- Build command: *(none)*
- Publish directory: `apps/intake-form`

Before going live, copy `config.example.json` to `config.json` on the
deploy (or set `mock: false` in `config.example.json` and rebuild) so
that submits POST to the real API. `config.json` is gitignored — do
**not** commit a production config to the repo.

## What's intentionally NOT here (yet)

- Google Places address autocomplete (v1.1)
- Photo blob upload (v1 captures `photo_count` only)
- Tests (see `TODO.md`)
- Analytics, CAPTCHA, multi-step, i18n

# demo-dashboard

Static, read-only sales-demo dashboard for Stormbook. Mocked data, no
backend, no auth, no real-time updates. Designed for screen-share on a
Zoom demo: Mack walks a prospect through it, prospect sees what their
pipeline would look like as a Stormbook client.

The dataset is intentionally labelled **DEMO DATA** in a full-width
ribbon so nobody mistakes it for a real client's pipeline.

## Stack

- One `index.html`, one `styles.css`, one `dashboard.js`
- `Chart.js` via CDN (jsDelivr) for the funnel chart
- Fonts via [fonts.bunny.net](https://fonts.bunny.net) (privacy-respecting
  Google Fonts mirror). System mono/sans fallbacks if it fails
- Mock data in `data/mock.json` — single source of truth

No build step. No bundler. Open `index.html` and it works.

## Run locally

```bash
npm run dev
# serves on http://localhost:4322 and opens browser
```

Or just open `index.html` directly in a browser. The dashboard fetches
`data/mock.json`, so it needs to be served over `http://` — opening via
`file://` will fail the fetch on most browsers. `npm run dev` handles
that.

## Deploy to Netlify (drag-and-drop)

1. Open <https://app.netlify.com/drop>
2. Drag the entire `apps/demo-dashboard/` folder into the drop zone
3. Netlify gives you a URL like `https://<random>.netlify.app`
4. Optional: in Site settings → Domain management → rename to something
   like `stormbook-demo.netlify.app`

No build settings required. No environment variables. Static deploy.

To redeploy after editing mock data, drag the folder in again — Netlify
will overwrite the previous version of the site.

## Updating the mock data

All mock numbers live in [`data/mock.json`](./data/mock.json). Edit the
JSON, save, refresh the browser.

Sections in the file map to sections of the dashboard:

| JSON key                | Dashboard section            |
| ----------------------- | ---------------------------- |
| `today_leads[]`         | Today's Leads table          |
| `metrics`               | Four KPI cards               |
| `funnel_30d`            | Conversion Funnel chart      |
| `inspections_this_week` | Inspections This Week grid   |

### KPI thresholds (where the "on target" badge logic lives)

In `dashboard.js`, `renderKpis()`:

- Avg first response time → ok if `< 60s`
- Avg time-to-book → ok if `< 240s` (4 min)
- Booked-to-held rate → ok if `> 0.75`
- 7-day inspections booked → neutral (no target)

If you change a metric in `mock.json` to a number that crosses one of
those thresholds, the card automatically flips from "On target" (green)
to "Off target" (amber). No code change needed.

### Calendar entries

Each entry in `inspections_this_week[]` has:

```json
{ "day": "Tue", "time": "10:30", "name": "Tanaka", "damage": "Hail" }
```

- `day`: one of `Mon Tue Wed Thu Fri Sat Sun`
- `time`: 24-hour `HH:MM`, between `08:00` and `18:00` (calendar window)
- `name`: shown bold (use last name only — that's the convention)
- `damage`: short tag, shown as a chip

Blocks render as 1-hour visual blocks regardless of actual duration —
this is a demo, not a scheduler.

## What this dashboard intentionally does NOT do (v1)

- No real API. Static JSON only.
- No auth. Public URL is fine because the data is fake.
- No CSV/PDF export.
- No date-range filter. Always shows "today" and "this week".
- No real-time updates / websockets.
- The **client selector** in the header is **cosmetic only** — switching
  between Henderson Roofing / Beacon Storm Pros / Lone Star Restoration
  does **not** swap datasets. Tooltip on the selector documents this.
- No analytics or tracking.
- No print stylesheet. It's for screen-share, not print.

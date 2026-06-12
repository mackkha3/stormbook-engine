# Stormbook Engine — Build Kit

This kit is everything you need to build the Stormbook engine using Claude
Code in your terminal. Files generated in the browser, executed in the
terminal, results stay on your laptop.

## What's in here

```
stormbook-engine/
├── README.md              ← you are here
├── spec.md                ← source of truth (v1, locked for first 10 pilots)
└── prompts/
    ├── 0-scaffolding.md   ← project structure + tooling setup
    ├── 1-intake-form.md   ← §5.1 build (demo-ready by Week 1)
    ├── 2-demo-dashboard.md ← §5.3 build (demo-ready by Week 2)
    └── 3-intake-api.md    ← §5.2 build (production by Week 3)
```

## Prerequisites — one-time host setup

This project is **isolated** from your system. Once these are
installed, nothing else this kit does will touch global state.

**Required:**

- `nvm` (or `volta`, or `asdf` — pick one). nvm is the most common.
  Install on macOS:
  ```bash
  brew install nvm
  # then follow brew's post-install instructions to source nvm in your shell
  ```
- `git`
- A terminal and an editor (Cursor and VS Code both work)

**You do NOT install:**

- Node directly — the project's `.nvmrc` will install the right
  version into nvm's per-project directory
- Wrangler, TypeScript, Vitest, or any other CLI tool globally —
  every tool lives inside the project's `node_modules/`

If you ever want to fully tear this project off your machine,
deleting the project folder removes 100% of its footprint. Nothing
was installed system-wide.

## Setup (one time)

1. Download this entire `stormbook-engine/` folder to your laptop.
   Recommended location: `~/code/stormbook-engine/`
2. Open it in your editor of choice (Cursor, VS Code, whatever).
3. Match the Node version this project expects:
   ```bash
   cd ~/code/stormbook-engine
   nvm install   # reads .nvmrc, installs Node 20 if you don't have it
   nvm use       # switches your shell to that version, just for this dir
   ```
4. Initialize git the moment you have anything: `git init && git add -A
   && git commit -m "kit: initial spec + prompts"`.
5. Open a terminal in that directory. Run `claude` to start Claude Code.

If `nvm use` ever throws `N/A`, run `nvm install` first.

## How to use the prompts

Prompts are run **in order, in separate Claude Code sessions**, in the
same project directory. Each prompt is self-contained and assumes
Claude Code has access to `./spec.md` for any details not in the
prompt itself.

| # | When | Run prompt | Verify before next |
|---|---|---|---|
| 0 | First | `prompts/0-scaffolding.md` | `ls` shows the expected directory tree |
| 1 | Week 1 (before Day 15 launch) | `prompts/1-intake-form.md` | Open `apps/intake-form/index.html` locally, submit a test → fills mock log |
| 2 | Week 2 (after first demo) | `prompts/2-demo-dashboard.md` | Open `apps/demo-dashboard/index.html` → charts render with mock data |
| 3 | Week 3 (before first pilot deploys) | `prompts/3-intake-api.md` | `npm run dev` in `apps/intake-api/`, POST test lead, confirm GHL contact created |

Order is deliberate: form + dashboard come first because both are
demo-critical. The API is production-critical, not demo-critical, so
it can wait until you have a paying pilot.

## Workflow per prompt

```
$ cd ~/code/stormbook-engine
$ claude
> [paste the full contents of prompts/N-*.md]
[Claude Code builds]
$ # inspect the files it created
$ # run the verification step from the prompt
$ git add -A && git commit -m "build: <component>"
$ exit  # exit claude, start a fresh session for the next prompt
```

A fresh Claude Code session per prompt is the right move — keeps context
windows clean, prevents one build's mistakes from contaminating the next,
forces each prompt to be self-sufficient.

## When something goes sideways

Don't keep prompting Claude Code to fix a half-broken build. Come back
to me (browser) with:

1. What you ran
2. What it built (file tree + key file contents)
3. What's wrong

I'll regenerate or patch the prompt. Then you run the fixed version
in a clean Claude Code session.

## What this kit does NOT include

These are deferred until 3 pilots are signed and paid:

- Voice AI integration (Vapi.ai) — Premium tier feature
- Branded caller ID / STIR-SHAKEN registration
- Multi-tenant SaaS dashboard for clients
- Client-facing portals
- Slack/email notification stack beyond what GHL handles natively

These live in GHL, not in code — do not ask Claude Code to build them:

- Pipeline + custom fields + Workflows A/B/C/D (you build these in GHL UI per §4)
- Calendar setup (GHL)
- SMS templates (GHL snippets)
- Stripe billing event firing (GHL → Stripe webhook)

Stay narrow. Build only what's in §5. Everything else is
distraction.

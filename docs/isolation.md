# Project Isolation

Stormbook's Node toolchain stays fully isolated from the operator's machine — no system-wide packages, no PATH pollution. Three rules enforce this.

## 1. Pinned Node version (`.nvmrc` + `.tool-versions`)

Python projects use a `venv` directory to pin the interpreter. Node has no built-in equivalent, so we pin the Node version with two files:

- `.nvmrc` — read by [nvm](https://github.com/nvm-sh/nvm)
- `.tool-versions` — read by [asdf](https://asdf-vm.com/)

Both point at Node 24 LTS. The root `package.json` has `engines.node` set to `>=24.0.0 <25.0.0` so any `npm install` from a wrong Node version warns the operator.

## 2. Zero global installs

No tool — Wrangler, TypeScript, Vitest, linters — is ever installed with `npm install -g`. Every dependency is a per-workspace `devDependency` and lives inside that workspace's `node_modules/`.

The root `package.json` has a `preinstall` script that runs before any install and aborts if `wrangler` or `typescript` are detected as globally installed npm packages. That's the practical sniff test — if those two aren't global, nothing else from this project is either. The guard fails silently if `npm ls -g` itself errors (e.g. sandboxed environment, no global directory), so it does not block installs in unusual setups.

## 3. All execution through npm scripts or npx

Never assume a tool is on the operator's PATH. `npm run dev` works because it resolves to a workspace-local binary in `./node_modules/.bin/` (or calls `npx wrangler dev`, which uses the workspace's local copy).

## Switching Node versions when joining the project

```bash
cd stormbook-engine

# nvm users
nvm install
nvm use

# asdf users
asdf install
```

Both commands read `.nvmrc` / `.tool-versions` automatically.

## Opt-in containerized path

If a future contributor wants a fully sealed environment (e.g. for CI parity or Windows), a devcontainer setup will be documented at `docs/devcontainer-setup.md`. It is not written yet because Cloudflare Workers + Netlify deployments do not need containers for v1.

## Full teardown

To remove 100% of this project's dependencies from your machine:

```bash
rm -rf node_modules apps/*/node_modules
```

Nothing was installed system-wide, so nothing else needs cleaning up.

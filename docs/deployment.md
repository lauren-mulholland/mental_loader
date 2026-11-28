# Railway personal deployment

Accepted 26 November 2026. Target hosting budget: approximately US$5–10/month, excluding
model usage and tax. The user explicitly accepts service shutdown at a US$10 hard limit.
**Do not push Git changes without permission.** The user will publish the changes; Railway
can then build both app services from GitHub main.

## Current deployment status — 26 November 2026

Production Web is https://web-production-3bf4f1ba.up.railway.app. Both app services build
GitHub main. At the user's request this was initialized fresh, with no existing app/native
state transferred. Shared readiness flags are now true; do not re-import the initial input
file over live settings. Owner sign-in/recovery credentials are private in
`.local/railway-owner.json` (also Web `/data/owner.json`).

The workspace currently reports Trial. CLI verification found **no hard cap**; attempting
the requested US$8 alert / US$10 cap returned "Usage limits require an active subscription."
The user must activate Hobby on the actual hosting workspace (or identify the subscribed
workspace), then set and verify the cap. No subscription change was made by the assistant.
The remaining numbered instructions describe an initial deployment or state-transfer route;
do not repeat initialization on the live database/home.

## 1. Set the hard cap first

Create a personal Railway account/workspace on the **Hobby** plan, not Pro. Before creating
running services, open the correct workspace's **Usage > Set Usage Limits** and set:

| Compute Usage setting | Value |
| --- | --- |
| Custom email alert | US$8 |
| Hard limit | **US$10** |

Save, reopen and verify that the hard limit is active. These limits cover the entire selected
workspace, not just one project. Use the personal workspace intended for this hobby app.
The cap is a Railway account setting: **the repository cannot set or verify it** and it has
not been configured by this change. Do not mistake local resource limits for a billing cap.

The Hobby minimum is US$5/month and includes US$5 of resource usage. This is not US$5 on top
of all compute. The limit targets Railway compute usage (CPU, RAM, storage and egress).
OpenRouter billing, tax and any separately selected subscription/add-ons are outside it.
Our Hermes process is not Railway's separately billed "Railway Agent" product.

After installing/logging into the current Railway CLI, the equivalent commands are:

```sh
railway usage limit set --target workspace --soft 8 --hard 10
railway usage limit status --target workspace
```

Check the workspace identity before changing billing. At the hard limit, Railway takes the
workloads offline; the user has accepted that outcome. The app and reminders will stop.
Keep backups; do not delete persistent volumes to respond to a cap. Check the Usage page
and restart/redeploy when the budget permits. Do not assume a reset automatically restarts
everything. Overdue reminders can arrive late after recovery; interrupted model work is
not blindly replayed.

## 2. Install local tooling and create an empty project

The project needs Node 22.18+ for its small infrastructure package and a current Railway
CLI (5.63.0 or newer). The application and Hermes remain Python; Node is only a local
configuration tool.

```sh
brew install railway
railway --version
railway login
npm ci --prefix .railway --ignore-scripts
npm --prefix .railway run check
```

Create an **empty** Railway project named `mental-loader` in that personal workspace, then
link this checkout and choose its production environment:

```sh
railway link
```

Do not use the old Render Blueprint. Render configuration remains in `render.yaml` and
[archived instructions](deployment-render.md) as an alternative, not the selected deployment.

## 3. Prepare private inputs

Inputs are already prepared locally in `.local/railway-inputs/shared.env`. To prepare a new
initial set on a fresh checkout:

```sh
uv run python deploy/transfer.py export-env .local/railway-inputs --platform railway
```

The helper refuses to overwrite the file. It preserves the current VAPID identity and app
encryption key, and creates separate random Hermes API/tool credentials. A bootstrap app key
is kept privately in `.local/deployment-app-secret.key` when the application has no key yet.
Do not regenerate credentials for a running deployment; preserve the existing input file.
The obsolete `.local/render-inputs` and `.local/render-phase2-inputs` files are not the
Railway shared-variable file.

In the Railway project's **production environment**, create **Shared Variables** from the
private `shared.env` file (using the raw `.env` editor where available). Do not paste keys
into chat, source files or terminal command arguments. Import `.env` syntax as a file, or
copy values without surrounding quotes when entering fields individually.

Important initial values:

- `WEB_READY=false`
- `HERMES_READY=false`
- `WEB_ORIGIN=https://configure-after-first-deploy.invalid`

The remaining values include the household ID, OpenRouter key, VAPID identity, app encryption
key and separate agent credentials. Neither shared readiness flag should be true yet.
Shared variables persist in Railway; the infrastructure definition references them without
embedding secrets in source or resetting them on subsequent applies.

## 4. Review and apply the infrastructure definition

`.railway/railway.ts` uses the current Railway TypeScript IaC SDK. It is not the deprecated
`railway.json`/`railway.toml` configuration format. The SDK is pinned in `.railway/package-lock.json`.

```sh
railway config plan
railway config apply
```

Review the plan before confirming. A fresh environment should create:

| Resource | Configuration |
| --- | --- |
| Web | Existing app image, one replica, 512 MiB maximum RAM, 0.5 CPU limit |
| Hermes | Native runtime image, one replica, 512 MiB maximum RAM, 0.5 CPU limit |
| Postgres | PostgreSQL 18 product, 256 MiB maximum RAM, 0.5 CPU limit |
| web-state | 500 MB volume mounted at `/data` for persistent app key/operator files |
| hermes-state | 500 MB volume mounted at `/data` for native memory, sessions, skills and scheduler |

Railway's database product provisions its own data volume. Check that volume in the plan
and dashboard. All resources use the existing database region, `iad` (US East). The configuration disables sleeping
and public database/TCP exposure. Only Web will receive a generated public HTTPS domain.
Both app services reference `lauren-mulholland/mental_loader`, branch `main`, with separate
Dockerfiles. In Railway, connect the personal GitHub account/app and grant access to this
repository before applying. The configuration file itself does not grant repository access. Do not apply this full-project file to an
unrelated populated environment: omitted resources are treated as removals by Railway IaC.

The initial live apply required 500 MB volumes because the current workspace rejected 1 GB.
PostgreSQL 18 and its existing `iad` volume are preserved; do not downgrade that data directory.

## 5. Connect GitHub and build in setup mode

Application images build from GitHub main. Publish future application changes yourself when
ready; the assistant must ask before any remote push. Infrastructure settings are applied
separately with the configuration CLI.
Do not include `.env`, `.local`, input files or node_modules (all ignored).

After repository access is granted and `railway config apply` creates the services, Railway
can build **Web** and **Hermes** from that repository. Verify both services show `main` under
Settings > Source and the correct Dockerfile under Build:

- Web: `deploy/Dockerfile.web`
- Hermes: `deploy/Dockerfile.hermes`

Check that each service's GitHub autodeploy setting is enabled if you want future main
pushes to deploy app changes. Use Deploy Latest Commit for the first build if needed.
The IaC definition must still be applied with `railway config plan` / `railway config apply`
when infrastructure settings change; Railway does not automatically apply `.railway/` on
source pushes. A later source-only push can rebuild both services. No infrastructure-apply
GitHub Action is installed by this change.

Both services start a small setup server while their readiness flags are false. The app
returns HTTP 503 to normal requests and 200 with `setup: true, ready: false` at `/healthz`.
No app worker, model inference or native reminder scheduler runs in setup mode. Provisioned
services still incur usage charges, subject to the workspace cap.

For **Web only**, use Railway **Settings > Networking > Generate Domain**, target port
**10000**. Update the shared `WEB_ORIGIN` to that exact `https://…up.railway.app` origin,
without a path. Apply the variable change while both readiness flags are still false.
Do not generate public domains or TCP proxies for Hermes or Postgres.

## 6. Take a stopped-service snapshot

Stop the local app/Hermes (`make dev`, Ctrl+C) but leave Docker/PostgreSQL running. Then:

```sh
uv run python deploy/transfer.py snapshot .local/railway-cutover
```

This creates `database.dump` and `hermes-home.tar.gz` in a new private directory. It leaves
the original database/home intact and refuses if the default app/Hermes ports remain open.
Stop any custom-port services explicitly too. The home archive preserves memory, sessions,
learned skills and scheduler state; deployment secrets are provided separately.
Do not run local and hosted schedulers for the same active household after cutover.

## 7. Transfer and restore into the running setup containers

Use the current CLI's non-PTY piped SSH input:

```sh
railway ssh --service Web -- sh -c 'umask 077; cat > /tmp/database.dump' < .local/railway-cutover/database.dump
railway ssh --service Hermes -- sh -c 'umask 077; cat > /tmp/hermes-home.tar.gz' < .local/railway-cutover/hermes-home.tar.gz
railway ssh --service Web -- python /app/deploy/transfer.py restore-db /tmp/database.dump
railway ssh --service Hermes -- python /app/deploy/transfer.py restore-home /tmp/hermes-home.tar.gz
```

The CLI must be linked to the correct project/environment. File transfer also supports SCP
or Railway's volume browser if needed. Use the actual running containers, not a fresh build
container. Restore refuses an already populated target and runs while processing is disabled.
Do not redeploy between uploading a `/tmp` archive and restoring it.

If the household has no personal owner account yet, substitute its ID from the shared inputs:

```sh
railway ssh --service Web -- python -m mental_loader.cli migrate
railway ssh --service Web -- python -m mental_loader.cli create-account --household-id HOUSEHOLD_UUID --name Lauren --role owner --output /data/owner.json
(umask 077; railway ssh --service Web -- cat /data/owner.json > .local/railway-owner.json)
```

Keep that personal/recovery credential file private. Hosted browser login rejects the old
household access code. Preserve existing accounts instead of creating a duplicate owner;
operator `reset-account` is available if recovery is needed.

## 8. Enable and verify

1. Confirm the US$10 workspace hard cap is still active, the real `WEB_ORIGIN` is set,
   both restores succeeded, and local app/Hermes remain stopped.
2. Change shared `WEB_READY` to **true**, apply/deploy Web, and sign in with the personal code.
   Confirm your saved history, tasks and birthday records.
3. Change shared `HERMES_READY` to **true**, apply/deploy Hermes. Startup preserves native
   state, refreshes the household plugin and requires exactly one owned reminder cron job.
4. Test a normal chat and one household tool. This uses OpenRouter credits, outside Railway's
   cap. Then test a one-minute reminder with the laptop off.
5. On iPhone, install the new HTTPS URL to Home Screen, sign in and enable notifications.
   Confirm lock-screen delivery and navigation. Old Render/tunnel installations do not move.
6. Restart/redeploy to confirm state persistence and inspect Railway Usage for actual costs.
   A 512 MiB container passed local startup/restart checks; model/delegation peaks still need
   measurement in real use. The spend cap intentionally allows shutdown instead of overruns.

If Railway variables change, apply staged changes/deploy the affected service. Do not raise
caps or resource limits automatically to hide a failure. The user accepts shutdown; discuss
a new budget before raising the US$10 hard limit.

## Scope, optional integrations and backups

The explicit PaaS route serves only the configured household's shared chats. Private-chat AI
needs separately provisioned scoped runtimes; it never falls back to this household service.
Accounts, privacy rules, migrations and encrypted backups remain unchanged. No Docker socket
is mounted into the public app. This is not a verified multi-household hosting platform.

Google web OAuth is optional. Later, transfer its client file privately to the Web volume,
set `GOOGLE_WEB_CLIENT_PATH` to that file and register the exact HTTPS callback
`/api/integrations/google/callback` with Google. Read-only connection code exists; live hosted
consent, automatic sync and selected-event birthday import are not established by deployment.
AnyList remains deferred.

Preserve the app encryption key separately from encrypted backups. Use the existing operator
backup/restore tools with stopped native writers and a fresh test destination to verify
recovery. Setting a cap is not a backup policy. No live Railway backup/restore drill or real
phone test has been performed by preparing these files. To roll back to the laptop, first
set both hosted readiness flags false and deploy/stop the services, then reconcile any hosted
writes before starting the local scheduler. Keep the original snapshot.

## Cost evidence and local checks

Synthetic local measurements on 26 November: Web approximately 115 MiB, PostgreSQL 49 MiB,
Hermes 233 MiB; the constrained Hermes peak was approximately 247 MiB. These are short local
samples without inference, not a guaranteed monthly bill or full model/delegation load test.
US$5–10/month is a target, with shutdown at the separately configured US$10 hard cap.

```sh
npm --prefix .railway run check
uv run ruff check .
uv run pytest -q
docker build --platform linux/amd64 -f deploy/Dockerfile.web -t mental-loader-web:railway .
docker build --platform linux/amd64 -f deploy/Dockerfile.hermes -t mental-loader-hermes:railway .
uv run python deploy/smoke.py --platform railway --measure
```

The smoke test uses disposable PostgreSQL and synthetic credentials/state, applies the same
memory/CPU limits, verifies restore, personal login, native restart/state and a single no-agent
job, then removes its containers/network/volume. It makes no model calls or cloud changes.

Provider references checked 26 November 2026: [IaC](https://docs.railway.com/infrastructure-as-code),
[reference](https://docs.railway.com/infrastructure-as-code/reference),
[usage limits](https://docs.railway.com/cli/usage),
[cost controls](https://docs.railway.com/pricing/cost-control),
[SSH transfers](https://docs.railway.com/cli/ssh),
[pricing](https://docs.railway.com/pricing/plans).

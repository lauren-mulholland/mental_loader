# Archived Render deployment (superseded by Railway)

Archived 26 November 2026. Use [Railway deployment](deployment.md) for the accepted budget setup. This remains an optional higher-cost alternative. Deploy current functionality first; adaptive onboarding and
category views follow. Deployment files are prepared locally. No Render resources have been
created by this work and the application has not yet been verified on Render.

## What is prepared

- `render.yaml`: one public web service, one private Hermes service, managed PostgreSQL,
  Oregon region, private-only database access and persistent disks.
- Separate Docker images with Python 3.14.6, locked application dependencies and the exact
  Hermes revision/patch/validation dependencies already used locally.
- Setup mode: both services bind their ports but do not start the app worker or Hermes
  scheduler until `DEPLOYMENT_READY=true`. Setup health reports `ready: false` explicitly.
- Hosted startup: the app derives its HTTPS origin from Render, enables secure cookies,
  verifies a restored household with a personal owner account and matching VAPID keys,
  persists the application encryption key, disables legacy login, and uses one Uvicorn worker.
- Hermes startup preserves imported memory/config/model selection, refreshes the household
  plugin and reminder script, and applies cloud bind/credential settings to its home `.env`.
- Private input export and stopped-service state transfer helpers in `deploy/transfer.py`.

The web disk is deliberately retained even though records live in PostgreSQL: Render stops
an instance with a disk before starting its replacement. This avoids overlapping embedded
workers during deploys. Hermes likewise runs as exactly one instance with one native cron
scheduler. Brief deploy downtime is expected. Do not scale either service horizontally.

## Costs and account setup

Use a personal Render account connected to `lauren-mulholland/mental_loader`. Keep repository
visibility unchanged. Add billing and review the resource prices before creating the Blueprint.

The initial sizes are **Starter web (512 MB), Standard Hermes (2 GB), Basic PostgreSQL
(256 MB), a 1 GB web disk, a 5 GB Hermes disk and 1 GB database storage**. Hermes memory needs
have not been measured on Render; 2 GB is an initial conservative allocation, not a demonstrated
minimum. Review the combined price in Render; the previous approximately A$50/month was a
planning budget, not a quote or spending cap. Model-provider usage is billed separately.

No custom domain is needed. Render supplies the HTTPS origin. Nothing needs to run on the
laptop after cutover. Keeping Hermes running consumes service compute, but the once-per-minute
reminder dispatcher is a native no-agent script and does not itself request model inference.

## 1. Publish the deployment files

Review and commit the intended working-tree changes, including existing local implementation
changes needed by the pinned runtime. Push to the GitHub branch that Render will deploy.
The private `.env`, `.local`, provider credentials and state snapshots must remain ignored.
The Docker context uses an allowlist and does not contain these private files.

## 2. Prepare private Blueprint inputs

From the repository root:

```sh
uv run python deploy/transfer.py export-env .local/render-phase2-inputs --platform render
```

This creates private `web.env` and `hermes.env` files without printing values. It refuses to
overwrite existing files. Use the new `render-phase2-inputs` folder; earlier `render-inputs` files
are obsolete for the combined build. The web file contains the existing household ID, VAPID identity and app encryption key
(or a new key when the local pilot has none). The Hermes file contains the OpenRouter key.
Render generates a new tool credential, separate from the old household login code, and
passes it to the web service as `HERMES_TOOL_TOKEN`.
Both include `DEPLOYMENT_READY=false`; Hermes also has a deliberately invalid callback URL
until the real web URL is assigned. Existing model selection travels with the native home.

These files are sensitive. Open them locally for the Render input fields; do not paste them
into chat. Render generates both the private Hermes API key and its distinct household tool
credential. The web service references them automatically. Neither is a browser login code.
Preserve `APP_SECRET_KEY_B64` and the matching `/data/app-secret.key` with encrypted backups;
startup refuses a different key rather than silently making saved connections unreadable.

## 3. Create the Blueprint in setup mode

1. In Render, choose **New > Blueprint**, select this repository and the branch containing
   `render.yaml`, and use `render.yaml` as the Blueprint path.
2. Enter each prompted value from the corresponding private input file. Enter **false** for
   both `DEPLOYMENT_READY` fields. Supply the initial invalid `ML_BACKEND_URL` placeholder
   from `hermes.env`; it is unused in setup mode.
3. Review the paid resource list and confirm creation when the price is acceptable.
4. Wait for both service images to build and start. The public app returns a setup response
   with HTTP 503; `/healthz` returns 200 with `setup: true, ready: false`. This is expected.
5. Note the public web HTTPS URL. On the Hermes service, set `ML_BACKEND_URL` to that exact
   origin, without a trailing path. Save without enabling the service yet.

Do not import the root local `.env` wholesale: its database, origin and loopback URLs are for
the laptop. The Blueprint uses internal database networking and a private Hermes API.
Hermes calls the public HTTPS web origin for authenticated household tools because the
existing plugin deliberately refuses non-loopback plain HTTP callbacks.

Automatic Git deploys are disabled initially. `DEPLOYMENT_READY` and the callback are
manually managed values (`sync: false`), so Blueprint sync does not reset the cutover flags.

## 4. Take the final snapshot

Leave local PostgreSQL running. Stop `make dev` in its terminal so the app and Hermes are
both stopped; do not kill unrelated processes. Then run:

```sh
uv run python deploy/transfer.py snapshot .local/render-cutover
```

The helper refuses if the usual app or Hermes ports remain open. It creates a PostgreSQL
custom-format dump and a portable archive of the private Hermes home. It excludes provider
`.env`, logs, lock/PID/socket files and symlinks; provider credentials are supplied separately.
It includes native memory, sessions, learned skills and cron state. The source home and local
database remain intact. The destination must be new; use a different directory for a repeat.
If running services on custom ports, explicitly stop them too before snapshotting.

Do not restart the local scheduler once the hosted scheduler is enabled. If there is a gap
between the snapshot and hosted startup, due reminders can be delivered late after cutover.

## 5. Transfer and restore privately

Add your existing SSH **public** key to Render Account Settings. Use **Connect > SSH** on each
service to obtain its service address. The images include the directory/account setup Render
requires. Connect to the actual running setup instance, not an ephemeral shell, because the
persistent home is only available on the actual instance.

Using the SSH addresses shown by Render, stream the two files from your local terminal.
Replace the uppercase placeholders; do not include secret values in these commands:

```sh
ssh WEB_SERVICE@ssh.oregon.render.com 'umask 077; cat > /tmp/database.dump' < .local/render-cutover/database.dump
ssh HERMES_SERVICE@ssh.oregon.render.com 'umask 077; cat > /tmp/hermes-home.tar.gz' < .local/render-cutover/hermes-home.tar.gz
```

In the **web service** shell:

```sh
cd /app
python deploy/transfer.py restore-db /tmp/database.dump
```

In the **Hermes service** shell:

```sh
cd /app
python deploy/transfer.py restore-home /tmp/hermes-home.tar.gz
```

After database restore, create the personal owner account **in the web service shell** if
the restored household has not already been enrolled. Substitute the household ID from the
private inputs (not the old household login code):

```sh
python -m mental_loader.cli migrate
python -m mental_loader.cli create-account --household-id HOUSEHOLD_UUID --name 'Lauren' --role owner --output /data/owner.json
```

Save the personal/recovery credentials privately on your laptop using SSH redirection:

```sh
(umask 077; ssh WEB_SERVICE@ssh.oregon.render.com 'cat /data/owner.json' > .local/render-owner.json)
```

Use that personal access code for browser login; the shared household code is rejected on
Render. If accounts already exist, preserve them or use the documented reset-account command
for the intended account; do not silently create a second owner. No local personal database
or original Hermes home is enrolled or changed by preparing deployment files.

Database restore requires an empty public schema and runs in a single transaction; it refuses
to replace an existing database. Home restore requires an empty target and rejects absolute
paths, parent traversal, links and special files. Neither command enables scheduling or the
app worker. Uploaded `/tmp` archives disappear on redeploy; keep the private local snapshot.

## 6. Enable and verify

1. Confirm local app/Hermes remain stopped, both restores succeeded, and Hermes has the real
   `ML_BACKEND_URL` configured.
2. Set `DEPLOYMENT_READY=true` on **web**, then save and deploy. Its health check now queries
   PostgreSQL. Sign in with the personal account and check existing chats/tasks and birthdays.
3. Set `DEPLOYMENT_READY=true` on **Hermes**, then save and deploy. Startup uses the restored
   home, updates the reminder job's working directory and preserves its identity; it refuses
   conflicting or duplicate owned reminder jobs. Never run a second gateway against that disk.
4. Check one normal chat and household tool call. This uses provider credits; record usage.
5. Open the HTTPS app on iPhone, add to Home Screen, sign in, enable notifications and save a
   one-minute test reminder. Close the app, lock the phone and confirm delivery/navigation.
   The old temporary-tunnel shortcut cannot serve the new origin.
6. Restart/redeploy and confirm saved chat, native memory and the single reminder job survive.
   Check overdue delivery recovery and absence of an extra reminder in the tested scenario.

Changing `PUBLIC_ORIGIN` manually is only needed if introducing a custom domain; otherwise
startup uses `RENDER_EXTERNAL_URL`. Keep VAPID keys unchanged. Phase 2 adds account-bound Google read-only web OAuth, encrypted connections and disconnect.
It is disabled until you add the web OAuth client as Render secret file `google-web-client.json`
and register the exact HTTPS callback `/api/integrations/google/callback` with Google. This is
optional for initial deployment. New consent on the combined hosted build is unverified;
automatic sync and selected-event birthday import are still deferred. AnyList is also deferred.

## Backups and rollback

Enable/check the database backup retention in Render and confirm disk snapshots. A disk
snapshot is not evidence of successful whole-app restoration. Take a stopped-service native
home archive and database dump for coordinated backups, and restore them into isolated
services with `DEPLOYMENT_READY=false` before claiming recovery is demonstrated. Setup mode
can bind and pass its health check but is deliberately unavailable to users.

For rollback, set hosted Hermes and web to `DEPLOYMENT_READY=false` and deploy both before
restarting local services. If the hosted app accepted new writes, export/reconcile those
first; the old local snapshot will not contain them. Preserve the local snapshot until the
hosted system and restore procedure have been checked. Do not enable two schedulers against
the same household. No automated backup schedule or restore drill has yet been established
on Render by this change.

This is now a personal-account pilot with six ordered migrations, membership/privacy checks,
encrypted connection storage/backups and leased workers. The Render entrypoint enforces
`HOSTED_MODE=true`, `ALLOW_LEGACY_LOGIN=false` and `PAAS_PERSONAL_RUNTIME=true`. The last flag is
an explicit single-household route: only shared chats for `HERMES_HOUSEHOLD_ID` can use the
private service. It is never a fallback for private chats or another household. Native tool
authentication uses the separate generated credential, not the old shared login code.

Phase 2's Docker-inspected, per-scope runtime registration remains available on Docker hosts.
Render cannot run that attestation from the web app; this personal route does not pretend to
provide those sandbox guarantees. Private-chat AI requires a separately registered scoped
runtime and is unavailable on this one-runtime Render layout. Do not invite unrelated
households on the strength of the personal PaaS route. Continue migrations for onboarding.

The operator-reviewed local backup helper now supports birthday date fields. Existing native
memory, sessions and learned skills stay in Hermes; accounts and birthdays do not create a
second general-purpose memory or scheduling service.

## Local verification

```sh
docker build --platform linux/amd64 -f deploy/Dockerfile.web -t mental-loader-web:render .
docker build --platform linux/amd64 -f deploy/Dockerfile.hermes -t mental-loader-hermes:render .
uv run pytest -q
uv run ruff check .
uv run python deploy/smoke.py
```

The smoke check uses disposable containers and synthetic state, tests setup/restore/restart
and real Hermes gateway startup, and removes its containers/network afterwards. It makes no
model requests and does not stop or modify the personal local services. Hosted phone delivery,
real state migration and Render backup restoration still require verification after deployment.

## Provider references

Checked 25 November 2026:

- [Blueprint setup](https://render.com/docs/infrastructure-as-code)
- [Blueprint specification](https://render.com/docs/blueprint-spec)
- [Private services](https://render.com/docs/private-services)
- [Persistent disks and limitations](https://render.com/docs/disks)
- [SSH and Docker setup](https://render.com/docs/ssh)
- [Current pricing](https://render.com/pricing)

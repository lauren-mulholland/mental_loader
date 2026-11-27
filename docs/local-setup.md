# Local setup and infrastructure

For the current implementation and next actions, read [development handoff](handoff.md). Phase 2 sharing setup, account-scoped Google OAuth, runtime containers and backup/restore commands are in the [phase 2 guide](phase-2-accounts.md). This guide also includes setup instructions for a fresh checkout; the user has already configured OpenRouter locally, so do not recreate credentials just to continue testing.

The first build runs on your laptop. No AWS, Fly, Railway, paid database, separate Redis service or cloud hosting account is required yet. You need one model-provider account for live chat, and trusted HTTPS for a later iPhone push test.

For the next private personal hosted release, use [Railway deployment](deployment.md).
The local instructions below remain useful for development; the 25 November hosting
decision supersedes earlier statements that cloud setup is not needed yet.

## What is already prepared

| Component | Local location | Your action |
| --- | --- | --- |
| Python application | `.venv`, Python pinned by `.python-version` | Keep the project environment; run commands from the repo root |
| PostgreSQL | Docker Compose, localhost port 55432 | Keep Docker Desktop running |
| Hermes runtime | `.local/hermes-runtime/.venv`, pinned upstream revision | Use the project wrapper rather than a global Hermes command |
| Native state | `.local/hermes-home` | Keep this private and persistent; it contains memory, skills, sessions and schedules |
| Web Push identity | `.local/vapid-private.pem` and app `.env` | Set the sender contact before real push testing |
| Household skills | `skills/` installed into the private Hermes home | Prototype instructions/contracts are prepared; live delegation is not yet validated |

The application runs at `http://localhost:8000`; Hermes' private API runs at `http://127.0.0.1:8642`. Both are loopback-only by default. PostgreSQL is the only Docker service initially; the application and Hermes run in their separate local environments.

## Start and inspect

For a fresh checkout, first install Hermes as described in README, including the upstream messaging extra needed by its HTTP API:

```sh
uv sync --project .local/hermes-runtime --frozen --no-dev --extra messaging
uv pip install --python .local/hermes-runtime/.venv/bin/python -r hermes-validation-requirements.txt
make setup
make dev
```

`make dev` runs the app and Hermes gateway in the foreground. Ctrl+C stops both; PostgreSQL stays running and its volume is preserved. If you already started either service manually, stop it before using this command. Do not start duplicate Hermes writers against the same home.

In a separate terminal:

```sh
make doctor
```

Doctor checks local HTTP services, PostgreSQL, setup files and the native scheduler heartbeat. It prints no credentials and makes no inference requests. Credential-file presence alone is not evidence that a provider is configured correctly or that a balance is available.

## Set up the model provider

There are two environment files. The root `.env` configures our web app, PostgreSQL, push delivery and the private Hermes API connection. `.local/hermes-home/.env` configures Hermes, including its model-provider credentials. `HERMES_API_KEY` in the root file authenticates the app to Hermes; it is not your model-provider key.

For OpenRouter, add the following to `.local/hermes-home/.env`, keeping its existing settings:

```dotenv
OPENROUTER_API_KEY=your-openrouter-key
```

Select the provider and model with the picker below, then restart the services. Provider selection and model configuration are owned by Hermes; our application has no separate model setting.

OpenRouter is the current recommendation for this learning POC: Hermes supports it directly, and one account lets you compare tool-capable models and their usage. Provider selection remains your choice; no provider key has been added by the build.

1. Create an [OpenRouter account](https://openrouter.ai/) and fund a small prepaid balance appropriate to your testing budget. Create a project-specific API key and set a spending limit in the provider dashboard.
2. From the repo root, run the isolated Hermes model picker:

```sh
bash scripts/hermes.sh model
```

3. Select OpenRouter, enter the key locally when requested and choose a reasonably priced tool-capable model with sufficient context. Do not paste keys into chat, commit them, or put them in the PWA. Check the displayed model prices before confirming; we have not hard-coded a provider or model.
4. Stop the local services. Set `AI_ENABLED=true` in the application's `.env`, then run `make dev` again. Hermes' provider credentials belong in its private home; the root `HERMES_API_KEY` authenticates our app to Hermes and is a different credential.
5. Run `make doctor`, then test the actual live path:

```sh
uv run python scripts/live_chat_smoke.py --allow-model-calls
```

That opt-in check sends two top-level requests and spends provider credits. It tests saved chat and conversational continuity. A top-level turn can produce additional model calls; use provider-side budget controls. Cross-session memory and specialist quality need separate live checks.

Before attempting delegation, verify ordinary conversation and one household tool. In a fresh chat, ask Hermes to remember an ordinary shopping preference using its memory tool, start another chat and ask it to recall the preference. Confirm the native tool actually wrote the memory. Avoid using private medical details for setup tests.

## Verify scheduling without a model

Keep `make dev` running. The native `mental-loader-reminders` cron job dispatches due household reminders every minute in no-agent mode. The following check saves one test task and waits for the real running scheduler; it does not manually invoke the dispatch function:

```sh
uv run python scripts/scheduler_smoke.py
```

It allows up to 150 seconds for scheduler cadence and writes local evidence to `.local/scheduler-check.json`. This check passed against the running gateway after approximately 60 seconds. It proves in-app dispatch, not that an iPhone displayed a push. If it fails, use `bash scripts/hermes.sh cron status` and `make doctor`. A scheduled job cannot run while the laptop sleeps or the Hermes gateway is stopped.

## Verified service restart recovery

On 4 November 2026, a test reminder was saved, both app and Hermes were stopped before it became due, and PostgreSQL retained the overdue undispatched task. After the user restarted `make dev`, native cron processed it once; a later successful scan produced no duplicate task or reminder. The completed test and evidence are recorded in `.local/downtime-check.json`. This used no model calls and no manual dispatch. Laptop power loss/sleep, interrupted model execution and phone push retry recovery remain separate checks.

## Diagnosing chat failures

The CLI suppresses HTTP access logs to protect OAuth callback query strings and writes brief worker failures to the terminal running `make dev`. Failed chat replies include a request reference; search the terminal for that `run_id`. Worker logs include a safe failure category, without raw provider bodies, message text or keys. Hermes HTTP 200 responses still require a successful response-body status; a failed turn is recorded as failed and is not automatically replayed by the app. Hermes may retry individual provider calls internally.

Hermes writes rotating logs to `.local/hermes-home/logs/`: `errors.log` contains provider errors and stack traces, `agent.log` contains runtime activity, and `gateway.log` contains gateway/session activity. Its secret redaction is enabled, but these logs can include ordinary conversation excerpts. Keep them private. The app currently has no separate persistent log file or external log service.

The pinned Hermes revision has a targeted compatibility patch in `patches/hermes-openrouter-replay.patch`. OpenRouter rejected persisted OpenAI/Azure encrypted reasoning with `invalid_encrypted_content` during a continued conversation. The upstream recovery only covered direct Responses requests, leaving this chat-completions route retrying unchanged history. On this specific rejection, the patch removes encrypted reasoning carriers from the request and canonical history and permits one repair retry per API iteration, preserving visible messages, tool calls, receipts and readable summaries. It does not disable normal reasoning, erase saved chats or replay completed tools. The precise upstream reason the original encrypted blocks became invalid has not been established.

The wrapper and `setup-hermes` apply this patch idempotently and refuse conflicting source changes. Restart `make dev` after a code/patch change; an already running gateway retains imported code. To verify the recovery against the real native classifier and handler without credits:

```sh
.local/hermes-runtime/.venv/bin/python scripts/hermes_recovery_smoke.py
```

Synthetic recovery and application failure handling are tested; a live retry of the affected conversation after restart is still needed. Before upgrading Hermes, review whether upstream has fixed this path and retire or adapt the local patch.

## What you need for iPhone notifications

A real iPhone test passed on 2 November 2026: lock-screen display and correct-chat navigation were confirmed by the user. The temporary tunnel was closed and its backend phone registration removed after testing; its old Home Screen shortcut is no longer usable. Repeat these steps with a live trusted HTTPS origin for another phone test or ongoing use.

1. In the app's `.env`, set `VAPID_SUBJECT=mailto:your-email-address`. The generated VAPID key is already local; do not regenerate it after subscribing devices unless you intend to reconnect them.
2. Arrange trusted HTTPS to the app for a personal development test, using a trusted local certificate or a deliberately configured development tunnel. A plain HTTP LAN address cannot support the required iPhone web-push setup. No tunnel or public endpoint is created by the build.
3. Set `PUBLIC_ORIGIN` to the exact HTTPS origin and `COOKIE_SECURE=true`, then restart the application. Keep the Hermes API private; only the app needs a phone-accessible URL.
4. On iPhone, add the app to Home Screen, open it there, sign in, and enable notifications. Save a one-minute reminder, leave its checkmark untouched, close the app and lock the phone. Tapping the checkmark completes the task and stops its reminder. Allow for the one-minute scan interval. Confirm both receipt and navigation to the correct saved chat.

This remains a personal development test. Account, membership, scoped runtime and restore foundations now exist in the phase 2 worktree; activating them and hosting securely are separate operator actions before inviting friends. Follow [phase 2 setup](phase-2-accounts.md) rather than copying or rotating existing credentials blindly.

## Google Calendar read access

Use the personal Gmail account only, never work/Okta. Setup succeeded on 4 November 2026 in personal Chrome with project `mental-loader-513621`, a Desktop client and an External/Testing OAuth registration containing the personal account as its sole test user. Keep `.local/google-client.json` and the household token under `.local/google/` private with mode `0600`. Ten upcoming primary-calendar events and one birthday were read; the user confirmed one birthday was expected. No Calendar writes or model calls occurred. Verification is in `.local/google-calendar-check.json`. Testing-mode access expires after seven days, so repeat consent if needed on resume; preserve the existing client rather than creating new credentials unnecessarily.

For a fresh setup, create a Google Cloud project with Calendar API enabled and a Desktop OAuth client for the local read-only probe. This requires no server deployment. Save the client JSON as `.local/google-client.json`, then follow [the calendar probe instructions](feasibility.md#google-calendar-probe). Hosted OAuth and automatic birthday sync are later work.

## Native delegation validation

The lean Hermes installation does not include `jsonschema`. Without it, native delegation accepts parseable JSON without validating the supplied schema. Install `hermes-validation-requirements.txt` after each upstream `uv sync`; its five versions match the pinned upstream lockfile. The wrapper refuses startup if the validator is missing. Run `.local/hermes-runtime/.venv/bin/python scripts/hermes_schema_smoke.py` to verify both acceptance and rejection without model calls. Pass complete parsed schemas in `delegate_task` task objects as `tasks[].output_schema`; schema text in context alone does not enable validation.

## Specialist experiment

The installed `household-orchestrator`, `household-meals` and `household-supplies` skills define sequential native subagent handoffs and structured outputs. A bounded synthetic-pantry test returned three dinners through the app with both native child schemas validated. Repeat with manually supplied pantry facts when evaluating broader meal quality. Inspect actual delegate_task calls; confirm the outputs respect supplied constraints and remain proposals. Pantry/grocery write tools and AnyList sync are not implemented yet.

Setup preserves skill files changed by Hermes so learned improvements are not silently overwritten. Review those local changes and promote useful improvements back into version-controlled skill sources deliberately.

## References

- [Hermes model providers](https://hermes-agent.nousresearch.com/docs/integrations/providers)
- [Hermes with OpenRouter](https://openrouter.ai/blog/tutorials/hermes-agent/)
- [Hermes script-only cron](https://hermes-agent.nousresearch.com/docs/guides/cron-script-only)

When moving to a PaaS later, persist both PostgreSQL and Hermes' private home, keep API credentials server-side, configure backups, and validate household isolation. Provisioning that hosted infrastructure is not needed for this local stage.

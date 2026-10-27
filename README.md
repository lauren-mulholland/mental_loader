# Mental Loader

A household assistant that helps people remember, prepare for, and share everyday responsibilities. It should proactively suggest the next useful action, so the user does not have to remember to check another list.

The first version is a **Hermes learning POC**, with a web app for both laptop and phone. Hermes provides the agent runtime: conversation, sessions, memory, scheduling, skills and delegation. Household rules and integration actions stay in clean Python functions exposed through a thin plugin. Future portability applies to those functions; we deliberately use Hermes' native features rather than building replacements now.

This is a local technical-feasibility spike, not a publicly deployable multi-user product. No paid model calls run by default.

## Product and design documentation

Continuing in a fresh conversation? Start with the [development handoff](docs/handoff.md) for the current implementation, verified results, unresolved incident and ordered next steps (updated 27 October 2026).

| Document | What it covers |
| --- | --- |
| [Development handoff](docs/handoff.md) | Current state, immediate priorities, evidence limits and instructions for the next conversation |
| [Product requirements](docs/product-requirements.md) | Purpose, users, household capabilities, constraints and boundaries |
| [Architecture](docs/architecture.md) | Components, request flows, data ownership and tenant isolation |
| [Design decisions](docs/design-decisions.md) | Agreed choices, reasons, tradeoffs and open questions |
| [Implementation plan](docs/implementation-plan.md) | Staged delivery, testing and readiness criteria |
| [Feasibility report](docs/feasibility.md) | Current evidence, setup instructions and outstanding live checks |
| [Local infrastructure setup](docs/local-setup.md) | Startup commands, provider credentials, readiness checks and iPhone setup |

The requirements describe the intended product. The feasibility report describes what has actually been tested, including [native Hermes feature coverage](docs/feasibility.md#native-feature-coverage). A planned or enabled capability should not be assumed to have passed live validation.

## Intended capabilities

- Birthdays, gift recipients and cultural occasions, including preparation lead times.
- Meal suggestions, cupboard staples, reported stock and grocery lists.
- Household appointments, booking follow-ups and supply replenishment.
- Recurring home tasks and maintenance as the product grows.
- Saved chat, upcoming tasks and proactive notifications with useful next actions.

The tenancy boundary is a **household**, with individual accounts and memberships planned before a friends pilot. The local spike uses household access codes and tests two households; only the personal household is connected to a dedicated Hermes runtime.

## Run locally

Requirements: `uv` and Docker Desktop running. `.python-version` pins Python 3.14.6; `uv` selects that interpreter for the local application environment.

```sh
uv sync --locked
docker compose up -d db
uv run python -m mental_loader.cli init
uv run python -m mental_loader.cli serve
```

Open <http://localhost:8000>. Use the access code from `.local/access-codes.json`. The second household is for isolation testing; it does not share the personal Hermes runtime or reminder scheduler. Try `/remind 1m Buy a birthday card` to save a task, then close/reopen the page to check saved history. Automatic reminder delivery also requires the Hermes scheduler described below.

## Hermes learning setup

Hermes uses **its own** `.local/hermes-runtime/.venv`, separate from the app's `.venv`. Its private home is `.local/hermes-home`; nothing is installed into global Python or `~/.hermes`. The native plugin is in [`hermes_plugin/`](hermes_plugin/).

```sh
# This checkout was tested at the exact commit in hermes-runtime.ref.
git clone https://github.com/NousResearch/hermes-agent.git .local/hermes-runtime
git -C .local/hermes-runtime checkout 749cb638ff43f9ce5333c186a1e4f67d473aa4f2
uv sync --project .local/hermes-runtime --frozen --no-dev --extra messaging
uv run python -m mental_loader.cli setup-hermes
bash scripts/hermes.sh setup
```

Choose your provider and an inexpensive supported model in Hermes' setup. Provider keys belong in `.local/hermes-home/.env` (for OpenRouter, `OPENROUTER_API_KEY`); the root `.env` contains application settings and `HERMES_API_KEY` for the app-to-Hermes connection. See [local setup](docs/local-setup.md#set-up-the-model-provider) for details. Then use `bash scripts/hermes.sh` for Hermes' native interface. Keep the Python backend running so its household tools are available.

`setup-hermes` enables native memory, session search, skills and cron tools, and registers one recurring `mental-loader-reminders` job. Hermes runs this in **no-agent mode** every minute: a Python script calls the household dispatch function without inference. The app has no parallel reminder timer. Keep the backend and Hermes gateway running for scheduled delivery; a registered job alone does not mean the scheduler is running.

Hermes' profile memory holds stable preferences, shopping routines and lessons. Its skills hold reusable procedures. PostgreSQL holds structured tasks and delivery records; it is not a replacement for Hermes memory. Use a new chat at natural boundaries so Hermes loads updated memory. Native memory and cron mechanisms have been tested without models; conversational use still needs live-provider validation.

For web chat, set `AI_ENABLED=true` in the app's `.env` after configuring a provider. Run `make dev` to start the app and gateway together, or use separate terminals for the individual commands. The upstream messaging extra supplies the gateway's HTTP adapter dependencies. The user has received two live responses through OpenRouter; a subsequent encrypted-history failure has a targeted runtime patch with offline regression coverage. Live recovery after restart, memory recall and specialist behaviour still need validation; see the [handoff](docs/handoff.md) and feasibility report.

`make doctor` checks local service and configuration readiness without model calls or printing secrets. See [local setup](docs/local-setup.md) for the remaining model-provider and phone setup.

The app's `AI_DAILY_CALL_LIMIT` limits top-level Hermes requests per household. It is not a dollar cap: a turn can make multiple provider or delegated calls. The dedicated Hermes config also restricts iterations and concurrent delegation.

## Verification

```sh
uv run pytest -q
uv run ruff check .
uv run python scripts/browser_smoke.py
# Uses Hermes' own interpreter and actual plugin loader; backend must be running.
.local/hermes-runtime/.venv/bin/python scripts/hermes_plugin_smoke.py
.local/hermes-runtime/.venv/bin/python scripts/hermes_features_smoke.py
.local/hermes-runtime/.venv/bin/python scripts/hermes_recovery_smoke.py
# Observe the running scheduler (no inference); make dev must be running.
uv run python scripts/scheduler_smoke.py
# Opt-in only, after connecting a provider and enabling AI; spends API credits.
uv run python scripts/live_chat_smoke.py --allow-model-calls
```

Browser checks require installed Google Chrome (otherwise install Playwright Chromium). Screenshots are written to `.local/screenshots/` and contain only local test data.

See [`docs/feasibility.md`](docs/feasibility.md) for Google Calendar setup, real-device push, current limitations, the isolation boundary and follow-up experiments.

# Mental Loader

A household assistant that helps people remember, prepare for, and share everyday
responsibilities. It should proactively suggest the next useful action, so the user does not have to remember to check another list.

The first version is a **Hermes learning POC**, with a web app for both laptop and phone.
Deterministic household functions, storage and reminders live in plain Python; Hermes provides conversation and optional delegation through a native plugin. The code is structured so the agent runtime can be replaced later.

The current build is a local technical-feasibility spike. Next is a private personal Render
deployment, followed by adaptive onboarding and category views alongside chat. The target
is a household app with built-in agent support. It is not ready for a multi-user launch.
No paid model calls run by default.

## Product and design documentation

| Document | What it covers |
| --- | --- |
| [Product requirements](docs/product-requirements.md) | Purpose, users, household capabilities, constraints and boundaries |
| [Architecture](docs/architecture.md) | Components, request flows, data ownership and tenant isolation |
| [Design decisions](docs/design-decisions.md) | Agreed choices, reasons, tradeoffs and open questions |
| [Render deployment](docs/deployment.md) | Personal hosting layout, setup responsibilities and launch checks |
| [Implementation plan](docs/implementation-plan.md) | Staged delivery, testing and readiness criteria |
| [Feasibility report](docs/feasibility.md) | Current evidence, setup instructions and outstanding live checks |

The requirements describe the intended product. The feasibility report describes what has
actually been tested. A planned capability should not be assumed to exist in this POC.

## Intended capabilities

- Birthdays, gift recipients and cultural occasions, including preparation lead times.
- Meal suggestions, cupboard staples, reported stock and grocery lists.
- Household appointments, booking follow-ups and supply replenishment.
- Recurring home tasks and maintenance as the product grows.
- Saved chat, upcoming tasks and proactive notifications with useful next actions.

The tenancy boundary is a **household**, with individual accounts and memberships planned
before a friends pilot. The local spike uses household access codes and tests two households;
only the personal household is connected to a dedicated Hermes runtime.

## Run locally

Requirements: `uv` and Docker Desktop running. `.python-version` pins Python 3.14.6;
`uv` selects that interpreter for the local application environment.

```sh
uv sync --locked
docker compose up -d db
uv run python -m mental_loader.cli init
uv run python -m mental_loader.cli serve
```

Open <http://localhost:8000>. Use the access code from `.local/access-codes.json`.
The second household is for isolation testing; it does not share the personal Hermes runtime.
Try `/remind 1m Buy a birthday card`. Close/reopen the page to check saved history.

## Hermes learning setup

Hermes uses **its own** `.local/hermes-runtime/.venv`, separate from the app's `.venv`.
Its private home is `.local/hermes-home`; nothing is installed into global Python or `~/.hermes`.
The native plugin is in [`hermes_plugin/`](hermes_plugin/).

```sh
# This checkout was tested at the exact commit in hermes-runtime.ref.
git clone https://github.com/NousResearch/hermes-agent.git .local/hermes-runtime
git -C .local/hermes-runtime checkout 749cb638ff43f9ce5333c186a1e4f67d473aa4f2
uv sync --project .local/hermes-runtime --frozen --no-dev
uv run python -m mental_loader.cli setup-hermes
bash scripts/hermes.sh setup
```

Choose your provider and an inexpensive supported model in Hermes' setup. Keep all secrets
in the ignored local files. Then use `bash scripts/hermes.sh` for Hermes' native interface.
Keep the Python backend running so its household tools are available.

For web chat, start `bash scripts/hermes.sh gateway run`, set `AI_ENABLED=true` in the app's
`.env`, and restart the app. The gateway can require optional runtime dependencies; follow
Hermes' package-manager diagnostics if prompted. **Live provider/gateway chat is not yet
verified**; see the feasibility report for the boundary between tested and pending work.

The app's `AI_DAILY_CALL_LIMIT` limits top-level Hermes requests per household. It is not
a dollar cap: a turn can make multiple provider or delegated calls. The dedicated Hermes
config also restricts iterations and concurrent delegation.

## Verification

```sh
uv run pytest -q
uv run ruff check .
uv run python scripts/browser_smoke.py
# Uses Hermes' own interpreter and actual plugin loader; backend must be running.
.local/hermes-runtime/.venv/bin/python scripts/hermes_plugin_smoke.py
```

Browser checks require installed Google Chrome (otherwise install Playwright Chromium).
Screenshots are written to `.local/screenshots/` and contain only local test data.

See [`docs/feasibility.md`](docs/feasibility.md) for Google Calendar setup, real-device push,
current limitations, the isolation boundary and follow-up experiments.

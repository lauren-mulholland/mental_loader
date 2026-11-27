# Development handoff

Updated 26 November 2026; earlier evidence dated Australia/Sydney. Read this first when continuing in a fresh conversation, then consult [implementation plan](implementation-plan.md), [architecture](architecture.md), [design decisions](design-decisions.md) and [local setup](local-setup.md). This handoff records the last verified state; inspect the working tree and running services before changing anything.

## Latest direction — 25 November 2026

The user resumed work. This section supersedes the older pause and hosting priorities below.

- Deploy current functionality on Render for the personal household first. Replace the
  earlier Vercel preference with a public FastAPI service, private persistent Hermes service
  and managed PostgreSQL. No hosted resources have been provisioned by this documentation.
- The product is a household app with built-in agent support. Add category tabs/views for
  Events, Meals, Shopping and Stock/Supplies over time. Users can manage records directly;
  chat is an additional interface. Shared domain functions keep UI and agent changes aligned.
- The fast follow is adaptive onboarding: ask which areas to manage, then relevant questions
  such as favourite foods and usual staples for meals. Support skip/resume and corrections.
- Preserve the current Hermes runtime and native capabilities. No category UI, onboarding,
  agent-framework migration or multi-household launch is part of the first deployment.
- The deployment change set also includes earlier chat reliability, polling and native
  validation changes. Render Blueprint, separate web/Hermes images, setup-mode startup and state-transfer helpers
  are now implemented. Both Linux images build; local container checks cover database restore,
  web restart, Hermes container replacement/restart, persistent memory and a single native
  reminder job with synthetic data and no model requests. Lint, 38 application tests and
  Render's published Blueprint schema validation pass. Private dashboard input files are
  prepared in ignored `.local/render-inputs` with owner-only permissions; no secrets uploaded.
  Hosted provisioning, real state transfer, phone checks and backup restoration remain to be done.
  See [deployment](deployment.md) and
  the current delivery order in [implementation plan](implementation-plan.md).

## Product and agreed approach

Mental Loader is a personal household assistant intended to reduce the effort of remembering and preparing for birthdays, gifts and cultural events; planning meals and groceries; managing appointments and replenishment; and eventually managing home tasks. Proactive reminders and useful next-action suggestions are central. The user is learning Hermes through real use before deciding whether an SDK-based agent is needed.

- Use Hermes as the actual runtime: native memory, sessions/recall, skills, delegation and scheduling. Do not rebuild these features for hypothetical future portability.
- Keep household rules, validation, calculations and integration actions in ordinary Python functions behind thin tools. Required deterministic sequences belong in code. A future migration can reuse these functions and the app/database, but must export/adapt native state and rewrite runtime integration.
- Provide a responsive PWA for laptop and iPhone, saved visible chat and upcoming tasks. No native app review or WhatsApp dependency. Phone push needs trusted HTTPS, Home Screen installation and permission.
- Make the household the tenant boundary. The POC has one real household and a second isolation-test household; only the real household can use the dedicated Hermes runtime. Accounts, memberships and safe runtime separation are required before sharing with friends.
- Use an orchestrator and focused specialists, introduced incrementally. Meals and supplies own distinct work but exchange explicit context and structured results through native `delegate_task`. Dependent work is sequential. Keep iterations and delegation small to control costs.
- Deploy the existing personal build on Render, accepted 25 November 2026, superseding the earlier Vercel preference. Preserve PostgreSQL and the private Hermes home. The Blueprint and packaging are ready locally; follow [deployment](deployment.md) for provisioning, state transfer and hosted checks.
- Google Calendar is the first calendar integration, initially read-only. AnyList is preferred but a supported integration has not been established. Do not promise either automatic calendar sync or grocery writes yet.

## Current implementation

| Area | Current state |
| --- | --- |
| App | FastAPI, SQLAlchemy/PostgreSQL, plain HTML/CSS/JavaScript PWA; one durable chat-request worker |
| Persistence | Household-scoped messages, requests, tasks, push subscriptions/deliveries and basic AI usage; schema creation only, no versioned migrations |
| Hermes connection | `src/mental_loader/agents.py` calls the private `/v1/responses` API with stable conversation and request identities; the app does not blindly replay uncertain executions |
| Tools | `hermes_plugin/` exposes native clock, task list and reminder tools through authenticated backend endpoints; household credentials determine scope |
| Reminder timing | One native Hermes no-agent cron job calls `/api/tool/dispatch-reminders` every minute; Python evaluates due tasks and delivers the PWA outbox without inference |
| Memory and skills | Native memory, session search, skills and delegation enabled; orchestrator/meals/supplies skill drafts and output contracts installed; learned skill edits are preserved during setup |
| Cost controls | AI enable switch, top-level daily request limit, bounded Hermes iterations/concurrency; request counts are not dollar caps or counts of every provider call |
| Model | User configured OpenRouter. Logs during the incident identified `openai/gpt-6-luna`; inspect current private configuration before assuming that remains selected |
| Calendar | Personal Google Desktop OAuth read access verified 4 November; ten upcoming events read; one birthday matched the user’s expected count. Automatic sync remains unimplemented |
| Sharing | Access-code POC only; no production identities, memberships, tenant sandbox or public deployment |

Live validation found and fixed a session-mapping issue: the adapter previously sent a household-only `X-Hermes-Session-Key`, causing new app chats to resolve to the same native session. The header now equals the household-and-conversation identifier also sent in the Responses body. Regression assertions cover stable mapping within a chat and different mapping across chats; live memory write/recall used distinct native sessions. This fix was installed in the original running checkout and loaded after the user restarted `make dev`. Existing saved response chains and profile memory are preserved.

Hermes owns agent context; the PostgreSQL transcript supports app-visible history. The request worker processes saved requests and does not schedule reminders. Web Push is a custom delivery transport, not a second scheduler.

## Environments, credentials and startup

Work from `/Users/laurenmulholland/Projects/personal/mental_loader`. The application uses `.venv` and root `uv.lock`; Hermes uses `.local/hermes-runtime/.venv` and its own upstream lockfile. The wrapper selects `.local/hermes-home`, never global `~/.hermes`. The exact upstream revision is in `hermes-runtime.ref`.

- Root `.env`: app, database, push and private Hermes connection settings. `HERMES_API_KEY` authenticates app-to-Hermes; `AI_ENABLED` enables natural-language chat.
- `.local/hermes-home/.env`: Hermes settings and `OPENROUTER_API_KEY`. Provider/model selection is in its `config.yaml`. Do not print keys or ask the user to paste them in chat.
- `.local/access-codes.json`: private local household login codes. Preserve these, the database volume, VAPID key and Hermes home.
- Docker Desktop supplies PostgreSQL on loopback port 55432. App is localhost:8000; Hermes API is loopback:8642. `make dev` runs both foreground processes; Ctrl+C stops them while preserving PostgreSQL.

```sh
make dev
# In another terminal; does not call a model:
make doctor
```

Inspect existing processes before starting another instance. The user has been running services in their own terminal; this chat does not have that terminal attached. Do not assume the latest patch has been loaded by those processes. No infrastructure changes or new keys are needed for the next local steps.

## Evidence and limits

- Last application verification: **38 tests passed**, lint passed. A Starlette/httpx deprecation warning remains. These checks used no model credits.
- Earlier real browser checks covered desktop/mobile layouts, saved history and household isolation. Native plugin execution, synthetic native memory persistence/isolation and no-agent cron execution passed. Actual gateway-driven reminder dispatch was observed after about 60 seconds.
- The user successfully sent two live messages through the app. This proves the basic provider path works; subsequent bounded checks verified natural-language reminder tool use and native memory recall in a fresh session. A bounded supplies-to-meals delegation also passed; broad specialist quality and general multi-turn reliability remain unestablished.
- The third message failed with HTTP 400 `invalid_encrypted_content`; a subsequent attempt reproduced it. The local runtime patch passed a synthetic regression against the actual Hermes classifier/recovery handler, but **live recovery after restart passed on 27 October**: one read-only turn in the affected chat recalled its first user message exactly, preserved the app transcript and left tasks unchanged; the runtime log confirmed encrypted-reasoning recovery and one retry.
- Actual iPhone lock-screen display and correct-chat navigation passed on 2 November. Automatic calendar sync, AnyList sync, learned skill creation and interrupted-execution recovery remain unverified or unimplemented as detailed in [feasibility](feasibility.md).
- The earlier-approach cleanup removed root `OPENAI_API_KEY`/`OPENAI_MODEL`, graph naming and `/suggest` routing. No old agent-framework dependencies or extra database tables were found. Hermes may use the OpenAI SDK internally; that is part of its provider implementation.

## Current incident and logging

The failed conversation contained encrypted reasoning with both OpenAI and Azure formats. The exact upstream reason those blocks became invalid is not established. The confirmed Hermes gap was that encrypted-history recovery covered direct Responses transport but not this OpenRouter chat-completions route, so generic retries resent the rejected history.

`patches/hermes-openrouter-replay.patch` adds a narrow recovery: on this rejection for OpenAI models through OpenRouter, strip encrypted reasoning carriers from canonical/request history and retry once per API iteration, preserving visible messages, readable summaries, tool calls and receipts. It does not pre-emptively remove reasoning or erase stored chats/memory. `scripts/patch_hermes.py` applies it idempotently from `setup-hermes` and the wrapper, refusing conflicting source changes. Review/retire this patch when upgrading Hermes; do not silently update the pinned runtime.

The app already checks the response-body status: HTTP 200 can carry a failed Hermes turn. Do not claim HTTP 200 alone is treated as success. The recent change retains usage on failed turns, logs a safe error category plus `run_id`, and includes that reference in the failed chat reply. It does not log raw provider bodies or request text.

App HTTP/worker output currently goes to the terminal, with no separate persistent app log. Hermes rotating logs live in `.local/hermes-home/logs/{errors,agent,gateway}.log`. Native secret redaction is enabled, but logs can include ordinary household message excerpts. Inspect narrowly and avoid copying private contents into docs or chat.

## Next steps, in order

1. **Incident recovery confirmed.** On 27 October, verified the gateway had restarted after the patch, then sent one read-only request in the affected chat. It recalled the exact first user message, preserved the earlier app transcript and left tasks unchanged. The runtime log confirmed removal of four replay carriers and a successful bounded retry. One top-level model request was used; this is not a count of provider attempts or a dollar-cost measurement. This establishes recovery of the affected chat, not general multi-turn reliability.
2. **Adaptive polling implemented.** `static/app.js` refreshes every 2.5 seconds while the selected chat has active runs and every 30 seconds when idle, measured after reads finish. Actions and tab return refresh immediately; hidden tabs stop polling. Refreshes serialize and queue a new read when an action or conversation switch occurs during an existing read. The separate once-per-minute no-agent reminder job is unchanged. `scripts/polling_smoke.py` exercises the actual frontend with a fake API and controlled browser clock: delayed reply, idle reminder, hidden/return, slow conversation switch, action during refresh, failed-read recovery, logout and single submission. This is frontend evidence, not a live scheduler/push check.
3. **Core live behaviour verified on 27 October.** A natural-language 24-hour reminder invoked `household_clock` and `household_remind` through Hermes' deferred tool dispatcher, produced exactly one task and a deadline within two minutes of the expected time. A real browser reload retained its saved transcript, including completion. Native memory confirmed a temporary marker save; a new app chat with a distinct native session recalled it without retrieval tools or the marker in its prompt. The test reminder was completed and the temporary curated-memory entry removed. Five top-level model requests were used, including an initial save/cleanup because the test inspector initially missed batched memory operations. Provider attempts and dollar costs were not measured. This is a small behaviour check, not broad quality evaluation.
4. **Small specialist experiment verified on 27 October.** Two app requests each ran supplies then meals over synthetic pantry facts and returned three vegetarian dinners for two. The initial run embedded schemas in context without enabling native validation; skill instructions now require parsed schemas in `tasks[].output_schema`. The lean runtime also lacked `jsonschema`, whose fallback only checks JSON parsing. Installed the five versions in `hermes-validation-requirements.txt`, matching the pinned upstream lockfile. The repeat returned `schema_valid: true` for both children; timestamps, complete supplies handoff and exact child results nested in the app reply were checked. A no-model smoke test also rejected deliberately schema-invalid JSON. Tasks and curated memory were unchanged in both runs. Outputs remain proposals; budget/time are model estimates, no cooking trial or grocery writes occurred. The subsequent real-device push check passed on 2 November; graceful app/gateway downtime recovery was subsequently verified on 4 November.
5. **iPhone delivery and graceful service downtime verified.** Configured the private sender contact without changing keys, used an explicitly approved temporary Cloudflare HTTPS tunnel, verified access-code login and Secure/HttpOnly cookies, and subscribed the installed iPhone PWA. The first test was completed by the user before due time, correctly suppressing delivery; a replacement remained open, was processed by native cron and accepted by Apple on its first push attempt. The user confirmed a lock-screen notification and correct-chat navigation. No model calls were used. Completed the test task, removed the temporary backend phone registration, closed the tunnel and restored the localhost origin/cookie settings on disk; restart `make dev` to load those settings if not already done. The temporary Home Screen shortcut no longer works. Keep private evidence in `.local/phone-push-test.json`. On 4 November, saved a fresh reminder through the authenticated backend, stopped the verified `make dev` launcher before its deadline, confirmed both service ports were closed and the PostgreSQL task remained open and undispatched past due time, then had the user restart `make dev`. Native cron recovered it once; a later successful native scan produced no duplicate task or reminder. Completed the test task and preserved evidence in `.local/downtime-check.json`. No model calls or manual dispatches were used. The earlier 2 November timing attempt was superseded and not counted. Laptop power loss/sleep, interrupted model execution and push-outbox recovery during downtime remain unverified.
6. **Personal Google read access verified on 4 November; integration work remains.** Use only the personal Gmail account, never work/Okta. The user created project `mental-loader-513621` in personal Chrome. With explicit approvals, enabled Calendar API, created the Mental Loader OAuth registration in External/Testing mode, and added only the personal account as a test user. The user created/downloaded the Desktop client and placed it in `.local/google-client.json` with mode `0600`. The user completed consent in personal Chrome; the existing probe read ten upcoming primary-calendar events using only `calendar.events.readonly`, with no writes or model calls. A count-only birthday query for the next 366 days returned one instance without pagination; the user confirmed one was expected. Token permissions are `0600`; evidence is `.local/google-calendar-check.json`. This is primary-calendar read feasibility and count confirmation, not exhaustive identity matching, additional-calendar coverage, automatic sync or agent-tool integration. Testing-mode grants/refresh tokens expire after seven days, so re-consent may be needed when resuming. Preserve client/token files privately and do not copy personal addresses, secrets or event details into docs. Next establish supported AnyList access or agree an alternative, then review remaining Phase 1 gates.
7. **Then follow the phased plan.** Complete Phase 1 gates before claiming feasibility complete. Build the birthday workflow/personal pilot and sharing foundations deliberately; individual accounts, isolation, migrations, backups and runtime persistence are prerequisites to a hosted friends pilot. Do not start a framework migration or cloud deployment merely to finish the local checks.

## Earlier pause (lifted 25 November 2026)

The 4 November pause ended when the user resumed and requested Render deployment preparation on 25 November. The following describes the state at that earlier pause: Calendar read access, user-confirmed birthday count, iPhone push, graceful service recovery and the Vercel preference are recorded above. The local app and Hermes were left running; no temporary public tunnel is open. Check service state when resuming. Credentials and verification evidence remain in the original checkout's ignored `.local` directory, not this worktree or Git. Check the testing-mode Google token before assuming it is still usable. The next planned work is AnyList feasibility or an agreed alternative and the remaining Phase 1 gates, before implementing birthday sync/workflow or hosting.

## Verification commands and repository state

```sh
make test
# Real Hermes classifier/recovery with fixtures, no model calls:
.local/hermes-runtime/.venv/bin/python scripts/hermes_recovery_smoke.py
.local/hermes-runtime/.venv/bin/python scripts/hermes_schema_smoke.py
# With local services running; no model calls:
uv run python scripts/browser_smoke.py
uv run python scripts/scheduler_smoke.py
# Opt-in only; spends credits and creates a test conversation:
uv run python scripts/live_chat_smoke.py --allow-model-calls
```

The existing live smoke test is two turns without tool use. It does not specifically prove recovery of the affected chat, memory persistence or specialist execution. Use targeted checks for those cases.

The baseline handoff was committed as `f5f7173`; core recovery/polling/session changes were committed as `12127b6` on `codex/partnerpal/verify-core-behaviour`. This continuation began in an isolated worktree with a clean detached HEAD; inspect current Git status for subsequent changes. The original running checkout is `/Users/laurenmulholland/Projects/personal/mental_loader`; it contains private runtime state that is absent from the isolated worktree. Preserve all existing work, inspect `git status`, and do not reset the repo or regenerate credentials. No new thread was created automatically. Documentation prose should use one physical line per paragraph; the user dislikes arbitrary hard wraps.

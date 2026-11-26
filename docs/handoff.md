# Development handoff

Updated 25 November 2026, Pacific/Honolulu. Read this first when continuing in a fresh conversation, then consult [implementation plan](implementation-plan.md), [architecture](architecture.md), [design decisions](design-decisions.md) and [local setup](local-setup.md). This handoff records the last verified state; inspect the working tree and running services before changing anything.

## Phase 2 completion

The user requested that phase 2 be finished without further check-ins unless an action was needed. Phase 2 sharing foundations are now implemented and their acceptance checks pass: personal identities/recovery, owner/member roles, private participants, five versioned migrations, account-scoped encrypted Google integration, scoped containerized Hermes routing and credentials, persistent quotas, safe leased workers/outbox, per-account usage and encrypted backup/restore. See [phase 2 foundations](phase-2-accounts.md) for the acceptance matrix, operating commands and limits. Private AI remains disconnected unless its own compliant runtime is registered. Public deployment and invitations remain phase 5 work, and the phase 1 live gates remain explicitly open.

Changes are in /Users/laurenmulholland/.codex/worktrees/4dc2/mental_loader. This worktree has no personal .env, installed native runtime or production credentials; ignored .local/screenshots/phase2 contains synthetic screenshots only. The original personal setup was not changed, migrated, enrolled or connected to extra runtimes. Verification used its existing application Python environment with this checkout's src on PYTHONPATH and a Docker image built from committed pinned Hermes source plus the existing reviewed patch. The image mental-loader-hermes:phase2 remains available locally; all test containers, volumes and temporary homes were removed after use.

Current evidence: 66 tests pass on SQLite and the same 66 pass on disposable PostgreSQL 17, including concurrent migrations/jobs, ancestry constraints, account/calendar isolation, device/session notification controls and a PostgreSQL-to-PostgreSQL encrypted restore drill. Ruff and whitespace checks pass. Desktop/mobile browser checks cover private conversations, participant revocation, sign-in, code rotation and offline recovery. Actual restricted Hermes containers pass memory/session/tool isolation, real gateway startup, native no-agent cron registration, wrong-scope restore refusal and fresh-volume restoration of memory, sessions and learned skills with rotated credentials. Model transport and Google exchange fixtures are labelled mocks; no paid model calls, real Google consent or actual phone pushes occurred. The container/host administrator remains trusted. Next new feature work is phase 3: birthdays and gifts.

## Product and agreed approach

Mental Loader is a personal household assistant intended to reduce the effort of remembering and preparing for birthdays, gifts and cultural events; planning meals and groceries; managing appointments and replenishment; and eventually managing home tasks. Proactive reminders and useful next-action suggestions are central. The user is learning Hermes through real use before deciding whether an SDK-based agent is needed.

- Use Hermes as the actual runtime: native memory, sessions/recall, skills, delegation and scheduling. Do not rebuild these features for hypothetical future portability.
- Keep household rules, validation, calculations and integration actions in ordinary Python functions behind thin tools. Required deterministic sequences belong in code. A future migration can reuse these functions and the app/database, but must export/adapt native state and rewrite runtime integration.
- Provide a responsive PWA for laptop and iPhone, saved visible chat and upcoming tasks. No native app review or WhatsApp dependency. Phone push needs trusted HTTPS, Home Screen installation and permission.
- Make the household the tenant boundary. The POC has one real household and a second isolation-test household; only the real household can use the dedicated Hermes runtime. Accounts, memberships and safe runtime separation are required before sharing with friends.
- Use an orchestrator and focused specialists, introduced incrementally. Meals and supplies own distinct work but exchange explicit context and structured results through native `delegate_task`. Dependent work is sequential. Keep iterations and delegation small to control costs.
- Start locally, then use a PaaS with persistent PostgreSQL and Hermes homes. Railway is a candidate, not a commitment. No AWS/Fly-specific dependency or cloud provisioning is needed now.
- Google Calendar is the first calendar integration, initially read-only. AnyList is preferred but a supported integration has not been established. Do not promise either automatic calendar sync or grocery writes yet.

## Current implementation

| Area | Current state |
| --- | --- |
| App | FastAPI, SQLAlchemy/PostgreSQL, plain HTML/CSS/JavaScript PWA; leased chat requests and fenced completion across workers |
| Persistence | Five transactional revisions, household ancestry constraints, shared/private conversations, account-scoped integrations and per-run/account usage |
| Hermes connection | Stable Responses conversations/request identities; attested scoped container routes or the unchanged local single-household legacy route; uncertain executions are not replayed |
| Tools | Clock/task/reminder functions behind credentials bound to a household-shared or private-conversation scope; hosted mode rejects legacy household credentials |
| Reminder timing | One native Hermes no-agent cron job calls `/api/tool/dispatch-reminders` every minute; Python evaluates due tasks and delivers the PWA outbox without inference |
| Memory and skills | Native memory, session search, skills and delegation enabled; orchestrator/meals/supplies skill drafts and output contracts installed; learned skill edits are preserved during setup |
| Cost controls | AI enable switch, top-level daily request limit, bounded Hermes iterations/concurrency; request counts are not dollar caps or counts of every provider call |
| Model | User configured OpenRouter. Logs during the incident identified `openai/gpt-6-luna`; inspect current private configuration before assuming that remains selected |
| Calendar | Account-scoped read-only OAuth/PKCE, encrypted credentials, connect/read/disconnect UI and account-aware desktop probe; live consent and event sync remain pending |
| Sharing | Operator-provisioned personal identities, one-time recovery, roles, private participants, TLS/secure-cookie hosting prerequisites and restricted per-scope runtime containers; no public deployment |

Hermes owns agent context; the PostgreSQL transcript supports app-visible history. The request worker processes saved requests and does not schedule reminders. Web Push is a custom delivery transport, not a second scheduler.

## Environments, credentials and startup

The existing personal instance lives at `/Users/laurenmulholland/Projects/personal/mental_loader`; phase 2 source changes are in the worktree above and have not been applied to that instance. The application uses `.venv` and root `uv.lock`; Hermes uses `.local/hermes-runtime/.venv` and its own upstream lockfile. The wrapper selects `.local/hermes-home`, never global `~/.hermes`. The exact upstream revision is in `hermes-runtime.ref`.

- Root `.env`: app, database, push and private Hermes connection settings. `HERMES_API_KEY` authenticates app-to-Hermes; `AI_ENABLED` enables natural-language chat.
- `.local/hermes-home/.env`: Hermes settings and `OPENROUTER_API_KEY`. Provider/model selection is in its `config.yaml`. Do not print keys or ask the user to paste them in chat.
- `.local/access-codes.json`: private local household login codes. Preserve these, the database volume, VAPID key and Hermes home.
- Docker Desktop supplies PostgreSQL on loopback port 55432. App is localhost:8000; Hermes API is loopback:8642. `make dev` runs both foreground processes; Ctrl+C stops them while preserving PostgreSQL.

```sh
make dev
# In another terminal; does not call a model:
make doctor
```

Inspect existing processes before starting another instance. The user has been running services in their own terminal; this chat does not have that terminal attached. Do not assume the latest patch has been loaded by those processes. Continuing the existing personal POC needs no credential regeneration. Activating the new sharing topology is a separate operator setup using the phase 2 guide; do not overwrite the personal environment or enable paid calls automatically.

## Evidence and limits

- Last application verification: **66 tests passed on SQLite and 66 on PostgreSQL 17**, lint passed. Browser authentication/recovery/private-chat checks, restricted native container isolation, real gateway startup/cron setup and native/database restore checks pass with synthetic data. A Starlette/httpx deprecation warning remains. These checks used no model credits and did not establish live conversational behavior or actual phone push.
- Earlier real browser checks covered desktop/mobile layouts, saved history and household isolation. Native plugin execution, synthetic native memory persistence/isolation and no-agent cron execution passed. Actual gateway-driven reminder dispatch was observed after about 60 seconds.
- The user successfully sent two live messages through the app. This proves the basic provider path works; conversational memory recall, specialist quality and full multi-turn reliability have not been established.
- The third message failed with HTTP 400 `invalid_encrypted_content`; a subsequent attempt reproduced it. The local runtime patch passed a synthetic regression against the actual Hermes classifier/recovery handler, but **a live retry after restart is not yet confirmed**.
- Actual iPhone lock-screen push, Google authorisation, automatic calendar sync, AnyList sync, live specialist execution, learned skill creation and interrupted-execution recovery remain unverified or unimplemented as detailed in [feasibility](feasibility.md).
- The earlier-approach cleanup removed root `OPENAI_API_KEY`/`OPENAI_MODEL`, graph naming and `/suggest` routing. No old agent-framework dependencies or extra database tables were found. Hermes may use the OpenAI SDK internally; that is part of its provider implementation.

## Current incident and logging

The failed conversation contained encrypted reasoning with both OpenAI and Azure formats. The exact upstream reason those blocks became invalid is not established. The confirmed Hermes gap was that encrypted-history recovery covered direct Responses transport but not this OpenRouter chat-completions route, so generic retries resent the rejected history.

`patches/hermes-openrouter-replay.patch` adds a narrow recovery: on this rejection for OpenAI models through OpenRouter, strip encrypted reasoning carriers from canonical/request history and retry once per API iteration, preserving visible messages, readable summaries, tool calls and receipts. It does not pre-emptively remove reasoning or erase stored chats/memory. `scripts/patch_hermes.py` applies it idempotently from `setup-hermes` and the wrapper, refusing conflicting source changes. Review/retire this patch when upgrading Hermes; do not silently update the pinned runtime.

The app already checks the response-body status: HTTP 200 can carry a failed Hermes turn. Do not claim HTTP 200 alone is treated as success. The recent change retains usage on failed turns, logs a safe error category plus `run_id`, and includes that reference in the failed chat reply. It does not log raw provider bodies or request text.

Worker output goes to the terminal, with no separate persistent app log. The CLI disables HTTP access logging to protect OAuth callback query strings, and database exceptions hide SQL parameters. Hermes rotating logs live in `.local/hermes-home/logs/{errors,agent,gateway}.log`. Native secret redaction is enabled, but logs can include ordinary household message excerpts. Inspect narrowly and avoid copying private contents into docs or chat.

## Next work and outstanding live gates

Phase 2 is complete in source and local acceptance checks. Next new development is phase 3: recipients, preferences, budgets, dates, preparation lead times and gift completion, then account-authorized calendar discovery/update handling. The following phase 1 checks remain separate; do not label feasibility or the hosted friends pilot complete based on phase 2 isolation tests.

1. **Confirm the incident recovery live.** Ask whether the user restarted `make dev` after the patch. After restart, send a short message in the same affected chat and verify completion, preserved context and any recovery warning. Do not delete the conversation, reset memory or blindly resubmit prior tool actions. Inspect saved tasks before retrying uncertain work. This is the outstanding paid-provider check; use only the minimum turns needed and report usage/evidence honestly.
2. **Reduce frontend polling.** The user queried noisy repeated GETs and accepted the proposed direction. Current `static/app.js` still fetches messages and tasks every 2.5 seconds while visible, including idle periods: roughly 48 GETs/minute/tab. Implement fast polling while a request is pending, roughly 30-second polling when idle, immediate refresh after actions and on returning to the tab, no hidden-tab polling and no overlapping requests/timers. These GETs use no model credits. Preserve the separate once-per-minute no-agent reminder job. SSE is optional future work, not required for this POC. Verify a delayed reply, an idle reminder, tab return and conversation switching without missed updates or duplicate submissions.
3. **Validate core live behaviour.** Check natural-language reminder creation with actual tool use, then memory write and recall in a fresh chat. Verify saved history after refresh. Keep model calls bounded; native smoke tests are not substitutes for live agent behaviour.
4. **Run one small specialist experiment.** Use manually supplied pantry facts and three dinners; supplies assessment first, then meals. Inspect actual `delegate_task` execution and result delivery through the API, not just an answer claiming delegation. Outputs remain proposals; pantry and grocery write tools are not implemented.
5. **Finish proactive delivery feasibility.** Set a valid VAPID sender contact if still needed, arrange trusted HTTPS for a personal device test, install the PWA on iPhone, subscribe and verify a locked-screen notification opens the right saved chat. Test gateway/laptop downtime and avoid assuming accepted push means displayed push.
6. **Finish integration feasibility.** Configure the user's Google Desktop OAuth client locally and run the read-only probe, including relevant birthday/calendar coverage. Establish supported AnyList access or explicitly agree an alternative.
7. **Then follow the phased plan.** Complete Phase 1 gates before claiming feasibility complete. Proceed to the birthday workflow/personal pilot deliberately. Accounts, scoped runtimes, migrations and restore foundations are implemented; hosted configuration, operational backup scheduling and monitoring still precede a friends pilot. Do not start a framework migration or cloud deployment merely to finish the local checks.

## Verification commands and repository state

```sh
make test
# Real Hermes classifier/recovery with fixtures, no model calls:
.local/hermes-runtime/.venv/bin/python scripts/hermes_recovery_smoke.py
# With local services running; no model calls:
uv run python scripts/browser_smoke.py
uv run python scripts/scheduler_smoke.py
# Opt-in only; spends credits and creates a test conversation:
uv run python scripts/live_chat_smoke.py --allow-model-calls
```

The existing live smoke test is two turns without tool use. It does not specifically prove recovery of the affected chat, memory persistence or specialist execution. Use targeted checks for those cases.

The October implementation is committed in the starting checkout. The November phase 2 implementation, documentation, lockfile, runtime scripts and regression tests are recorded on branch `codex/phase-2-sharing-foundations`; inspect `git log -1 --oneline` for its commit. The branch has not been pushed or merged into the original personal checkout. Preserve all existing work, inspect `git status`, and do not reset the repo or regenerate credentials. No new thread was created automatically. Documentation prose should use one physical line per paragraph; the user dislikes arbitrary hard wraps.

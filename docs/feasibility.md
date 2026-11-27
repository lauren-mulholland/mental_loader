# Phase 1: technical feasibility

## Consolidated local build — 26 November 2026

Phase 2 accounts, membership/privacy rules, migrations, encrypted connections/backups and
leased workers are integrated with Render packaging and a minimal dated birthday workflow.
The combined suite passes 85 tests on SQLite and disposable PostgreSQL 17. Birthday UI checks
pass at desktop/mobile widths. Additional browser and deployment checks are recorded in the
handoff after completion. These are synthetic/local checks, not a new Google consent,
provider-quality assessment, phone-push confirmation or hosted restore drill. Earlier live
evidence below retains its original date and scope.


Status updated 4 November 2026. Phase 1 remains in progress. Read [development handoff](handoff.md) for the current incident, operational state and ordered next actions.

## Current decision

Use Hermes first to learn its capabilities and limitations, following the agreed
[design decisions](design-decisions.md) and [architecture](architecture.md). The domain
functions, database and integrations do not import Hermes. A native plugin calls our backend
over HTTP; `agents.py` adapts browser conversations to Hermes' Responses API.

The current scope is one real household plus a second household for isolation tests. Future
friend households need separately configured Hermes homes/processes or a demonstrated safe
shared execution design. The application refuses to route a second household into the first
household's runtime. Session IDs and prompt instructions alone are not security boundaries.

## Feasibility matrix

| Question | Implementation and evidence | Remaining live check |
| --- | --- | --- |
| Does the app work on laptop and phone layouts? | Responsive PWA; browser script tests 1440px and 390px layouts | Installed iPhone PWA used for the successful push test; broader usability remains |
| Can users see saved conversations? | PostgreSQL messages and durable request records; browser reload test; user received two live OpenRouter responses | Affected chat recovered after restart with exact first-message recall; broader multi-turn reliability still needs use |
| Can Hermes call deterministic household code? | Native plugin registered by the actual Hermes plugin manager; clock, save reminder, duplicate retry and list tasks exercised against local PostgreSQL | Live 24-hour reminder selected clock/reminder tools and saved one task; broader ambiguity/correction checks remain |
| Can code run without models? | `/remind` saves tasks without inference; the running native Hermes scheduler processed a due reminder after about 60 seconds without manual dispatch | Overdue task recovery after graceful app/gateway stop passed 4 November; interrupted model execution remains |
| Are native memory and skills used? | Native memory tool persisted a synthetic preference across store reloads; a second home did not see it. Memory, session search and skills toolsets are enabled; meals/supplies/orchestrator skill drafts and output contracts installed | Live native save and fresh-session recall of a temporary marker passed; bounded specialist execution passed; learned skill creation remains |
| Are household records separated? | Tests for conversations, tasks, tool credentials and runtime routing | User accounts, membership roles, invitations, DB row policies and deployment review |
| Can reminders notify outside the app? | Web Push transport, service worker, VAPID keys, persistent outbox, retry/expired-subscription tests | iPhone lock-screen display and correct-chat navigation confirmed 2 November; graceful app/gateway recovery passed 4 November; power-loss/push-retry recovery remains |
| Can Google Calendar be read? | Personal Desktop OAuth connected 4 November; ten upcoming primary events and one upcoming birthday instance read; user confirmed expected birthday count | Additional-calendar coverage, exact birthday matching and automatic sync remain unimplemented/unverified |
| Can AnyList be updated? | No supported public integration established from reviewed official help pages | Confirm a supported integration with AnyList; do not promise sync |

Run the commands in README to reproduce the evidence. A fake model response or mocked push
transport is not evidence of a working live provider or real phone notification.

Hermes owns reminder timing; the application reminder loop has been removed. `setup-hermes` creates a native `mental-loader-reminders` script-only job with a one-minute interval. Its script calls the authenticated household dispatch action, which handles structured task state and the custom PWA push transport. Both direct no-agent execution and an actual gateway-driven scheduled dispatch have been observed. The cadence check completed after approximately 60 seconds; phone lock-screen display and correct-chat navigation were subsequently confirmed on 2 November.

## Native feature coverage

| Hermes feature | Current use | Evidence boundary |
| --- | --- | --- |
| Cron scheduler | Native one-minute, no-agent reminder-dispatch job; additional agent-backed jobs can be managed with native cron tools | Native runner and actual gateway-driven cadence tested without inference; Overdue reminder recovery and no duplicates on a later scan passed after graceful app/gateway stop; in-flight execution interruption remains |
| Curated memory | Native `USER.md` / `MEMORY.md` for preferences, routines and lessons in the household's dedicated home | Synthetic store persistence/isolation plus live native memory save and exact marker recall in a distinct fresh session passed; test marker removed afterwards |
| Sessions and recall | Stable Hermes conversation mapping and enabled `session_search`; two initial live responses and a successful recovery turn | Affected-chat recovery and native profile-memory recall in a distinct fresh session are verified; explicit session_search recall remains unverified |
| Skills | Native skills tools and learning nudges enabled; initial meals/supplies/orchestrator skills and handoff contracts installed | Native discovery can list the prepared skills; supplies/meals skills loaded and delegated live; learned improvements remain unverified |
| Delegation | Native delegation tools enabled with bounded iterations/concurrency | Two sequential supplies/meals experiments returned results through the app; repeat verified native schema validation. Broader quality and background continuation remain unverified |

The PWA transcript, structured household database and push sender support the product interface. They do not replace Hermes' native memory, session management, skills or scheduling. Portability is concentrated in household functions and integration adapters; a future migration must also export/adapt native state.

## Live testing and recovery incident

The user configured OpenRouter and received two successful live chat responses. Logs identified `openai/gpt-6-luna`. A third message failed on 26 October and a subsequent attempt failed on 27 October with `invalid_encrypted_content`. Persisted reasoning included both OpenAI and Azure formats; why those encrypted blocks became invalid has not been established. The confirmed recovery gap in the pinned Hermes source was that direct Responses replay had a repair path while this OpenRouter chat-completions path retried unchanged history.

The versioned `patches/hermes-openrouter-replay.patch` adds a bounded repair for this specific route/error, removing encrypted reasoning from canonical/request history while retaining visible messages, tool calls, receipts and readable summaries. The wrapper/setup apply it idempotently. A running gateway needs a restart to load it. The real Hermes classifier and patched handler passed a synthetic regression test; a live check after restart on 27 October exercised that recovery path successfully. One read-only turn in the affected chat recalled the first user message exactly, preserved its earlier app transcript and left tasks unchanged. The runtime warning confirmed removal of four replay carriers and one retry. This verifies the targeted recovery, not the upstream root cause or all future provider failures.

Last verification: 28 application tests passed, lint passed, native recovery smoke passed, patch reapplication was idempotent and shell syntax checks passed. No paid calls were used for these checks. The app already rejected failed Hermes response-body statuses on HTTP 200; recent diagnostics retain failed-turn usage and add safe failure categories/request references. See [logging details](local-setup.md#diagnosing-chat-failures).

## Core live behaviour check, 27 October 2026

A natural-language request for a reminder in 24 hours invoked the actual `household_clock` and `household_remind` handlers through Hermes' deferred `tool_call` dispatcher. The tool receipt returned a task ID, the app stored exactly one matching task, and its deadline was within two minutes of the expected instant. The test task was completed afterwards. A real Chrome reload retained the saved conversation, including the completion message.

The check exposed a session-mapping defect: a household-only `X-Hermes-Session-Key` resolved new app chats to an existing native session. The adapter now uses the same household-and-conversation identifier in the session header and Responses body. Automated regression assertions check continuity within a chat and distinct keys between chats; the user restarted the app after installation. Existing stored response chains were preserved.

The live memory tool confirmed a separate temporary marker save and the marker appeared in the native on-disk store. A fresh app chat used a distinct native session and recalled the exact marker without session search, other retrieval tools, or the marker being included in its user prompt. The marker was removed through the native memory function afterwards. This verifies one curated-memory path, not general memory quality, session-search recall or learned skills.

Five top-level model requests were used. Two were an initial memory save/cleanup because the test inspector initially expected single operations and missed Hermes' valid batched memory format. The subsequent save and recall passed after the inspector was corrected; final cleanup required no inference. No dollar-cost or provider-attempt count was measured. Real interrupted-model-execution recovery remains open; personal Calendar read access was subsequently verified below; specialist evidence is recorded below.

## Specialist experiment, 27 October 2026

Two bounded app requests each delegated a synthetic stock assessment to supplies, then passed the complete result to meals for three vegetarian dinners for two. Native session traces show two distinct children per request and completion of supplies before the meals child started. The stateless Responses path joined children synchronously and returned their results in the app response; it did not require an extra client continuation. Tasks and curated-memory files were unchanged in both runs. The initial oversized prompt was rejected before model execution and was shortened to fit the app limit.

The first experiment revealed two validation gaps. The model pasted schemas into child context without passing `tasks[].output_schema`; both outputs passed independent offline schema validation, but native enforcement had not run. The lean Hermes environment also omitted `jsonschema`, so its native validator silently accepted parseable JSON without schema checks. Tightened all three skill instructions, installed the five dependencies in `hermes-validation-requirements.txt` using versions from the pinned Hermes lockfile, and added a wrapper check plus `scripts/hermes_schema_smoke.py`. The smoke accepts a matching fixture and rejects wrong owner/acceptance fields without inference. Reinstall the requirements after upstream `uv sync`.

The repeat passed complete schema objects in both task arguments and both native results reported completed status, completed exit reason and `schema_valid: true`. Independently validated those results, checked exact equality with the result objects nested in the final app reply, verified the full supplies assessment reached meals, and confirmed unknown oil/salt and missing eggs/cheese remained labelled in supplies. Three dinners for two were returned as proposals. Two top-level requests spawned four children total; parent reasoning and any validation retries are additional provider calls, so this is not a four-call or dollar-cost claim. Private reports are `.local/live-specialist-check.json` and `.local/live-specialist-validated-check.json`.

Quality limits remain: cooking time and budget are untested model estimates. The meals schema lacks dedicated fields for these estimates, stock uncertainty and drained-versus-can-weight conversion; some estimates appeared in meal names. This experiment proves the sequential handoff, native output validation and synchronous result delivery. It does not establish a production meal-planning workflow, accepted grocery calculation, stock subtraction, live prices, AnyList writes or background event continuation.

## Keep the environments separate

- Application: `.venv`, managed by root `pyproject.toml` and `uv.lock`.
- Hermes: `.local/hermes-runtime/.venv`, managed by its upstream frozen lockfile.
- Hermes user state: `.local/hermes-home`; additional runtime state: `.local/hermes-managed`.
- Source pin: `hermes-runtime.ref`. The third-party checkout and its environments are ignored.
- The upstream `messaging` extra is required for the gateway HTTP adapter's `aiohttp` dependency; it is installed in the Hermes environment only.
- No global package install, no changes to `~/.hermes`, and no automatic provider login.

The `scripts/hermes.sh` wrapper selects the repo-local Hermes home for every native command.
`setup-hermes` copies the current plugin and persona into that home, creates gateway/tool
credentials in private files, and binds the application's adapter to the personal household.
Run it again after changing the plugin to refresh that copy. It preserves existing config.

It also merges required native toolsets, enables curated memory, restores skill-learning nudges, configures explicit credential passthrough for the reminder script, and creates the dispatcher job only if it is missing. It preserves an existing job rather than resetting a paused job or creating duplicates. A gateway restart is required after configuration changes.

## Learning experiments, in order

1. With AI off, create a reminder, close/reopen the browser, and mark the task done.
2. Use the actual Hermes plugin-loader smoke script. This exercises real registration and
   handlers without paying for inference.
   Run `scripts/hermes_features_smoke.py` with Hermes' Python to exercise native memory persistence and the no-agent cron runner as well.
3. Configure a provider in the isolated Hermes home. Ask in Hermes CLI: “What time is it in
   my household?” and “Remind me in ten minutes to buy a birthday card.” Inspect tool calls;
   confirm the task in the web app. Native CLI calls without an application conversation ID
   create a separate saved tool conversation.
4. Test the web adapter through the gateway. Make two related requests, refresh the page,
   then continue. Verify native Hermes history and the app's visible transcript.
   The private API's authenticated capabilities endpoint is reachable and two live turns succeeded. The affected chat recovered after restart; a separate live check also verified native memory recall in a distinct fresh session. These bounded checks do not establish general multi-turn reliability. See [local setup](local-setup.md) for the opt-in smoke check and [handoff](handoff.md) for the targeted incident check.
5. Request gift suggestions with one small delegation. Inspect iteration/token usage and
   whether the final result returns correctly through the gateway. Background delegation
   can require a subsequent client turn; this POC adapter does not yet implement its event
   continuation protocol. The tested stateless Responses path joins children synchronously; background continuation remains separate unverified work.
6. Stop/restart the app. Pending work persists; in-flight work is marked interrupted rather
   than blindly replayed. A native Hermes call may continue after an application timeout:
   inspect saved tasks and Hermes state before retrying it.

The persona asks Hermes to use structured household tools for task state, native memory for stable preferences and lessons, skills for procedures, and native cron for timing and scheduled agent work. Household date validation, credential scoping and duplicate-request checks are enforced in Python. Instructions alone do not guarantee correct tool use.

## Google Calendar probe

Verified on 4 November 2026 against the user's personal account and project `mental-loader-513621`. Calendar API was enabled and OAuth registered in External/Testing mode, with the personal account as the sole test user. The user created/downloaded the Desktop client, stored it as `.local/google-client.json` with mode `0600`, and personally completed consent in Chrome. The existing probe successfully read ten upcoming primary-calendar events with exactly `calendar.events.readonly` scope. A separate count-only query for birthday events over the next 366 days returned one instance and no next page; the user confirmed one birthday was expected. No event names/descriptions/dates were logged, no Google events were changed, and no model calls were made. The token was saved privately with mode `0600`; evidence is `.local/google-calendar-check.json`.

This verifies API access and a user-confirmed count for the current primary calendar. It does not establish exact birthday identity matching, secondary-calendar completeness, Google Contacts completeness, automatic Calendar sync, or a Calendar tool exposed to the agent. The current Desktop client is for the local deterministic probe; hosted linking needs a separate web OAuth setup. External/Testing authorizations and refresh tokens expire after seven days for these scopes, so re-consent may be needed on resume. Do not silently publish the OAuth app to change that policy. [Google OAuth token-expiry documentation](https://developers.google.com/identity/protocols/oauth2).

1. Create/select a Google Cloud project and enable Google Calendar API.
2. Configure OAuth consent for testing and add your Google account as a test user.
3. Create a **Desktop app** OAuth client and save the downloaded JSON as
   `.local/google-client.json` (never commit it).
4. Copy your household ID from `.local/access-codes.json`, then run:

```sh
uv run python -m mental_loader.cli google-probe --household-id YOUR_HOUSEHOLD_ID
```

It opens Google's consent page using the installed-app loopback flow, requests
`calendar.events.readonly`, reads at most ten upcoming primary-calendar events, and prints
only the count and connection metadata. A token is kept in a household-specific private
local file. No events are written or synced into household tasks. Production account linking
will need a web OAuth flow and encrypted token storage. Calendar birthdays and additional
calendars need separate validation; this probe is not proof of complete birthday coverage.

Official setup: <https://developers.google.com/workspace/calendar/api/quickstart/python>.

## Real-device push

Verified on 2 November 2026 using the installed iPhone PWA and a temporary trusted HTTPS tunnel. HTTPS login and Secure/HttpOnly session cookies passed; one Apple subscription was saved. Native cron processed an open test reminder, and the persistent outbox recorded `sent` after one attempt. Separately, the user confirmed that the notification appeared on the locked phone and opened the correct saved conversation. This test used no inference. The earlier attempt was completed by tapping its checkmark before due time, so it correctly produced no dispatch or notification.

After confirmation, the test task was completed, its temporary phone registration removed from the backend, and the tunnel closed. Original localhost origin/cookie settings were restored on disk; the user restarted the app and localhost login was verified. VAPID keys and saved chat were preserved. The old Home Screen shortcut points to an expired temporary origin and is not an ongoing phone deployment. A future phone setup needs a live trusted origin and a new subscription there. Evidence is private in `.local/phone-push-test.json`. Graceful app/gateway downtime recovery was subsequently verified on 4 November as described below; laptop power loss/sleep and push-outbox recovery remain unverified.

`init` generates a VAPID private key with restrictive permissions and writes its public key
to the app's `.env`. Set `VAPID_SUBJECT` to your email and restart the app before live push.

On a laptop, localhost is a secure context. On a phone, an HTTP LAN address is insufficient
for service workers/push. Use trusted HTTPS (local certificate or a deliberately configured
development tunnel), update `PUBLIC_ORIGIN` to that exact origin, and set `COOKIE_SECURE=true`.
The default server binds only to loopback. If using a tunnel, point it at localhost:8000;
do not publish this POC to friends yet.

On iPhone, add the HTTPS app to Home Screen, open it there, sign in, and enable notifications.
Send `/remind 1m Test phone push`, leave its checkmark untouched, close the app and lock the screen. The checkmark completes the task and suppresses its reminder. Confirm a notification arrives, opens the correct chat, and the same reminder exists in history. Keep both the backend and Hermes gateway/scheduler running on the awake laptop. The one-minute scan cadence can add approximately a minute of delay. A provider accepting a push message is not proof the phone displayed it.

Push text is generic to avoid putting household or health details on a lock screen. The
outbox records pending/sent/failed/expired states. Delivery is at least once, not exactly
once; notification tags help replace duplicates. No device subscribed at due time means an
in-app reminder only, not a successful push. Network failures retry up to three attempts.

Apple reference: <https://webkit.org/blog/13878/>.

## Graceful service downtime, 4 November 2026

A new deterministic reminder was saved through the existing authenticated backend before stopping the verified project launcher. Both app (8000) and Hermes (8642) ports closed before the deadline; PostgreSQL remained running. After the deadline, the task was still open and saved, with no notification timestamp or delivery row. The user restarted `make dev`; native cron then produced exactly one reminder message for exactly one task. A later successful native scan produced no duplicates. The test task was completed afterwards and the chat retained. No model calls or manual dispatch actions were used. Private evidence is `.local/downtime-check.json`. The earlier 2 November timing attempt was superseded and is not counted as a pass.

This verifies late in-app reminder recovery after a graceful stop of both local services. It does not simulate a laptop power cut/sleep, database outage, interrupted paid provider request or retry of a previously queued phone delivery. The temporary phone registration had already been removed, so this test does not establish push recovery after downtime.

## Deliberate POC limitations

- The original personal instance still uses household codes. Phase 2 source now implements personal identities, roles, one-time recovery and hosted-mode authentication requirements; it has not been applied to that instance. Public signup and email recovery are not implemented.
  Cookies are HttpOnly, mutations require the configured same origin, and the model's tool
  credentials resolve a household server-side. This is not ready for public hosting.
- Household tools can read/write their household records. A household access code is an
  owner-level credential, not a member-scoped credential. Never give one household's code
  to another household's runtime.
- No row-level security, schema migration framework, encrypted-at-rest OAuth tokens, login
  rate limiting, account recovery, comprehensive data deletion or production backup setup yet.
- Single application instance with one chat-request worker, plus one dedicated Hermes runtime and its native scheduler. Do not run multiple web workers or duplicate reminder dispatchers: startup recovery and delivery claiming are not yet safe for that topology.
- Chat fetches messages and tasks every 2.5 seconds while the selected conversation has active work and every 30 seconds while idle, after reads finish. Actions and returning to a visible tab refresh immediately; hidden tabs stop polling and refreshes do not overlap. Idle in-app reminders can take up to about 30 seconds plus request latency to appear. These reads use no model credits; Web Push and the Hermes scheduler are independent.
- Chat uses polling rather than streaming. A request continues after the browser closes,
  but an app restart can interrupt work. Hermes session persistence is separate from our
  application transcript. A framework migration requires explicit export/adaptation of native memory, skills, schedules and session data; internal execution state is not automatically portable.
- Completed tasks cancel queued push attempts, but a send already in progress may arrive.
- No user-facing recurring task records, quiet hours, digests, snooze or calendar sync yet.
- Hermes supports recurring native jobs, but user-facing recurring household task records, quiet hours and digests have not been implemented. Only the personal household has a configured native dispatcher in this spike.
- Request limits are not hard provider spend limits. Configure a provider-side budget and
  review delegated calls. No paid inference was needed for the deterministic checks.

## Source references for Hermes behaviour

- Plugin registration: <https://hermes-agent.nousresearch.com/docs/developer-guide/plugins>
- Responses/conversation API: <https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server>
- Delegation: <https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation>
- Native scheduling and no-agent jobs: <https://hermes-agent.nousresearch.com/docs/user-guide/features/cron>
- Native curated memory and session boundaries: <https://hermes-agent.nousresearch.com/docs/user-guide/features/memory>

The runtime's actual source pin was inspected alongside the docs. Recheck interfaces before
upgrading Hermes; its extension and execution surfaces are evolving.

# Phase 1: technical feasibility

Phase 1 evidence last updated 27 October 2026; sharing-foundation note updated 25 November 2026. Phase 1 remains in progress. Read [development handoff](handoff.md) for the current incident, operational state and ordered next actions.

Phase 2 sharing foundations are now implemented and separately verified in the worktree; see the [phase 2 acceptance matrix](phase-2-accounts.md). The existing personal instance has not been migrated. Phase 2 acceptance does not close the live-provider, phone-push or Google-consent evidence gaps below.

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
| Does the app work on laptop and phone layouts? | Responsive PWA; browser script tests 1440px and 390px layouts | Actual iPhone installation/usability |
| Can users see saved conversations? | PostgreSQL messages and durable request records; browser reload test; user received two live OpenRouter responses | Reliable continuation after the third-turn encrypted-history rejection and recovery patch |
| Can Hermes call deterministic household code? | Native plugin registered by the actual Hermes plugin manager; clock, save reminder, duplicate retry and list tasks exercised against local PostgreSQL | Model selecting the correct tools in natural conversation |
| Can code run without models? | `/remind` saves tasks without inference; the running native Hermes scheduler processed a due reminder after about 60 seconds without manual dispatch | Recovery of work interrupted during gateway restart |
| Are native memory and skills used? | Native memory tool persisted a synthetic preference across store reloads; a second home did not see it. Memory, session search and skills toolsets are enabled; meals/supplies/orchestrator skill drafts and output contracts installed | Live conversational memory/recall, specialist execution and learned skill creation |
| Are household records separated? | Phase 2 adds identities/roles, private participants, account integrations, ancestry controls, scoped runtime containers, device/session notifications and adversarial tests | Hosted invitations/deployment review; see phase 2 for current implementation rather than the old shared-code shortcut |
| Can reminders notify outside the app? | Web Push transport, service worker, VAPID keys, persistent outbox, retry/expired-subscription tests | Real subscription and lock-screen delivery, especially iPhone |
| Can Google Calendar be read? | Read-only OAuth probe provided; account credentials are not included | Authorise a test account and run probe |
| Can AnyList be updated? | No supported public integration established from reviewed official help pages | Confirm a supported integration with AnyList; do not promise sync |

Run the commands in README to reproduce the evidence. A fake model response or mocked push
transport is not evidence of a working live provider or real phone notification.

Hermes owns reminder timing; the application reminder loop has been removed. `setup-hermes` creates a native `mental-loader-reminders` script-only job with a one-minute interval. Its script calls the authenticated household dispatch action, which handles structured task state and the custom PWA push transport. Both direct no-agent execution and an actual gateway-driven scheduled dispatch have been observed. The cadence check completed after approximately 60 seconds; phone push display is still unverified.

## Native feature coverage

| Hermes feature | Current use | Evidence boundary |
| --- | --- | --- |
| Cron scheduler | Native one-minute, no-agent reminder-dispatch job; additional agent-backed jobs can be managed with native cron tools | Native runner and actual gateway-driven cadence tested without inference; interrupted-execution recovery remains to be tested |
| Curated memory | Native `USER.md` / `MEMORY.md` for preferences, routines and lessons in the household's dedicated home | Synthetic preference saved with the native memory tool and read from a fresh store; conversational recall remains unverified |
| Sessions and recall | Stable Hermes conversation mapping and enabled `session_search`; two successful live responses reported | Reliable continuation after encrypted-history recovery and explicit cross-session recall remain unverified |
| Skills | Native skills tools and learning nudges enabled; initial meals/supplies/orchestrator skills and handoff contracts installed | Native discovery can list the prepared skills; live specialist execution and learned improvements remain unverified |
| Delegation | Native delegation tools enabled with bounded iterations/concurrency | Configuration enabled; live specialist execution and API completion delivery remain unverified |

The PWA transcript, structured household database and push sender support the product interface. They do not replace Hermes' native memory, session management, skills or scheduling. Portability is concentrated in household functions and integration adapters; a future migration must also export/adapt native state.

## Live testing and recovery incident

The user configured OpenRouter and received two successful live chat responses. Logs identified `openai/gpt-6-luna`. A third message failed on 26 October and a subsequent attempt failed on 27 October with `invalid_encrypted_content`. Persisted reasoning included both OpenAI and Azure formats; why those encrypted blocks became invalid has not been established. The confirmed recovery gap in the pinned Hermes source was that direct Responses replay had a repair path while this OpenRouter chat-completions path retried unchanged history.

The versioned `patches/hermes-openrouter-replay.patch` adds a bounded repair for this specific route/error, removing encrypted reasoning from canonical/request history while retaining visible messages, tool calls, receipts and readable summaries. The wrapper/setup apply it idempotently. A running gateway needs a restart to load it. The real Hermes classifier and patched handler passed a synthetic regression test; the user has not yet confirmed a successful live retry after restart. Do not report the provider issue as fully resolved.

Last verification: 28 application tests passed, lint passed, native recovery smoke passed, patch reapplication was idempotent and shell syntax checks passed. No paid calls were used for these checks. The app already rejected failed Hermes response-body statuses on HTTP 200; recent diagnostics retain failed-turn usage and add safe failure categories/request references. See [logging details](local-setup.md#diagnosing-chat-failures).

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
   The private API's authenticated capabilities endpoint is reachable and two live turns succeeded. Confirm continued conversation after the recovery patch before claiming multi-turn reliability. See [local setup](local-setup.md) for the opt-in smoke check and [handoff](handoff.md) for the targeted incident check.
5. Request gift suggestions with one small delegation. Inspect iteration/token usage and
   whether the final result returns correctly through the gateway. Background delegation
   can require a subsequent client turn; this POC adapter does not yet implement its event
   continuation protocol. Use direct CLI first to understand this distinction.
6. Stop/restart the app. Pending work persists; in-flight work is marked interrupted rather
   than blindly replayed. A native Hermes call may continue after an application timeout:
   inspect saved tasks and Hermes state before retrying it.

The persona asks Hermes to use structured household tools for task state, native memory for stable preferences and lessons, skills for procedures, and native cron for timing and scheduled agent work. Household date validation, credential scoping and duplicate-request checks are enforced in Python. Instructions alone do not guarantee correct tool use.

## Google Calendar probe

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

`init` generates a VAPID private key with restrictive permissions and writes its public key
to the app's `.env`. Set `VAPID_SUBJECT` to your email and restart the app before live push.

On a laptop, localhost is a secure context. On a phone, an HTTP LAN address is insufficient
for service workers/push. Use trusted HTTPS (local certificate or a deliberately configured
development tunnel), update `PUBLIC_ORIGIN` to that exact origin, and set `COOKIE_SECURE=true`.
The default server binds only to loopback. If using a tunnel, point it at localhost:8000;
do not publish this POC to friends yet.

On iPhone, add the HTTPS app to Home Screen, open it there, sign in, and enable notifications.
Send `/remind 1m Test phone push`, close the app and lock the screen. Confirm a notification arrives, opens the correct chat, and the same reminder exists in history. Keep both the backend and Hermes gateway/scheduler running on the awake laptop. The one-minute scan cadence can add approximately a minute of delay. A provider accepting a push message is not proof the phone displayed it.

Push text is generic to avoid putting household or health details on a lock screen. The
outbox records pending/sent/failed/expired states. Delivery is at least once, not exactly
once; notification tags help replace duplicates. No device subscribed at due time means an
in-app reminder only, not a successful push. Network failures retry up to three attempts.

Apple reference: <https://webkit.org/blog/13878/>.

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
- Chat currently fetches messages and tasks every 2.5 seconds while visible, including idle periods. Adaptive active/idle polling is the agreed next change and is not yet implemented. These reads use no model credits.
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

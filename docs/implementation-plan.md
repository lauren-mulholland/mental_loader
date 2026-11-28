# Implementation plan

Agreed baseline: 26 October 2026; priorities updated 27 November 2026. Build the smallest useful household workflow first, learn from real use, and preserve clear application/agent boundaries. For the current working state and ordered next actions, start with [development handoff](handoff.md#next-thread-ui-design-and-hermesapplication-boundary).

## Current delivery order — 27 November 2026

The fresh personal Railway deployment is live and the user confirmed sign-in. The next
requested work is **UI design and implementation of a logical boundary between Hermes and
custom code**. This supersedes the deployment/onboarding order below.

1. Audit existing screens and trace direct UI and agent actions to identify shared rules
   and current coupling. Design minimal mobile/desktop category navigation with agent access.
2. Define the application operations used by both UI APIs and thin agent tools. Keep auth,
   validation, transactions, external adapters and authoritative records in custom code;
   keep native reasoning, sessions, curated memory, skills and delegation in Hermes.
   Preserve the current no-agent scheduler/dispatch split rather than adding another timer.
3. Implement and verify one vertical slice through that boundary. Reuse existing birthday
   UI/domain code where helpful; first category and final visual design are not yet chosen.
   Routine supported record operations must work with AI unavailable.
4. Add adaptive category onboarding and further Events, Meals, Shopping and Stock views
   incrementally. Resolve editable preference ownership and correction propagation before
   creating competing app/native-memory copies.

The workspace subscription/cap mismatch and hosted phone-delivery check remain separate
operational follow-ups; see the handover. Do not change subscriptions or push to GitHub
without authorization. This planning update does not implement the UI or boundary refactor.

## UI/boundary slice implemented locally — 27 November 2026

The first Events slice is implemented: category navigation, direct birthday/reminder forms, separate Assistant/Settings, and shared `HouseholdOperations` behind browser/tool endpoints. Meals, Shopping and Stock are explicitly planned screens. Preference ownership and future proposal acceptance are specified in [UI and application boundary](ui-and-application-boundary.md). Full onboarding, broader category CRUD and production release remain follow-ups. The ordered brief above is retained as the starting context; inspect the implementation before repeating its audit/refactor work.

## Hosting update — 26 November 2026

Railway is now the default host, superseding the Render choice below. Target US$5–10/month
with an explicitly requested US$10 Compute hard cap and accepted shutdown. Configure that
workspace limit before provisioning. Follow [deployment](deployment.md), restore state and
personal accounts before enabling processing, and connect the app services to GitHub main.
The initial IaC apply remains necessary; app pushes do not apply infrastructure settings.
No Git push is authorized. The 27 November UI/boundary priority above now takes precedence.

## Earlier delivery order (25 November 2026; superseded above)

The user resumed work and accepted Render for a private personal deployment. This supersedes
the previous pause, Vercel preference and the requirement to finish unrelated integration
experiments before personal hosting. Retain existing evidence and limitations; do not label
all Phase 1 gates complete merely because the app is hosted.

1. Package and deploy the current app, one dedicated Hermes runtime with persistent storage,
   and PostgreSQL. Preserve current personal state, configure backups and demonstrate
   chat/reminders with the laptop off and after a redeploy. See [deployment](deployment.md).
2. Add adaptive onboarding: select categories, ask focused questions, save/resume progress,
   review/correct answers and produce a useful first meal proposal.
3. Add minimal category views incrementally: events, meals, shopping and stock/supplies.
   Direct list/stock operations use shared domain functions and work without model calls.
4. Continue capability and integration work based on personal use. Complete sharing
   foundations before inviting other households.

The app-with-agent direction is specified in [product requirements](product-requirements.md).
The historical phases below remain a capability roadmap, subordinate to this delivery order.

## Phase 1 — Technical feasibility

Build and test the responsive local app, saved history, deterministic reminder functions, native Hermes plugin, Hermes API adapter, native memory, skills/session-recall availability, native no-agent scheduling, Google Calendar read probe and Web Push path. Hermes should own runtime services rather than act only as a model endpoint.

Current evidence is recorded in [feasibility.md](feasibility.md). Automated code tests, desktop/mobile browser checks, native Hermes plugin execution, memory persistence across store reloads, direct no-agent execution and actual gateway-driven scheduled dispatch have passed. The user configured OpenRouter and received two live chat responses, then encountered an encrypted-history rejection on the third message. The targeted recovery patch passed offline checks and a live continuation of the affected chat after restart, with exact first-message recall and unchanged tasks. Live natural-language reminder tool use, browser history after reload and curated-memory recall in a distinct fresh session have now passed. A bounded sequential supplies/meals experiment with native schema validation and app result delivery also passed. iPhone lock-screen notification display and correct-chat navigation were confirmed on 2 November. Graceful app/gateway downtime recovery and absence of duplicates on a later native scan passed on 4 November. Personal Google read access also passed on 4 November: ten upcoming primary events and one birthday matching the user’s expected count. Learned skill creation, interrupted model execution, power-loss/push-retry recovery and AnyList feasibility remain open. Phase 1 is not complete.

Recovery in the affected chat is confirmed and adaptive active/idle frontend polling is implemented with browser regression coverage. Live reminder tool use and fresh-session curated-memory recall are now verified. The small sequential specialist handoff is now verified; the real-device push check also passed on 2 November. Graceful app/gateway downtime recovery is now verified. Personal Google Calendar read-only feasibility is now verified. Next establish supported AnyList access or an agreed alternative and review remaining Phase 1 gates; broader failure recovery and automatic Calendar sync remain open. The previous pause was lifted on 25 November 2026; follow the current delivery order above. See the [handoff](handoff.md) for evidence and limitations.

Exit criteria:

- A user can sign in on a laptop, send a request, reload, and see saved history.
- Hermes actually selects and executes a household tool with a configured live provider.
- A deterministic reminder is stored and processed without inference.
- Hermes' memory tool persists a preference across a fresh store/session, with no leakage to a separate home; verify conversational recall with a live provider.
- Native cron invokes the reminder action without inference; test actual recurring cadence and stop/restart behaviour after the gateway is running.
- A notification reaches an actual phone with the app closed and opens the correct chat.
- Google read access is demonstrated without writing events.
- AnyList feasibility is classified honestly as supported, blocked or requiring an alternative.
- Costs and runtime limitations are recorded; model mocks are labelled as mocks.

## Phase 2 — Foundations for sharing (consolidated locally 26 November 2026)

Completed in source and local acceptance checks on 25 November 2026 at the user's request. [Sharing foundations](phase-2-accounts.md) now include personal identities/recovery, roles, participants, five migrations and ancestry enforcement, account-scoped encrypted Google integration, attested containerized runtime routes/credentials, persistent quotas, leased/fenced jobs, device/session-bound notifications, per-account usage and encrypted backup/restore. All 66 tests pass on SQLite and PostgreSQL. Browser sign-in/recovery/private-chat checks and actual native container isolation, gateway/cron and fresh-volume restore checks pass. The original personal instance was not changed and no model credits were used. Remaining live-provider, phone-push and Google-consent checks are phase 1 gates; hosted deployment and invitations remain phase 5. Next feature work is phase 3.

Introduce individual identities, households, owner/member roles, conversation participants,
versioned database migrations, stronger isolation controls and account-scoped integrations.
Validate separate Hermes contexts and credentials before connecting additional households.
Add safe job claiming, rate limits, secrets handling and usage accounting as needed for hosting.

Exit criteria met in the local acceptance environment: independently authenticated test accounts cannot access each other's private records, native model context, scoped tools or notifications, including through background jobs and guessed resource IDs. Provider behavior is mocked where noted; native runtime storage, containers and credential boundaries are exercised directly.

Do not invite friends based only on the access-code spike's isolation tests.

Phase 2 implementation now includes personal accounts, private conversation permissions,
versioned migrations, encrypted connections/backups and leased workers. Its local scoped
Docker routes are retained; Render uses the explicit one-household route described in
[deployment](deployment.md). Multi-household hosting is not established by local tests.

## Phase 3 — Birthday and gift workflow

The minimal direct-edit dated birthday workflow and gift specialist are now retained from
the reviewed birthday commit; account-linked selected-event import and background sync are
deferred. Continue expanding gift recipients, preferences, budgets, occasion dates, preparation lead times and
purchase/completion status. Import relevant Google events and handle updates/cancellations.
Use a specialist for suggestions; keep deadline calculation and integration/delivery functions in code while Hermes owns scheduled execution. Use native memory for gift interests and preferences and skills for repeatable preparation procedures.

Exit criteria: one birthday is handled from discovery to completed preparation, without
duplicate tasks or reminders continuing after the relevant work is finished.

## Phase 4 — Personal pilot

Use the assistant for roughly two weeks. Add notification preferences, quiet hours, digest
grouping and snooze based on observed needs. Test short follow-ups and corrections.

Record missed reminders, unwanted reminders, incorrect assumptions, corrections, task
completion and per-workflow model usage. The useful outcome is less effort for the user,
not a high count of agent runs or notifications.

## Phase 5 — Hosted friends pilot

Extend the private Render deployment to support isolated households. Retain persistent
PostgreSQL and Hermes homes, HTTPS, backups, restore instructions and monitoring. Native
memory, skills and cron state must survive redeployments alongside application data.
Complete the account and isolation work before inviting a few households.

Exit criteria: the service works with the developer's laptop off; backup restoration has
been demonstrated; failures are visible; household budgets and permissions are enforced;
a friend can onboard into an isolated household.

## Phase 6 — Expand one capability at a time

1. Meals/groceries: preferences, staple-versus-stock records, shopping routine, a small meal
   plan and accepted grocery updates. Test through a real shopping cycle.
2. Appointments/supplies: household members, supplied dates, preparation and booking
   follow-ups, replenishment reminders and changes/cancellations.
3. Home tasks: recurring chores, maintenance, seasonal work and explicit ownership.

## Verification strategy

Use ordinary automated tests for dates, offsets/daylight saving, state transitions,
idempotency, tenant isolation, retry handling and integration contracts. Use a controllable
clock instead of waiting through real calendar periods.

Evaluate agent behaviour separately: tool choice, handling ambiguity, factual grounding,
following corrections, appropriate delegation and truthful reports of completed actions.
Inspect real traces and costs; do not treat string assertions on a fake answer as live-agent
quality evidence. Include restart and uncertain-execution tests to avoid duplicate actions.

## Future runtime migration

There is no fixed deadline to replace Hermes. Consider an SDK-based agent if evidence shows
that required workflow control, multi-tenant isolation, observability or lifecycle management
is disproportionately difficult in Hermes. Keep domain functions and integration contracts
reusable, but expect to adapt agent definitions, tools, sessions and delegation.

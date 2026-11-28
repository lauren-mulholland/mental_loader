# Design decisions

Agreed baseline: 26 October 2026; updated 4 November 2026. Accepted choices guide implementation; provisional choices and unresolved integrations should be revalidated as the POC produces evidence. See [development handoff](handoff.md) for current execution status.

| Decision | Status | Rationale and consequence |
| --- | --- | --- |
| Use Hermes as the actual runtime | Accepted | Learn native scheduling, curated memory, session recall, skills and delegation in a real household scenario. Do not reimplement these services to anticipate a possible migration. |
| App exposes MCP; Hermes consumes it | Accepted 27 November 2026; implement after design review | Expose bounded application tools through a thin MCP server over shared application operations. Direct UI and MCP use the same rules, records and scoped permissions. See [MCP application interface](mcp-application-interface.md) for bulk-edit semantics and migration from the current native plugin. |
| Keep household logic independent | Accepted | Plain Python owns required household rules and integration actions. Hermes tools remain thin wrappers. Runtime state stays native to Hermes; migration would reuse the functions and export/adapt memory, skills, sessions and schedules. |
| Combine deterministic code and agent work | Accepted | Hermes controls scheduled execution and agent behaviour. Python functions enforce validation, state changes, calculations and integration actions. Native no-agent cron runs deterministic actions without inference. Prompt instructions are not workflow guarantees. |
| Laptop and phone share a responsive PWA | Accepted | Saved chat and a small task view work across devices with little frontend overhead. Avoid native iPhone distribution and app-review overhead. WhatsApp is optional future work. |
| Use Hermes sessions and keep a visible transcript | Accepted | Hermes manages agent context and recall. The app stores the messages shown in the PWA so they remain visible after refresh or device changes; that transcript does not replace native session memory. |
| Household is the tenant boundary | Accepted | Support future sharing with friends. Authentication, records, integrations, jobs, memory and notification recipients must all respect household boundaries. |
| Limit the existing personal runtime to one household | Preserved compatibility boundary | The original instance is unchanged. Phase 2 adds separately attested per-scope containers; never point unrelated households or private conversations at the personal shared runtime. |
| Orchestrator plus specialists | Accepted direction | Split gifts, meals, appointments/supplies and home tasks by responsibility. Introduce specialists incrementally and invoke only the necessary ones. |
| Google Calendar first | Accepted | First calendar feasibility target selected by the user. Personal read-only authorisation and primary-calendar birthday count were verified on 4 November; broader calendar coverage and automatic sync remain future work. |
| Investigate AnyList | Provisional | Preferred grocery-list integration, but a supported write/sync API has not been established. Keep an internal list/export fallback in scope. |
| PostgreSQL from the start | Accepted direction | Use the same database family locally and when hosted. Adds Docker setup now, reduces a later persistence migration. |
| Separate app and Hermes environments | Accepted | Each has an isolated venv and lockfile. Pin the upstream Hermes revision; keep its home and credentials inside ignored local state. |
| Render for private personal deployment | Accepted 25 November 2026; supersedes Vercel preference | Keep the current FastAPI app, persistent private Hermes service and PostgreSQL on one PaaS. Deploy existing functionality before onboarding; verify costs, state transfer, backups and restart behaviour. |
| Household app with embedded agent | Accepted 25 November 2026 | Add category views for events, meals, shopping and stock over time. Direct UI actions and agent tools share domain functions and authoritative records. Use the agent for judgement and suggestions; routine CRUD needs no inference. |
| Adaptive onboarding after deployment | Accepted 25 November 2026 | Ask which areas to manage, then focused category-specific questions. Make progress resumable and answers editable. Start with meals; separate usual staples from confirmed current stock. |
| Deterministic reminders work with AI off | Accepted | Protect basic usefulness and cost control when provider access fails or request limits are reached. |
| Use native memory and skills | Accepted | Keep stable household preferences and lessons in the dedicated Hermes home; use native skills for repeatable procedures. Task state and delivery receipts remain structured records. Export/adapt native state later if migration becomes necessary. |
| Use Hermes cron for reminder timing | Accepted | One native script-only job dispatches due tasks every minute without a model. The PWA push sender remains a custom transport because Hermes does not supply our web-app notification interface. |
| Proactive delivery is a core feature | Accepted | The assistant must reach users instead of requiring them to remember to check it. Real-device push is a feasibility gate, not an assumed success. |
| Keep costs visible and bounded | Accepted | Track usage by household/workflow, limit iterations and delegation, and use provider-side budget controls. Top-level request limits do not cap dollars or all subagent calls. |
| Use OpenRouter for the learning POC | Current user configuration | User supplied the key locally and configured Hermes. Logs identified `openai/gpt-6-luna`; this is an experimental selection, not a permanent model commitment. |
| Reduce idle frontend polling | Agreed next direction; pending implementation | Poll quickly while a reply is pending, around every 30 seconds when idle, refresh after actions/on tab return and pause hidden tabs. Preserve the separate native reminder schedule. SSE can wait. |
| Track narrow upstream compatibility fixes | Implemented workaround | Keep the pinned-runtime encrypted-reasoning recovery patch in version control, apply it idempotently and refuse conflicts. Preserve native runtime ownership and review whether the patch is still needed on upgrade. Live recovery remains unconfirmed. |

## UI and boundary decisions — 27 November 2026

Events is the first implemented category, reusing dated birthdays and preparation tasks. Assistant and Settings have dedicated views; Meals, Shopping and Stock have explicit planned-capability screens. Preserve conversation-based privacy for existing records. Browser and tool adapters invoke shared application operations with a server-resolved principal; operations own scope checks and transactions. General preferences remain native curated memory, while occasion notes stay on their dated record. Future structured settings must be injected into current agent context without becoming a duplicated native-memory authority. Meal/shopping proposals require an explicit acceptance operation before transactional writes. See [UI and application boundary](ui-and-application-boundary.md) for the implemented scope and future acceptance/correction contracts.

## Hosting budget — 26 November 2026

Railway Hobby replaces Render as the selected host to target US$5–10/month before model
usage/tax. The user explicitly accepts service shutdown at a US$10 Compute hard limit.
The workspace cap must be set and verified outside the repository before provisioning;
local memory/CPU limits do not enforce a dollar ceiling. Keep sleeping disabled so reminders
work without the laptop until the cap stops the services. Both app services now reference GitHub main at the user's preference; connect the GitHub app
and apply IaC once before relying on source autodeploys. Do not push without permission. Use the supported Railway
TypeScript IaC format and preserve the same PostgreSQL/native-Hermes ownership split.

## Consolidation decisions — 26 November 2026

Merged Phase 2 (`ffc97d6`) into local main and retained the existing Render packaging,
adaptive polling and per-chat Hermes session identity. The birthday commit (`1143534`) was
reviewed selectively: keep its dated record, deterministic deadline/reconciliation helpers,
minimal direct-edit panel, gift specialist and native tools. Adapt them to Phase 2 permissions,
worker semantics, encrypted backups and migration 006. Do not copy its competing password
accounts, migration module, encryption configuration or account-Calendar implementation.
Calendar import helpers remain internal; selected-event import routes/background sync are
not exposed by this consolidation. Original live evidence from that worktree is not proof
that this combined build has performed those live operations.

Render uses an explicit personal PaaS route with separate agent tool credentials, secure
personal browser login and a persistent application encryption key. The default hosted mode
still rejects the old single-runtime fallback. The explicit route permits only shared chats
of the configured household; private chats and unrelated households have no fallback.
Docker sandbox attestation is retained for locally managed scoped runtimes, not claimed for
Render. This avoids mounting a Docker socket in the public web service. No remote push is
authorized; local integration and commits only.

## Deliberate simplifications

The bounded pilot uses high-entropy individual credentials and one-time offline recovery instead of public signup or an external identity provider. Legacy household browser codes remain only before enrollment, and legacy tools/single-runtime routing are forbidden in hosted mode. The personal POC keeps its existing setup. Sharing foundations now include transactional migrations, account-scoped encrypted integrations, private participants, restricted per-scope runtime containers, persistent quotas and leased jobs. Public signup/email recovery, cloud provisioning and operational deployment remain later hosting work; see [phase 2 acceptance](phase-2-accounts.md).

The PWA provides an optional product interface; native Hermes CLI is also an important
learning surface. Use it to inspect tool selection, memory, delegation and failure behaviour
before hiding those details behind the web application.

## Open decisions and evidence needed

- Long-term provider/model choice based on cost and observed tool reliability; OpenRouter is already configured for current tests.
- Reliable return of background specialist results through the selected Hermes API flow.
- Hosted runtime operations and live-provider reliability after the tested per-scope container isolation foundation.
- Supported AnyList integration, or an accepted alternative.
- Google account linking for a hosted app and coverage of relevant calendars/birthdays.
- Quiet hours, digest timing, escalation, notification recipients and ownership defaults.
- Final hosted identity provider, membership permissions, token encryption and deletion policy.
- Render service sizing, exact monthly cost and durable state transfer/restore. The chosen layout is documented in [deployment](deployment.md); packaging and hosted verification remain to be completed.

Personal hosting was discussed with a planning budget of approximately A$50/month, not a
guaranteed price or a configured spending cap. Recheck provider prices and measured model
usage before deploying.

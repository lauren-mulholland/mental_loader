# Design decisions

Agreed baseline: 26 October 2026; updated 25 November 2026. Accepted choices guide implementation; provisional choices and unresolved integrations should be revalidated as the POC produces evidence. See [development handoff](handoff.md) for current execution status.

| Decision | Status | Rationale and consequence |
| --- | --- | --- |
| Use Hermes as the actual runtime | Accepted | Learn native scheduling, curated memory, session recall, skills and delegation in a real household scenario. Do not reimplement these services to anticipate a possible migration. |
| Keep household logic independent | Accepted | Plain Python owns required household rules and integration actions. Hermes tools remain thin wrappers. Runtime state stays native to Hermes; migration would reuse the functions and export/adapt memory, skills, sessions and schedules. |
| Combine deterministic code and agent work | Accepted | Hermes controls scheduled execution and agent behaviour. Python functions enforce validation, state changes, calculations and integration actions. Native no-agent cron runs deterministic actions without inference. Prompt instructions are not workflow guarantees. |
| Laptop and phone share a responsive PWA | Accepted | Saved chat and a small task view work across devices with little frontend overhead. Avoid native iPhone distribution and app-review overhead. WhatsApp is optional future work. |
| Use Hermes sessions and keep a visible transcript | Accepted | Hermes manages agent context and recall. The app stores the messages shown in the PWA so they remain visible after refresh or device changes; that transcript does not replace native session memory. |
| Household is the tenant boundary | Accepted | Support future sharing with friends. Authentication, records, integrations, jobs, memory and notification recipients must all respect household boundaries. |
| Limit the existing personal runtime to one household | Preserved compatibility boundary | The original instance is unchanged. Phase 2 adds separately attested per-scope containers; never point unrelated households or private conversations at the personal shared runtime. |
| Orchestrator plus specialists | Accepted direction | Split gifts, meals, appointments/supplies and home tasks by responsibility. Introduce specialists incrementally and invoke only the necessary ones. |
| Google Calendar first | Accepted | First calendar feasibility target selected by the user. Start read-only; account authorisation and birthday-calendar coverage still need live validation. |
| Investigate AnyList | Provisional | Preferred grocery-list integration, but a supported write/sync API has not been established. Keep an internal list/export fallback in scope. |
| PostgreSQL from the start | Accepted direction | Use the same database family locally and when hosted. Adds Docker setup now, reduces a later persistence migration. |
| Separate app and Hermes environments | Accepted | Each has an isolated venv and lockfile. Pin the upstream Hermes revision; keep its home and credentials inside ignored local state. |
| Local first, then PaaS | Accepted direction | Begin on the laptop; eventually operate independently of it. Railway is a suggestion, while provider-specific coupling should remain minimal. |
| Deterministic reminders work with AI off | Accepted | Protect basic usefulness and cost control when provider access fails or request limits are reached. |
| Use native memory and skills | Accepted | Keep stable household preferences and lessons in the dedicated Hermes home; use native skills for repeatable procedures. Task state and delivery receipts remain structured records. Export/adapt native state later if migration becomes necessary. |
| Use Hermes cron for reminder timing | Accepted | One native script-only job dispatches due tasks every minute without a model. The PWA push sender remains a custom transport because Hermes does not supply our web-app notification interface. |
| Proactive delivery is a core feature | Accepted | The assistant must reach users instead of requiring them to remember to check it. Real-device push is a feasibility gate, not an assumed success. |
| Keep costs visible and bounded | Accepted | Track usage by household/workflow, limit iterations and delegation, and use provider-side budget controls. Top-level request limits do not cap dollars or all subagent calls. |
| Use OpenRouter for the learning POC | Current user configuration | User supplied the key locally and configured Hermes. Logs identified `openai/gpt-6-luna`; this is an experimental selection, not a permanent model commitment. |
| Reduce idle frontend polling | Agreed next direction; pending implementation | Poll quickly while a reply is pending, around every 30 seconds when idle, refresh after actions/on tab return and pause hidden tabs. Preserve the separate native reminder schedule. SSE can wait. |
| Track narrow upstream compatibility fixes | Implemented workaround | Keep the pinned-runtime encrypted-reasoning recovery patch in version control, apply it idempotently and refuse conflicts. Preserve native runtime ownership and review whether the patch is still needed on upgrade. Live recovery remains unconfirmed. |

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
- Whether public signup/email recovery needs an external identity provider, and the long-term deletion/ownership-transfer policy. Individual credentials, membership permissions, offline recovery and token encryption are implemented for the bounded pilot.
- PaaS selection and resource sizing after measuring the local application.

Personal hosting was discussed with a planning budget of approximately A$50/month, not a
guaranteed price or a configured spending cap. Recheck provider prices and measured model
usage before deploying.

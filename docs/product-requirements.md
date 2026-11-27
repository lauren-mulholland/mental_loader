# Product requirements

Agreed baseline: 26 October 2026; product direction updated 25 November 2026. Implementation status lives in [feasibility.md](feasibility.md).

## Purpose and users

Mental Loader reduces household mental load by remembering responsibilities, identifying
preparation work, and prompting the right person with a useful next action. It should reduce
both forgotten tasks and the effort of deciding what to do next.

The initial user is the project owner, using a laptop and iPhone. Later, friends should be
able to use the same application with their own isolated households. A household can have
multiple members, and a person may belong to multiple households. Individual accounts,
membership and permissions are required before sharing beyond the personal pilot.

This is also a learning project: the first agent runtime is Hermes so its capabilities,
costs and limitations can be understood through real household use.

Use Hermes' native scheduling, memory, sessions, skills and delegation as part of the POC. Keep household tools in clean functions for possible future migration; do not avoid the native runtime features merely to make that hypothetical migration easier.

## Product direction: a household app with built-in agent support

The primary product is a household management application with an embedded agent, rather
than a chat-only assistant. Over time, provide category views or tabs for Events, Meals,
Shopping, Stock/Supplies and other household responsibilities. Keep the first version
minimal; this is a product direction, not a requirement to build all tabs before hosting.

Users must be able to view, add, edit and complete supported records directly in the UI
without sending a chat message. Examples include adding a shopping-list item, updating
reported stock, reviewing events and editing a meal plan. Ordinary record operations use
deterministic application code and do not require a model call. Chat and direct UI actions
operate on the same authoritative household records, so a change made through either
surface is visible through the other.

Use the agent for work involving judgement, synthesis or uncertain inputs: proposing meals,
suggesting gifts, interpreting natural-language requests and identifying useful next steps.
Preserve proactive help and existing Hermes runtime capabilities. This direction does not
require replacing Hermes or moving its scheduler or native memory into custom services.

## Adaptive onboarding (fast follow after personal deployment)

Start by asking what the user wants the app to manage. Offer relevant categories and allow
multiple selections, skipping, resuming and adding categories later. Ask only follow-up
questions appropriate to the selected areas. Do not require a complete household inventory
before delivering a useful result.

For meals, collect who is eating, dietary requirements/allergies, favourite meals or foods,
usual staples and realistic cooking time. Ask about budget, equipment and shopping routines
when needed. Distinguish usual staples from timestamped confirmation of current stock.
Offer an initial meal suggestion or small plan after the essential questions.

Keep selected categories and onboarding progress in explicit household-scoped application
records. Stable preferences continue to use Hermes native memory; transactional lists,
stock and plans belong in structured records. Provide a way to inspect and correct answers,
and ensure subsequent suggestions use corrections rather than an obsolete memory copy.
These screens and records are planned, not implemented in the current spike.

## Intended capabilities

| Area | Requirements |
| --- | --- |
| Occasions and gifts | Read relevant Google Calendar events; remember which people receive gifts, preferences and budgets; calculate preparation deadlines; suggest gifts; track purchased/prepared status. |
| Cultural occasions | Support events such as Mother's Day and Christmas according to household country, traditions and preferences. Do not infer that every event applies to every household. |
| Meals and groceries | Remember dietary preferences and shopping routines; suggest meals; combine ingredients; maintain a grocery list. Investigate AnyList as the preferred external list integration. |
| Pantry knowledge | Separate usual cupboard staples from recently confirmed stock. Store when and how stock was reported; do not assume a usual staple is available. |
| Appointments and supplies | Track household members' appointments, booking follow-ups, medication replacement and vaccination-related reminders using supplied or confirmed dates/instructions. |
| Home responsibilities | Extend later to chores, maintenance, repairs and seasonal tasks. Associate responsibilities with an owner rather than defaulting everything to the organiser. |
| Proactive help | Deliver timely reminders and suggested next actions; eventually support quiet hours, grouped digests, snooze, completion, cancellation and reassignment. |
| Memory and history | Use Hermes' native memory for stable preferences and routines, session search for recall, and skills for procedures. Preserve the PWA-visible transcript across reloads and devices. Keep transactional facts such as task status and stock observations in structured records; let users inspect, correct and delete stored information. |

An occasion and its preparation are different records. For example, a birthday may lead to
choosing, ordering and wrapping a gift, each with its own deadline and completion state.

## Interaction and constraints

- One responsive web application must work on laptops and phones. A PWA provides optional
  Home Screen installation and push; App Store distribution and native app reviews are outside scope.
- The current spike has chat, upcoming tasks and simple settings. The target app adds
  direct category views alongside chat; users choose the appropriate interaction. Keep
  frontend overhead low. WhatsApp integration is not required.
- A notification should explain why it appeared and offer a small next action. Completed
  or cancelled tasks should stop generating new reminders.
- A closed browser must not erase a submitted request or conversation. Use Hermes sessions for agent continuity and retain a visible transcript and durable request status in the app; make interrupted or failed work visible.
- Begin locally, then move to a PaaS when useful. Local scheduled work requires the laptop, application backend and Hermes scheduler to remain running. Phone push testing needs trusted HTTPS and device permission.
- Keep running costs low. Use deterministic code for required actions, inexpensive model
  routes for appropriate requests, bounded delegation and visible usage limits.
- Record relevant sources and confirmation dates for remembered facts. Ask when dates,
  preferences or identity are ambiguous.

## Household privacy and control

Each household's records, credentials, conversations, agent context and notifications must
remain isolated. Tenant identity comes from authenticated application context or a bound
tool credential, never from a household ID selected by the model.

The intended account design starts with owner/member roles. Conversations should be private
to their participants unless deliberately shared; household membership must not automatically
expose every conversation. These permissions are not implemented in the access-code spike.

Keep notification previews generic where they might expose personal or health details.
Health features organise supplied information; they do not determine clinical schedules,
diagnose conditions or generate medication instructions. Purchases, bookings and messages
to other people are outside the initial POC; future support needs explicit user acceptance.

## First useful outcome

The first complete household workflow should notice an upcoming birthday for someone on the
gift list, create preparation tasks, send an actionable reminder, suggest gifts when wanted,
and stop the relevant reminders when the gift has been bought.

Before that, phase 1 must demonstrate the smaller foundation: saved chat, a deterministic
reminder tool, an actual Hermes interaction, and real-device notification delivery. See
[the implementation plan](implementation-plan.md) for the readiness criteria.

# Application MCP interface for Hermes

Accepted 27 November 2026 (Pacific/Honolulu), during a side conversation while the main chat continues UI design. This is the agreed implementation direction after design review, not an implemented MCP service or authorization to resume application development, upgrade Hermes, deploy or push.

## Decision

The custom Mental Loader application will expose an MCP server that our Hermes agent consumes as an MCP client. The direction is **Hermes → our application's MCP tools → shared application operations → application database**. Direct browser actions invoke the same application operations through their existing authenticated APIs.

MCP is a thin tool interface over application capabilities. Household rules, validation, dates, calculations, permissions, transactions, idempotency and integration actions remain in ordinary application code. The MCP adapter must not contain a second implementation of those rules or give the agent direct database access. Hermes continues to own reasoning, provider execution, native sessions, curated memory, skills and delegation.

The current native plugin already wraps authenticated application endpoints. Bulk editing is possible through that mechanism too; the reason for choosing MCP is a standard, discoverable interface for the application's tools. Existing birthday/task/reminder operations are the starting point. Person defaults, gift-setting bulk operations and their persistence are still to be implemented after design approval.

## Initial tool surface

The names below illustrate the intended capability boundaries; finalize schemas with the application operations rather than treating these names as an existing API.

| Capability | Example tools | Application responsibility |
| --- | --- | --- |
| Inspect people and settings | `list_people`, `get_gift_settings` | Return only accessible records, including the effective defaults and explicit person/occasion overrides. |
| Capture an explicit gift idea | `save_gift_idea` | Resolve an accessible recipient, validate input and return a durable success receipt under the ownership contract agreed during design. |
| Prepare a bulk edit | `preview_bulk_gift_settings` | Resolve the exact targets, field changes, affected records and current versions without writing the requested settings. |
| Apply a bulk edit | `apply_bulk_gift_settings` | Revalidate authorization and record versions, apply the accepted changes atomically and return the affected records and counts. |

Prefer bounded operations such as changing selected people's budgets or card defaults. Do not expose unrestricted SQL, arbitrary table updates or generic internal API execution.

## Bulk changes and side-panel interaction

For a request such as “Set everyone's gift budget to $40,” Hermes interprets the intent and calls application tools. The application resolves which accessible people are included. The result must distinguish changes to **usual defaults**, **existing occasion plans**, or **both**; a change to one must not silently imply the others. Inherited values and explicit overrides must remain distinguishable.

The preview operation returns a concrete change set that the assistant side panel and direct bulk-edit UI can display. Application code owns the identity and contents of that change set. Applying it must not let the model substitute different targets or values after review. A preview ID alone is not authorization, and a model-supplied `confirmed: true` is not evidence of any required user approval. The main design review will determine when an explicit request is sufficient and when a separate Apply action is useful; MCP itself does not impose an extra confirmation for every edit.

Apply operations use a stable request identity so network retries do not duplicate changes. Reject stale previews with a clear conflict and fresh preview rather than silently overwriting intervening edits. Recheck permissions at apply time. Commit a bounded bulk edit and any required task reconciliation in one transaction; validation failure must not leave a partially changed set. Return saved identifiers, counts and a visible result receipt so the agent reports what actually happened.

Both direct controls and the agent operate on the same authoritative records, so a confirmed agent edit appears in the UI and a manual correction is available to subsequent tool reads. Structured editable planning settings, including budgets, card choices and person defaults, are application-owned. General learned tastes and routines remain native curated memory. Finalize the gift-idea storage and correction contract during design; do not introduce a competing authoritative preference store merely to support MCP.

## Authentication and runtime boundary

The application authenticates each MCP connection/request and derives the actor, household and allowed conversation scope from server-controlled credentials. Tool arguments do not grant a household, role or private-chat identity. A shared household runtime must not gain access to private conversations, and a private runtime must not gain wider access just because its owner can access other records. Browser clients never receive runtime credentials.

Keep MCP access restricted to the capabilities the runtime needs. Preserve account authorization, encrypted external credentials and validated integration adapters in the application. Provider reasoning is not an authorization boundary.

Prefer an HTTP MCP endpoint within the existing backend deployment so the interface does not require an additional hosted service solely for protocol translation. Verify the pinned Hermes runtime's MCP dependencies, supported transport, authentication behavior and server lifecycle during implementation; current upstream documentation alone is not evidence that the deployed runtime is configured or compatible. Do not upgrade Hermes automatically to introduce this interface.

## Implementation order and retained behavior

1. Finish the main chat's UI/onboarding design and define the application-owned person settings and bulk-edit semantics.
2. Implement and test shared application operations for reads, individual changes, previews and bulk apply. Demonstrate the direct UI path with AI unavailable.
3. Add a thin MCP adapter to those operations and configure the existing scoped Hermes runtime to consume it.
4. Verify scope enforcement, shared UI/tool behavior, atomicity, stale-preview conflicts, retry identities and visible receipts. Exercise actual MCP discovery and tool calls without a provider where possible; label mocked provider checks and obtain explicit authorization for paid model checks.
5. Move equivalent agent-facing actions from the native plugin to MCP after parity is established. Preserve working compatibility during the transition and avoid exposing duplicate equivalent tool sets to the model.

The single native no-agent reminder cron job, deterministic dispatch/outbox, application job leases and native conversation/session identities remain in place. This decision does not create another scheduler, memory service or agent framework. The current dispatch endpoint need not be replaced merely because interactive agent tools move to MCP.

For the existing application boundary and memory ownership context, see [architecture](architecture.md), [design decisions](design-decisions.md) and [development handoff](handoff.md). Hermes' upstream [MCP documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) describes its MCP client support; runtime-specific compatibility remains an implementation check.

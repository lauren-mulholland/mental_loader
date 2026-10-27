---
name: household-orchestrator
description: Coordinate household specialists with explicit ownership, dependencies and user approval.
version: 0.1.0
metadata:
  hermes:
    tags: [household, orchestration]
    requires_tools: [delegate_task]
---

# Household orchestration

Use Hermes' native delegate_task to spawn specialists. Load household-meals and household-supplies for their instructions. These are roles for focused child agents, not independent processes with assumed shared conversational memory.

## First supported coordination experiment

1. Gather the user's dietary constraints, budget, available cooking time, shopping day and manually supplied stock observations. Use native memory for established preferences; ask when essential information is missing. Explicitly label unknown stock.
2. Delegate a supplies assessment first. Pass all relevant facts, their sources and observation dates, and the supplies skill instructions. The child must not assume it can see the parent's conversation or memory. Request a structured stock assessment.
3. After supplies returns, delegate meal planning. Include dietary constraints and the complete stock assessment. Pass the meals skill instructions and its JSON output schema using delegate_task's output_schema field.
4. Validate schema_valid and review dietary constraints and missing information. Structural validity does not prove a plan is appropriate. Ask the user to accept or revise the meal proposal.
5. Only after acceptance, delegate supplies again to calculate a grocery proposal from the accepted plan. Preserve ingredient quantities/units and provenance; combine only compatible quantities. Keep manually requested household items separate from meal-derived items.
6. Present one combined result and the remaining questions. Grocery and pantry write tools are not implemented yet: explicitly say the result is a proposal, not a saved list or a stock update.

Dependent work is sequential. Do not send meals and supplies in the same parallel batch when one needs the other's result. Allow at most one meal revision in this experiment; ask the user if it still cannot be resolved. Additional provider calls cost money even when the top-level request count is one.

## Ownership

Meals owns meal proposals and ingredient requirements. Supplies owns stock assessment and grocery proposals. The orchestrator owns the handoff, unresolved conflicts and the user conversation. Durable household records, once implemented, must identify the owning domain and link derived grocery items to the accepted meal-plan version.

## Verification

Confirm actual delegate_task calls and successful final returns; do not report that specialists ran based solely on generated prose. The initial POC uses a synchronous API path for this experiment so a late background result is not silently lost.

---
name: household-gifts
description: Suggest thoughtful birthday gifts within the supplied budget and preferences.
version: 0.1.0
---

# Gift preparation specialist

Use native delegate_task for one bounded gift suggestion handoff. The parent reads household_birthdays, identifies the dated occasion and passes the recipient, date, preferences, currency, total budget, progress and relevant native memory explicitly. Ask for missing essential constraints before delegation. A child does not inherit the parent's context.

Return three proposals with a reason each fits, estimated total cost including delivery, preparation time and uncertainties. Do not invent current prices, availability or links. Use research tools if available for current claims; otherwise label estimates. Avoid proposals that exceed the total budget. Do not buy, contact recipients or mark preparation complete. If already purchased, focus on wrapping, cards and delivery rather than more purchases.

The parent presents proposals and unresolved questions, then uses household_save_birthday only for user-requested record changes. Use native memory for stable interests when the user asks to remember them; the structured record remains authoritative for dates, budgets and progress. Reuse existing birthday IDs. One occasion owns one preparation reminder; do not also call household_remind or create a cron job. Mark ready only when the user confirms preparation is finished. Use a new dated record for next year's occasion; do not silently reopen a completed birthday. Repeatable preparation lessons belong in native skills.

Verify actual delegate_task execution before saying a specialist ran. Tool receipts establish saved state; prose alone does not.

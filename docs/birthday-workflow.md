# Dated birthdays and gifts

Retained from reviewed commit `1143534` and adapted to Phase 2 on 26 November 2026.

The direct-edit panel saves a recipient, interests, total budget/currency, one dated occasion,
preparation lead time and progress. Records are shown in their selected conversation.
Browser and native tool routes use the same household/private-chat permissions. A birthday
cannot be moved into another conversation; task ancestry remains immutable.

Each occasion owns one preparation task. Its deadline is 09:00 in the household timezone,
the selected number of calendar days before the occasion. Repeated saves reuse that task.
Purchased remains open for wrapping/cards; ready or cancelled completes preparation and
cancels pending deliveries. Completing the task also marks the birthday ready. Existing
receipts are retained and notified tasks do not automatically notify again after edits.
Annual rollover is not implemented; create a new dated record for next year.

`household_birthdays` and `household_save_birthday` expose the same records to Hermes.
The `household-gifts` native skill supports bounded gift proposals from supplied preferences
and budget. Gift ideas fills a chat draft for the user to send. Saving/editing records makes
no model call. Suggestions do not purchase gifts, contact anyone or mark work complete.

The old birthday commit also contained a competing account system, migration module and
Calendar connection implementation. Those were not imported: the Phase 2 implementations
remain authoritative. Dated-event reconciliation helpers and their contracts are retained,
but selected Google event import is not exposed through the UI/API or CLI in this build.
Automatic Calendar synchronization remains unimplemented.

Tests cover dates/DST/leap day, repeated saves, progress/completion, pending-delivery
cancellation, household/private-chat isolation, source reconciliation and encrypted backup
round trips with date values. The isolated browser smoke checks creation, editing, completion
and reload at desktop/mobile widths without inference. Live gift delegation and real phone
push for this combined build have not been rerun.

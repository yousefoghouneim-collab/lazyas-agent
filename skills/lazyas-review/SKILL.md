---
name: lazyas-review
description: Use when the person mentions their Lazy as review, review notes, "what I pointed at", "my feedback", or asks to fix the things they recorded or noted on screen. Fetches the review through the lazyas MCP tools, fixes notes by reference, and reports each fix with proof.
---

# Working through a Lazy as review

Lazy as notes come from a person pointing at their screen and talking. Each note has a reference
(`2026-10-16_031500#3`), what they said, one or more screenshots (a box marks where they pointed), sometimes the
element under the pointer and the text near it, and a status.

1. **Get the review.** Call `get_latest_review` (it picks this project's newest review when the project has a
   `.lazyas.json`). Use `list_open` for everything still to do across reviews.
2. **Look before you change.** For each note, open its screenshot with `get_note` or `get_image`. The words
   "this" and "here" mean the boxed spot. Match the element name or nearby text to the code (search for it).
3. **Pick up.** `set_status(ref, "sent")` when you start a note, so the person sees it's being handled.
4. **Fix, then check.** Run the app or tests and look at the result the way the person would.
5. **Report with proof.** `set_status(ref, "claimed", summary, evidence)` per note. Evidence is something a person
   can check: a screenshot file of the result, test output, or the exact change. Never claim what you didn't check.
6. **Can't or shouldn't?** `set_status(ref, "wontfix", summary="why")`, or ask the person.
7. **Finish honestly.** Say which references you claimed and what's left. Only the person can mark a note
   verified; they'll check before/after in Lazy as and reopen anything that isn't right.

Reopened notes come back with the reason; treat that reason as the new instruction for that note.

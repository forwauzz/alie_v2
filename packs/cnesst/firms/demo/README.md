# Firm layer — demo

Resolution order is **base → pack → firm → case → unit** (PRD §6.3).

This layer exists because style is per-firm and arguably per-paralegal. Without it,
onboarding firm #2 means forking a pack, and the fork drifts from the regime rules it was
supposed to inherit.

A firm may restate **wording and display** — `output.yaml` and the `unit_toggles` /
`vocabulary` blocks of `pack.yaml`. It may **not** invent a class, a date role or a filter:
those are regime facts, not house style, and letting a firm edit them would put regime
knowledge in two places.

Mappings merge key by key; lists and scalars replace. A firm overriding one `field_lines`
entry restates that line and inherits every other.

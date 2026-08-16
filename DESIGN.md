### Confirmed instance of the renumbering limitation (Phase 1 ingest)

17 CFR 275, comparing snapshots 2024-11-18 -> 2024-11-19: seven sections
(275.206(4)-9, 275.206(4)-10, and all five of the 275.211(h) cluster)
disappear from `current_chunk` between these two dates, one day apart.

Plausible cause: SEC finalized marketing-rule / Form PF related amendments
around this period that likely reorganized adjacent sections under new
citations, rather than repealing the underlying requirements outright. Not
independently verified -- doing so would mean cross-referencing the Federal
Register rule text to confirm where (or whether) the content moved, which is
Phase-6-scale investigative work, not Phase 1 ingest scope.

**Decision:** accepted as-is. Used in the golden set as a genuine
`section_path`-level disappearance for testing `diachronic-002`-style
deletion-detection questions, but the eval rubric for any item built on this
pair must state the ambiguity explicitly: "this section_path was removed from
the family's current numbering as of this date; this may reflect content
being renumbered elsewhere rather than a substantive repeal." A system that
reports the disappearance faithfully passes; a system that claims the
underlying *requirement* was necessarily eliminated does not, since we
haven't verified that claim ourselves.
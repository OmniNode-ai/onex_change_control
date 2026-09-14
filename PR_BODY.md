## Summary

- `ModelAcBinding._AC_LABEL_RE` and `ac_criteria._AC_LABEL_RE` required a word
  boundary directly after the ordinal digits, so a suffix letter sitting
  immediately after them (both word characters, no boundary) failed the whole
  match. A criterion labelled `AC2b` read as no label at all upstream --
  unbindable, never absent -- which is exactly the shape OMN-18332 hit: 6 of
  its 12 declared criteria carry suffixed labels and could never be bound by
  any evidence item.
- Fix: both regexes gain the identical optional single-letter suffix group
  the producer already ships (`omniclaude#2159`, `omnimarket#2540`) --
  captured verbatim, case preserved, so `AC2b` and `AC2B` are distinct labels
  rather than one folded into the other. A plain `AC2` is unaffected.
- Three pre-existing OMN-18333 tests pinned the old unbindable behaviour for
  exactly this shape. They are corrected, not relaxed, with the reversal
  reasoned in their own docstrings -- one of them already anticipated it in
  its own text ("the remedy is OMN-18356 in the reader, not a downgrade
  here"). The genuinely unparseable case, a two-letter compound (`AC2bb`),
  still refuses on the same terms as before.
- Nothing else changed: hashing, timestamps and every other field are
  untouched.

## Test plan

- RED first: 11 of 19 new tests in `TestSuffixedLabelGrammar` failed against
  the unfixed regex pair (`canonical_ac_label`, the model's field validator,
  and an end-to-end `check_contract_ac_bindings` join); 19/19 green after the
  fix.
- 143/143 across the touched test files
  (`test_omn_18236_ac_binding_acceptance.py`,
  `test_omn_18333_binds_every_declared_criterion.py`,
  `test_omn_18333_absent_binding_holds.py`, `test_omn_18056_binds_ac_occ_gate.py`).
- `mypy --strict` clean on the three touched source files; `ruff format
  --check` and `ruff check` clean.
- `pre-commit run` clean on every touched file.

Ticket: OMN-18356

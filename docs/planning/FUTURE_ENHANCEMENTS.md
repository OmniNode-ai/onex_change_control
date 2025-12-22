# Future Enhancements for ONEX Change Control

This document tracks future enhancements identified during PR reviews and implementation planning. These should be created as Linear tickets when ready to implement.

## From PR #5 Review (OMN-963: JSON Schema Export)

### High Priority (Post-Merge)

1. **Error Scenario Tests**
   - Add tests for filesystem errors, permissions issues
   - Test schema content validation (exported schemas validate real data)
   - Test version consistency (EXPORT_SCRIPT_VERSION matches manifest)
   - **Priority**: Medium
   - **Effort**: Small

2. **Schema Content Validation**
   - Validate that exported JSON schemas can actually validate real YAML data
   - Ensure exported schemas are functional, not just syntactically correct
   - **Priority**: Medium
   - **Effort**: Small

### Medium Priority

3. **Schema Registry Helper**
   - Create programmatic schema access helpers for downstream consumers
   - Provide Python API for loading schemas by version
   - **Priority**: Medium
   - **Effort**: Medium

4. **Schema Versioning/Migration Strategy**
   - Plan for schema versioning when releasing v2.0.0
   - Consider adding migration scripts for breaking changes
   - Document version upgrade path
   - **Priority**: Low (until v2.0.0 is needed)
   - **Effort**: Medium

5. **JSON Schema Validation Fields**
   - Add `$schema` and `$id` fields to exported JSON schemas
   - Improve schema discoverability and tooling compatibility
   - **Priority**: Low
   - **Effort**: Small

6. **Export Script Improvements**
   - Add `--quiet` flag for CI environments
   - Consider shared constants module for schema version (currently hardcoded in two places)
   - **Priority**: Low
   - **Effort**: Small

### Low Priority / Nice-to-Have

7. **CI Optimization**
   - Consider fail-fast strategy to stop all jobs if schema check fails
   - Already implemented: schema-determinism depends on test job
   - **Priority**: Low
   - **Effort**: Small

8. **Error Handling Refinement**
   - Review exception handling scope in export script
   - Consider being more specific about which exceptions to handle vs propagate
   - **Priority**: Low
   - **Effort**: Small

## Implementation Plan Milestones

These are already tracked in the implementation plan but listed here for completeness:

- **M2 (Local validator)**: validate YAML against pinned schemas (no network) - OMN-965
- **M3 (Repo CI hardening)**: purity + naming + determinism checks enforced
- **M4 (First downstream pilot)**: 1 repo adopts "contract exists + schema validates"
- **M5 (Enforcement expansion)**: diff compliance + evidence enforcement

## Notes

- All enhancements are optional and non-blocking
- Priority levels are relative to current milestone (M1)
- Effort estimates are rough and may vary based on implementation details

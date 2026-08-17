# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| `0.5.x` | Yes (current) |
| `< 0.5.0` | No |

Once `1.0.0` is released, the policy will follow standard SemVer support windows: the latest minor/patch of the current major is supported; older majors receive security fixes for 12 months after the next major release.

---

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues privately to: **contact@omninode.ai**

Include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce or a proof-of-concept (if available).
- The affected version(s).

You will receive an acknowledgment within 48 hours and a resolution timeline within 7 days.

---

## Security Model

`onex_change_control` is a **schema and enforcement library**. Its threat model:

- **Schema modules are pure** (D-008): no filesystem reads, no network calls, no env reads. This prevents supply-chain attacks that exploit env-variable injection into pure data models.
- **CLI tools run with caller permissions**: `validate-yaml`, `check-drift`, etc. run locally in CI with the permissions of the invoking process. They do not escalate privileges or write outside the current working directory.
- **No network calls at runtime**: all validators are local and offline. There is no telemetry, no outbound API calls, and no remote schema fetches during validation.
- **Exported JSON schemas are immutable**: once a schema version is published, the artifact is never mutated in-place. Downstream consumers can safely pin schema hashes.

---

## Known Limitations

- The `check-drift` and `check-anthropic-key-guard` tools read local files and environment variables. Run them in trusted CI environments only.
- YAML parsing (`pyyaml`) uses safe-load by default. Do not switch to `yaml.load()` without an explicit Loader argument.

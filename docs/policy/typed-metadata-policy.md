# Typed-Metadata Policy

> **Status:** Active
> **Ticket:** OMN-5133 (epic OMN-5058)
> **Root cause:** OMN-5056

---

## 1. Rule

Pydantic model fields **MUST NOT** use `dict[str, Any]` or `dict[str, object]` as a metadata catch-all. Every metadata field must use one of three strategies:

| Strategy | When to use |
|----------|-------------|
| **A -- Promote to typed field** | Key is semantically intrinsic to the owning model |
| **B -- Replace with TypedDict** | Field is genuinely metadata but keyspace is finite and internal |
| **C -- ONEX_EXCLUDE** | Intentionally open extension surface for third-party or protocol-driven values (last resort) |

If you cannot justify Strategy C, use Strategy A or B.

---

## 2. Decision Rubric

### Strategy A: Promote to typed field

Use when a key is **semantically intrinsic** to the owning model -- it controls validation, execution ordering, serialization behavior, or any logic that the model itself participates in.

**Pattern:**

```python
# Before:
contract.metadata.get("priority", 0)

# After:
priority: int = Field(default=0, description="Execution priority; lower runs first")
contract.priority
```

**Decision signal:** If any line of production code reads or writes the key with a string literal (`metadata["priority"]`, `metadata.get("priority")`), the key belongs on the model as a typed field.

### Strategy B: Replace with TypedDict

Use when the field is genuinely metadata but the **keyspace is finite and entirely controlled by our code** -- every key is set internally, never by external consumers.

**Pattern:**

```python
class ClaudeHookResultMetadataDict(TypedDict, total=False):
    handler: str
    reason: str
    kafka_emission: str

class ModelClaudeHookResult(StampedModel):
    metadata: ClaudeHookResultMetadataDict = Field(default_factory=dict)
```

**Decision signal:** You can enumerate every key the dict will ever contain. All writers are in our codebase.

### Strategy C: ONEX_EXCLUDE (last resort)

Use **only** when the metadata is an intentionally open extension surface for third-party or protocol-driven values. You must state:

1. **Why** the keyspace is open-ended.
2. **Who** controls the extension surface (name the concrete external consumer or protocol).

**Pattern:**

```python
# ONEX_EXCLUDE: dict_str_any - MCP extensibility contract; third-party tools add arbitrary keys
metadata: dict[str, object] = Field(default_factory=dict)
```

If you cannot name a concrete external consumer, use Strategy B instead.

---

## 3. Exemption Process

1. Add a comment on the **same line** as the field definition:

   ```python
   # ONEX_EXCLUDE: dict_str_any - <reason stating WHY open-ended and WHO controls the surface>
   metadata: dict[str, object] = Field(default_factory=dict)
   ```

2. The pre-commit hook `scripts/check_no_untyped_metadata.py` scans for `dict[str, Any]` and `dict[str, object]` in Pydantic model files. Lines bearing `ONEX_EXCLUDE: dict_str_any` are skipped.

3. If your exemption reason cannot name:
   - A concrete external consumer, **or**
   - A protocol specification that mandates open-ended keys

   then the exemption is invalid. Use Strategy B.

**Future centralization path:** The guard script currently lives in individual repos (e.g., `omniclaude/scripts/check_no_untyped_metadata.py`). It will be moved to `onex_change_control/scripts/validation/check_no_untyped_metadata.py` once downstream repos declare `onex_change_control` as a dev dependency, giving every repo a single authoritative copy.

---

## 4. Worked Examples

### Strategy A: `allow_empty` promotion on `ModelValueContainer`

**Before (untyped):**

```python
class ModelValueContainer(StampedModel):
    metadata: dict[str, Any] = Field(default_factory=dict)

# Caller:
if container.metadata.get("allow_empty", False):
    return True
```

**After (typed field):**

```python
class ModelValueContainer(StampedModel):
    allow_empty: bool = Field(
        default=False,
        description="When True, the container accepts empty values without raising ValidationError",
    )

# Caller:
if container.allow_empty:
    return True
```

The string-literal access is gone. Mypy catches typos. Autocomplete works.

### Strategy B: `ClaudeHookResultMetadataDict` on `ModelClaudeHookResult`

**TypedDict definition:**

```python
from typing import TypedDict

class ClaudeHookResultMetadataDict(TypedDict, total=False):
    handler: str
    reason: str
    kafka_emission: str
```

**Field replacement:**

```python
class ModelClaudeHookResult(StampedModel):
    # Was: metadata: dict[str, Any]
    metadata: ClaudeHookResultMetadataDict = Field(default_factory=dict)
```

All keys are enumerated. Any new key requires updating the TypedDict, which mypy will enforce across every consumer.

### Strategy C: `ModelHandlerContract.metadata` ONEX_EXCLUDE

```python
class ModelHandlerContract(StampedModel):
    # ONEX_EXCLUDE: dict_str_any - Handler contracts are extended by third-party node authors who register arbitrary config; the ONEX plugin protocol does not constrain the keyspace
    metadata: dict[str, object] = Field(default_factory=dict)
```

The comment names:
- **Why:** third-party node authors register arbitrary config.
- **Who:** external ONEX plugin protocol consumers.

### BLE001 bad/good pair

A bare `except Exception` can silently swallow `AttributeError` from untyped metadata access.

**Bad (BLE001 violation):**

```python
try:
    version = result.classifier_version  # AttributeError -- field is in metadata dict
except Exception:
    pass  # Swallowed for months with zero log output
```

**Good (narrowed exception):**

```python
try:
    version = result.classifier_version
except AttributeError:
    logger.warning("classifier_version not found on result model; check metadata migration")
    version = "unknown"
```

Narrowing the exception forces the developer to handle the specific failure mode and makes the untyped access immediately visible in logs.

---

## 5. Background

### OMN-5056 root cause

`result.classifier_version` failed at runtime because the field actually lived in `result.metadata["classifier_version"]` -- an untyped dict access by string literal. The Pydantic model had no `classifier_version` attribute, so the access raised `AttributeError`.

A bare `except Exception` (BLE001 violation) silently swallowed the error for months. There was zero log output. The bug was only discovered when a downstream consumer depended on the value being non-None.

### BLE001 connection

Removing the BLE001 (`except Exception: pass`) suppression forces an audit of every bare exception catch in the codebase. Many of these catches exist specifically to paper over untyped metadata access. Fixing them surfaces the underlying issue: metadata fields that should be typed.

### Guard script

The pre-commit hook `scripts/check_no_untyped_metadata.py` enforces this policy by scanning Pydantic model files for `dict[str, Any]` and `dict[str, object]`. Lines with `ONEX_EXCLUDE: dict_str_any` are exempt.

Cross-reference: `onex_change_control/scripts/validation/` (future home once downstream repos declare the dev dependency).

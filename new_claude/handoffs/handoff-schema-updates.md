# Handoff: Update `schemas/handoff.py` to Match V2 Skill Requirements

**Type:** Task completion — schema gaps identified during handoff-protocol skill creation
**Created:** 2026-03-06
**Previous work:** handoff-protocol skill created at `new_claude/skills/handoff-protocol/`

---

## Resume Point

The handoff-protocol skill is complete and approved. During creation, we discovered that the existing `schemas/handoff.py` Pydantic model is 90% complete but has 3 field-level gaps that prevent the skill's judgment guidance from being structurally enforceable.

**Next action:** Update `schemas/handoff.py` with the 3 schema changes below, then verify Pydantic validation still passes.

## What's Done

- `new_claude/skills/handoff-protocol/SKILL.md` — approved (200 lines, sender/receiver routing, anti-rationalization)
- `new_claude/skills/handoff-protocol/references/patterns.md` — templates for all 5 handoff types
- `new_claude/skills/handoff-protocol/references/anti-patterns.md` — 12 WRONG/RIGHT patterns
- Skill's validation TODO updated to reference existing infrastructure (`schemas/handoff.py` + `hooks/utils/schema_validator.py`)
- Confirmed: PostToolUse hook already validates `.claude/handoffs/*.json` against the `Handoff` Pydantic model

## Schema Changes Needed

All changes are in `schemas/handoff.py` (Pydantic model, 168 lines).

### 1. Add `source` and `locked` to `HandoffDecision`

**Current** (line 28-35):
```python
class HandoffDecision(BaseModel):
    decision: str
    reason: str
    state_when_made: str | None = None
```

**Needed:**
```python
class HandoffDecision(BaseModel):
    decision: str
    reason: str
    source: Literal["user_preference", "plan", "codebase_evidence", "judgment"] | None = None
    locked: bool = False  # True if source is user_preference or plan
    state_when_made: str | None = None
```

**WHY:** The skill teaches that decisions with `source: "user_preference"` are LOCKED — successors must not override without user consent. Without `source` and `locked` in the schema, this distinction exists only in prose and will be rationalized away.

### 2. Add state description to `files_modified`

**Current** (line 128):
```python
files_modified: list[str] = Field(default_factory=list)
```

**Needed:** Either change to a structured model or use a list of objects:
```python
class HandoffFileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    state: str = Field(
        description="Current state of the file — what was done, what remains",
        examples=["BatchProcessor with select_batch() implemented and tested. apply_filters() is a stub."]
    )

files_modified: list[HandoffFileEntry] = Field(default_factory=list)
```

**WHY:** Anti-pattern #7 in the skill: file paths without state descriptions force the successor to read every file end-to-end. A one-line state description saves 5-10 minutes of successor context per file.

### 3. Add context to `user_preferences`

**Current** (line 138-141):
```python
user_preferences: list[str] = Field(default_factory=list)
```

**Needed:**
```python
class UserPreferenceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preference: str
    context: str | None = Field(
        default=None,
        description="When/where the user expressed this preference",
        examples=["User corrected nested if/else in select_batch during chunk-01"]
    )

user_preferences: list[UserPreferenceEntry] = Field(default_factory=list)
```

**WHY:** Bare strings lose the context of when/why the preference was expressed. "Early returns over nested conditionals" is less useful than knowing the user explicitly corrected code style during a specific interaction.

## Decisions (LOCKED)

| Decision | Reason | Source |
|----------|--------|--------|
| No separate `validate-handoff.py` script needed | Infrastructure already validates via Pydantic + PostToolUse hook | Investigation — LOCKED |
| Schema updates go in existing `schemas/handoff.py`, not a new file | Model already registered in schema_validator.py registry | Codebase evidence — LOCKED |
| No LLM-based semantic validation hook | The writing agent IS an LLM; skill anti-rationalization handles judgment quality. Adding Haiku review adds cost for marginal benefit | User + investigation — LOCKED |

## What Didn't Work / Considered and Rejected

- **Standalone `scripts/validate-handoff.py`** — Unnecessary. The Pydantic model + PostToolUse hook already validates on write. Adding a manual script would be redundant and the agent would need to be told to run it (wasted tokens).
- **LLM-based semantic validation (Haiku prompt hook)** — Discussed with user. Rejected as overcomplicating. The skill's anti-rationalization defense handles the judgment quality. Structural validation (Pydantic) catches the mechanical failures.
- **`handoff_type` enum discriminator** — Considered adding to distinguish context_pressure/scrap/chunk_boundary. Not needed — `HandoffReason` already covers this with values `context_limit`, `task_complete`, `scrap_and_retry`, `user_requested`, `error`.

## Key Files

| File | Relevance |
|------|-----------|
| `schemas/handoff.py` | **THE file to modify** — Handoff Pydantic model |
| `schemas/_constants.py` | HandoffReason enum (no changes needed) |
| `schemas/session_log.py` | SessionLog model — has its own handoff support fields, may need corresponding updates to Decision model |
| `hooks/utils/schema_validator.py` | Validates `.claude/handoffs/*.json` — no changes needed unless new models added |
| `hooks/post_tool_use.py` | Calls schema_validator on writes — no changes needed |
| `new_claude/skills/handoff-protocol/SKILL.md` | The skill that motivates these schema changes |
| `.claude/schemas/session-log.schema.json` | V1 JSON Schema — superseded by `schemas/session_log.py` Pydantic model |

## Verification

After making the 3 schema changes:
- [ ] `python -c "from schemas.handoff import Handoff; print('OK')"` — model imports
- [ ] Existing handoff JSON files (if any in `.claude/handoffs/`) still validate
- [ ] `schemas/session_log.py` `Decision` model updated to match `HandoffDecision` changes (both should have `source` and `locked`)
- [ ] Tests pass (if any exist for schema models): `pytest tests/ -k handoff`

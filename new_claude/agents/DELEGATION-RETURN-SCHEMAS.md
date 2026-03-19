# Delegation Return Schemas — Design Reference

**Purpose:** Specifies all Pydantic models needed in `schemas/delegation_return.py` to support the 9 sub-agent return types. 7 of 9 sub-agents use custom return types that extend the existing 5 base types. 2 sub-agents (codebase-scout, research-scout) use the existing types unchanged.

**Source:** `SUB-AGENT-SPECS.md` Appendix C (Delegation Type Mapping), sections §1.3–§9.3 (Output Contracts per sub-agent).

**Current state of `schemas/delegation_return.py`:**
- 5 return models: `TargetedReturn`, `GuidedReturn`, `ExplorationReturn`, `ResearchReturn`, `TddChunkReturn`
- 1 shared sub-model: `UnknownResolved`
- 1 discriminated union: `DelegationReturn` dispatched by `delegation_type` field
- All models use `extra="allow"` (sub-agents may add fields)
- All models use `DelegationStatus = Literal["completed", "partial", "failed"]`

---

## 1. Design Decision: Extension Strategy

### Option A: Subclass existing models (rejected)

```python
class AuditReturn(GuidedReturn):
    delegation_type: Literal["audit"]  # override parent
    verdict: ...
```

**Problems:**
- Pydantic discriminated unions require each variant to have a unique `delegation_type` Literal. Subclassing with overridden Literal works but creates confusing inheritance where the parent's `delegation_type: Literal["guided"]` is silently replaced.
- Validators on the parent may not apply to the child's domain-specific fields.
- `extra="allow"` on the parent already means custom return types work today — the hook just can't validate domain-specific fields.

### Option B: Flat models with shared sub-models (chosen)

Each custom return type is a standalone `BaseModel` with its own `delegation_type` Literal. Shared field patterns are extracted into reusable sub-models (not base classes). This matches the existing design pattern in the file.

**Why:** Flat models are explicit, each has exactly the fields it needs, discriminated union dispatch is clean, and there's no inheritance confusion. The `extra="allow"` convention is preserved for forward compatibility.

### Option C: Protocol-based structural typing (deferred)

Use `Protocol` classes to define structural contracts that multiple return types satisfy. Interesting but premature — the current discriminated union approach is working and understood.

---

## 2. Shared Sub-Models (New)

These sub-models are used by multiple return types. They go in the `# --- Shared sub-models ---` section.

### QualityGateResult — SIMPLIFIED

~~Used by: `ImplementationReturn`, `DebuggingReturn`, `OptimizationReturn`, `TestWritingReturn`~~

**Design change:** The SubagentStop hook blocks termination if the quality gate fails — sub-agents cannot return until the gate passes. Therefore, the detailed breakdown (format/lint/typecheck/tests) is redundant in the return schema. Simplified to a boolean flag. Detailed gate results are written to `~/.claude/logs/quality-gates.jsonl` by the hook for observability.

Sub-agents that run the quality gate include `quality_gate_passed: bool` directly in their return model (not a shared sub-model). The SubagentStop hook validates this is `true` before allowing termination. The `QualityGateResult` sub-model is **removed** — each return type just has:

```python
quality_gate_passed: bool = Field(
    description="True if ./scripts/gate.sh passed. Sub-agent cannot return until this is true."
)
```

Used by: `ImplementationReturn`, `DebuggingReturn`, `OptimizationReturn`, `TestWritingReturn` (as inline field, not sub-model).

### AuditFinding

Used by: `AuditReturn`

```python
AuditSeverity = Literal["minor", "moderate", "major", "critical"]
AuditCategory = Literal[
    "plan_adherence", "test_coverage", "code_quality",
    "style", "design_coherence", "integration"
]

class AuditFinding(BaseModel):
    """A single finding from an audit-checker."""

    model_config = ConfigDict(extra="allow")

    severity: AuditSeverity
    category: AuditCategory
    file: str
    line: int | None = None
    description: str
    expected_behavior: str = ""
    actual_behavior: str = ""
    recommendation: str
```

### TestRecord

Used by: `TestWritingReturn`, `ScenarioWritingReturn`

```python
class TestRecord(BaseModel):
    """A single test written by a test-writer or scenario-writer."""

    model_config = ConfigDict(extra="allow")

    file: str
    test_name: str
    description: str
```

### RedVerification

Used by: `TestWritingReturn`

```python
class RedVerification(BaseModel):
    """Verification that all tests fail (red phase)."""

    model_config = ConfigDict(extra="allow")

    all_tests_fail: bool
    failure_types: list[str] = Field(default_factory=list)
    problematic_failures: list[str] = Field(default_factory=list)
```

### RootCause

Used by: `DebuggingReturn`

```python
BugCategory = Literal[
    "logic", "timing", "concurrency", "memory",
    "architecture", "data_flow", "configuration"
]

class AffectedFile(BaseModel):
    """A file affected by a bug."""

    model_config = ConfigDict(extra="allow")

    path: str
    lines: str = ""
    role_in_bug: str = ""

class HypothesisEntry(BaseModel):
    """A hypothesis tested during debugging."""

    model_config = ConfigDict(extra="allow")

    hypothesis: str
    evidence: str
    result: Literal["confirmed", "rejected", "inconclusive"]

class RootCause(BaseModel):
    """Root cause analysis from a debugger."""

    model_config = ConfigDict(extra="allow")

    description: str
    category: BugCategory
    affected_files: list[AffectedFile] = Field(default_factory=list)
    reproduction_steps: list[str] = Field(default_factory=list)
    hypothesis_log: list[HypothesisEntry] = Field(default_factory=list)
```

### FixApplied

Used by: `DebuggingReturn`

```python
class FixApplied(BaseModel):
    """A fix applied by a debugger."""

    model_config = ConfigDict(extra="allow")

    files_modified: list[str]
    description: str
    confidence: Literal["high", "medium", "low"]
```

### OptimizationEntry

Used by: `OptimizationReturn`

```python
OptimizationCategory = Literal[
    "algorithmic", "memory", "cache", "io", "concurrency", "architecture"
]

class OptimizationEntry(BaseModel):
    """A single optimization applied by an optimizer."""

    model_config = ConfigDict(extra="allow")

    file: str
    description: str
    category: OptimizationCategory
    impact_estimate: Literal["high", "medium", "low"]
    before_after: dict[str, str] = Field(default_factory=dict)
```

### PlanDimension

Used by: `PlanVerificationReturn`

```python
class PlanDimension(BaseModel):
    """Verification result for one plan dimension."""

    model_config = ConfigDict(extra="allow")

    passed: bool = Field(alias="pass")
    notes: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
```

**Design note:** The field is named `passed` in Python (because `pass` is a reserved keyword) with `alias="pass"` so the JSON representation uses `"pass": true` as defined in the sub-agent specs. Pydantic v2 handles this via `model_config = ConfigDict(populate_by_name=True)` on the parent or `alias` on the field.

### RevisionSuggestion

Used by: `PlanVerificationReturn`

```python
class RevisionSuggestion(BaseModel):
    """A revision suggestion from a plan-checker."""

    model_config = ConfigDict(extra="allow")

    dimension: str
    issue: str
    suggestion: str
```

### ScenarioRecord

Used by: `ScenarioWritingReturn`

```python
class ScenarioRecord(BaseModel):
    """A scenario written by a scenario-writer."""

    model_config = ConfigDict(extra="allow")

    file: str
    scenario_name: str
    tier: Literal["critical", "important", "edge_case"]
    description: str
    design_requirement: str = ""
```

### CollectOnlyResult

Used by: `ScenarioWritingReturn`

```python
class CollectOnlyResult(BaseModel):
    """Result of pytest --collect-only for scenario discovery."""

    model_config = ConfigDict(extra="allow")

    exit_code: int
    scenarios_discovered: int = 0
    collection_errors: list[str] = Field(default_factory=list)
```

### TestResults

Used by: `ImplementationReturn`

```python
class TestResults(BaseModel):
    """Summary of pytest execution results."""

    model_config = ConfigDict(extra="allow")

    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
```

---

## 3. New Return Models (7 models)

Each model is a standalone `BaseModel` with `extra="allow"`. Listed in the order they should appear in the file.

### 3.1 PlanVerificationReturn

**Sub-agent:** plan-checker
**delegation_type literal:** `"plan_verification"`
**Extends conceptually:** `GuidedReturn` (exercises judgment, resolves unknowns)

```python
PlanVerdict = Literal["PASS", "REVISE"]

PLAN_DIMENSIONS = frozenset({
    "goal_coverage", "task_completeness", "dependency_accuracy",
    "scope_boundaries", "risk_identification", "wave_feasibility",
    "acceptance_criteria_clarity", "technical_feasibility",
})

class PlanVerificationReturn(BaseModel):
    """Return schema for plan-checker sub-agents."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    delegation_type: Literal["plan_verification"]
    status: DelegationStatus
    verdict: PlanVerdict
    dimensions: dict[str, PlanDimension]
    revision_suggestions: list[RevisionSuggestion] = Field(default_factory=list)
    unknowns_resolved: list[UnknownResolved] = Field(default_factory=list)
    decisions_made: list[dict[str, Any]] = Field(default_factory=list)
```

**Validation rules (SubagentStop hook):**
1. `delegation_type == "plan_verification"` and `status` valid
2. `verdict` is `"PASS"` or `"REVISE"`
3. `dimensions` has all 8 keys from `PLAN_DIMENSIONS`
4. Each dimension value has `pass` (bool) and `notes` (non-empty string)
5. If `verdict == "PASS"`: all `dimensions[*].pass == True`
6. If any `dimensions[*].pass == False`: `verdict` must be `"REVISE"`
7. If `verdict == "REVISE"`: `revision_suggestions` must be non-empty

### 3.2 AuditReturn

**Sub-agent:** audit-checker
**delegation_type literal:** `"audit"`
**Extends conceptually:** `GuidedReturn` (exercises judgment, resolves unknowns)

```python
AuditVerdict = Literal["APPROVED", "CRITIQUE", "ESCALATED"]
DesignAlignmentScore = Literal["high", "medium", "low"]

class AuditReturn(BaseModel):
    """Return schema for audit-checker sub-agents."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["audit"]
    status: DelegationStatus
    verdict: AuditVerdict
    findings: list[AuditFinding]
    design_alignment_score: DesignAlignmentScore = "medium"
    test_coverage_assessment: str = ""
    style_conformance_notes: list[str] = Field(default_factory=list)
    unknowns_resolved: list[UnknownResolved] = Field(default_factory=list)
    decisions_made: list[dict[str, Any]] = Field(default_factory=list)
```

**Validation rules (SubagentStop hook):**
1. `delegation_type == "audit"` and `status` valid
2. `verdict` is `"APPROVED"`, `"CRITIQUE"`, or `"ESCALATED"`
3. `findings` is a list (can be empty for APPROVED)
4. Each finding has `severity`, `category`, `file`, `description`, `recommendation`
5. If `verdict == "CRITIQUE"`: at least one finding has `severity == "major"`
6. If `verdict == "ESCALATED"`: at least one finding has `severity == "critical"`
7. If `verdict == "APPROVED"`: no finding has `severity` in `{"major", "critical"}`

### 3.3 TestWritingReturn

**Sub-agent:** test-writer
**delegation_type literal:** `"test_writing"`
**Extends conceptually:** `TddChunkReturn` (TDD red phase)

```python
class TestWritingReturn(BaseModel):
    """Return schema for test-writer sub-agents."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["test_writing"]
    status: DelegationStatus
    tests_written: list[TestRecord]
    red_verification: RedVerification
    quality_gate_result: QualityGateResult | None = None
    decisions_made: list[dict[str, Any]] = Field(default_factory=list)
    carry_forward: list[str] = Field(default_factory=list)
```

**Validation rules (SubagentStop hook):**
1. `delegation_type == "test_writing"` and `status` valid
2. `tests_written` non-empty (unless `status == "failed"`)
3. `red_verification.all_tests_fail == True`
4. `red_verification.problematic_failures` is empty (warn, don't block)
5. All files in `tests_written[*].file` exist on disk (checked by hook via `os.path.exists`)
6. No Write/Edit tool calls to source file globs (checked via tool call log)

### 3.4 ImplementationReturn

**Sub-agent:** implementer
**delegation_type literal:** `"implementation"`
**Extends conceptually:** `TddChunkReturn` (TDD green phase)

```python
class ImplementationReturn(BaseModel):
    """Return schema for implementer sub-agents."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["implementation"]
    status: DelegationStatus
    files_modified: list[str]
    quality_gate_result: QualityGateResult
    test_results: TestResults | None = None
    decisions_made: list[dict[str, Any]] = Field(default_factory=list)
    carry_forward: list[str] = Field(default_factory=list)
    iterations: int = 1
```

**Validation rules (SubagentStop hook):**
1. `delegation_type == "implementation"` and `status` valid
2. `files_modified` non-empty (unless `status == "failed"`)
3. `quality_gate_result` exists with all 4 keys
4. All `quality_gate_result` values are `"pass"` (block + retry if not)
5. All files in `files_modified` exist on disk
6. No Write/Edit tool calls to test file globs

### 3.5 ScenarioWritingReturn

**Sub-agent:** scenario-writer
**delegation_type literal:** `"scenario_writing"`
**Extends conceptually:** `TddChunkReturn` (TDD-adjacent, writes test code)

```python
class ScenarioWritingReturn(BaseModel):
    """Return schema for scenario-writer sub-agents."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["scenario_writing"]
    status: DelegationStatus
    scenarios_written: list[ScenarioRecord]
    collect_only_result: CollectOnlyResult
    quality_gate_result: QualityGateResult | None = None
    decisions_made: list[dict[str, Any]] = Field(default_factory=list)
```

**Validation rules (SubagentStop hook):**
1. `delegation_type == "scenario_writing"` and `status` valid
2. `scenarios_written` non-empty (unless `status == "failed"`)
3. `collect_only_result.exit_code == 0`
4. `collect_only_result.collection_errors` is empty
5. All files in `scenarios_written[*].file` exist on disk
6. No Write/Edit to source file globs or non-scenario test globs

### 3.6 DebuggingReturn

**Sub-agent:** debugger
**delegation_type literal:** `"debugging"`
**Extends conceptually:** `GuidedReturn` (exercises judgment, resolves unknowns)

```python
class DebuggingReturn(BaseModel):
    """Return schema for debugger sub-agents."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["debugging"]
    status: DelegationStatus
    root_cause: RootCause
    fix_applied: FixApplied | None = None
    related_risks: list[str] = Field(default_factory=list)
    quality_gate_result: QualityGateResult | None = None
    unknowns_resolved: list[UnknownResolved] = Field(default_factory=list)
    decisions_made: list[dict[str, Any]] = Field(default_factory=list)
```

**Validation rules (SubagentStop hook):**
1. `delegation_type == "debugging"` and `status` valid
2. `root_cause` exists with non-empty `description` and valid `category`
3. If `status == "completed"`: `fix_applied` must exist with non-empty `files_modified`
4. If `fix_applied` exists: `quality_gate_result` must exist with all values `"pass"`
5. `root_cause.hypothesis_log` has ≥2 entries (warn, don't block — encourages systematic investigation)
6. No Write/Edit to test file globs

### 3.7 OptimizationReturn

**Sub-agent:** optimizer
**delegation_type literal:** `"optimization"`
**Extends conceptually:** `GuidedReturn` (exercises judgment, resolves unknowns)

```python
class OptimizationReturn(BaseModel):
    """Return schema for optimizer sub-agents."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["optimization"]
    status: DelegationStatus
    optimizations_applied: list[OptimizationEntry] = Field(default_factory=list)
    quality_gate_result: QualityGateResult | None = None
    worktree_branch: str
    diff_summary: str = ""
    no_optimization_found: bool = False
    unknowns_resolved: list[UnknownResolved] = Field(default_factory=list)
    decisions_made: list[dict[str, Any]] = Field(default_factory=list)
```

**Validation rules (SubagentStop hook):**
1. `delegation_type == "optimization"` and `status` valid
2. If `no_optimization_found == False`: `optimizations_applied` must be non-empty
3. If `optimizations_applied` non-empty: `quality_gate_result` must exist with all values `"pass"`
4. `worktree_branch` is non-empty string
5. No Write/Edit to test file globs

---

## 4. Updated Discriminated Union

The `DelegationReturn` union must include all 12 types (5 existing + 7 new):

```python
DelegationReturn = Annotated[
    # --- Existing base types (used by generic delegations) ---
    Annotated[TargetedReturn, Tag("targeted")]
    | Annotated[GuidedReturn, Tag("guided")]
    | Annotated[ExplorationReturn, Tag("exploration")]
    | Annotated[ResearchReturn, Tag("research")]
    | Annotated[TddChunkReturn, Tag("tdd_chunk")]
    # --- New sub-agent-specific types ---
    | Annotated[PlanVerificationReturn, Tag("plan_verification")]
    | Annotated[AuditReturn, Tag("audit")]
    | Annotated[TestWritingReturn, Tag("test_writing")]
    | Annotated[ImplementationReturn, Tag("implementation")]
    | Annotated[ScenarioWritingReturn, Tag("scenario_writing")]
    | Annotated[DebuggingReturn, Tag("debugging")]
    | Annotated[OptimizationReturn, Tag("optimization")],
    Discriminator(_delegation_type_discriminator),
]
```

The existing `_delegation_type_discriminator` function works unchanged — it extracts `delegation_type` from dict or model instance. The 7 new Literal values just add more dispatch targets.

**Backward compatibility:** The 5 existing types are preserved. Any sub-agent can still return a generic `GuidedReturn` or `TddChunkReturn` — the custom types are preferred but the base types remain valid. The SubagentStop hook validates against the specific type when present, falls back to the base type validation otherwise.

---

## 5. Shared Field Patterns (Factored Out)

Fields that appear across multiple return types, extracted as sub-models or type aliases:

| Pattern | Appears In | Representation |
|---------|-----------|----------------|
| `quality_gate_result` | Implementation, TestWriting, ScenarioWriting, Debugging, Optimization | `QualityGateResult` sub-model |
| `decisions_made` | All 7 new types + 3 existing | `list[dict[str, Any]]` (kept as-is, too generic for sub-model) |
| `carry_forward` | TestWriting, Implementation | `list[str]` (kept as-is, simple type) |
| `unknowns_resolved` | PlanVerification, Audit, Debugging, Optimization + existing Guided | `list[UnknownResolved]` (already a sub-model) |
| `status` | All types | `DelegationStatus` alias (already exists) |

**Why not a shared base class:** The 7 new types don't share enough structure to justify a base. The closest pattern is "types that extend GuidedReturn" (PlanVerification, Audit, Debugging, Optimization all have `unknowns_resolved` + `decisions_made`), but they each have very different required fields. A base class would either be too permissive (just `status` + `delegation_type`) or create awkward inheritance with field overrides. The flat model approach keeps each type self-contained and explicit.

---

## 6. SubagentStop Hook Validation Summary

The hook dispatches by `delegation_type` value. Each type has a validation function.

```python
# Dispatch table for SubagentStop hook
RETURN_VALIDATORS: dict[str, Callable] = {
    # Existing
    "targeted": validate_targeted,
    "guided": validate_guided,
    "exploration": validate_exploration,
    "research": validate_research,
    "tdd_chunk": validate_tdd_chunk,
    # New
    "plan_verification": validate_plan_verification,
    "audit": validate_audit,
    "test_writing": validate_test_writing,
    "implementation": validate_implementation,
    "scenario_writing": validate_scenario_writing,
    "debugging": validate_debugging,
    "optimization": validate_optimization,
}
```

### Validation severity levels

| Severity | Behavior | When |
|----------|----------|------|
| **Block + retry** | Hook returns error, sub-agent gets one retry to fix return | Structural issues: missing required fields, invalid enum values, inconsistent verdict/findings |
| **Warn only** | Hook logs warning, allows return | Non-critical issues: missing optional fields, INV-1 violations (logged for audit trail) |
| **Block (hard)** | Hook returns error, NO retry | Write boundary violations: sub-agent wrote files outside its allowed globs |

### Per-type validation functions

Each function receives the parsed JSON and returns `(valid: bool, errors: list[str], warnings: list[str])`.

**validate_plan_verification:**
- All 8 dimension keys present
- Each dimension has `pass` and `notes`
- Verdict-dimension consistency (PASS ↔ all pass, REVISE ↔ any fail)
- REVISE → revision_suggestions non-empty

**validate_audit:**
- Verdict present and valid enum
- Each finding has severity, category, file, description, recommendation
- Verdict-severity consistency (APPROVED ↔ no major/critical, CRITIQUE ↔ ≥1 major, ESCALATED ↔ ≥1 critical)

**validate_test_writing:**
- tests_written non-empty (unless failed)
- red_verification.all_tests_fail == true
- Test files exist on disk

**validate_implementation:**
- files_modified non-empty (unless failed)
- quality_gate_result all "pass"
- Modified files exist on disk

**validate_scenario_writing:**
- scenarios_written non-empty (unless failed)
- collect_only_result.exit_code == 0
- Scenario files exist on disk

**validate_debugging:**
- root_cause.description non-empty
- root_cause.category valid enum
- If completed: fix_applied exists with files_modified non-empty
- If fix_applied: quality_gate_result all "pass"

**validate_optimization:**
- optimizations_applied non-empty OR no_optimization_found == true
- If optimizations: quality_gate_result all "pass"
- worktree_branch non-empty

---

## 7. File Existence Checks in Hook

Three return types require the hook to verify files exist on disk: `test_writing`, `implementation`, `scenario_writing`.

**Implementation approach:** The SubagentStop hook runs in the same filesystem context as the sub-agent. It can use `os.path.exists()` on the returned file paths. For worktree-based sub-agents, the paths are absolute within the worktree.

```python
def _check_files_exist(paths: list[str]) -> list[str]:
    """Return list of paths that don't exist."""
    return [p for p in paths if not os.path.exists(p)]
```

This is a Block + retry check. If files don't exist, the sub-agent likely forgot to write them or returned wrong paths.

---

## 8. Migration Path

### Step 1: Add sub-models

Add to `schemas/delegation_return.py` in the `# --- Shared sub-models ---` section:
- `QualityGateResult`
- `AuditFinding` (+ `AuditSeverity`, `AuditCategory` type aliases)
- `TestRecord`
- `RedVerification`
- `RootCause` (+ `BugCategory`, `AffectedFile`, `HypothesisEntry`)
- `FixApplied`
- `OptimizationEntry` (+ `OptimizationCategory`)
- `PlanDimension` (+ `PLAN_DIMENSIONS`)
- `RevisionSuggestion`
- `ScenarioRecord`
- `CollectOnlyResult`
- `TestResults`

### Step 2: Add return models

Add after existing return models:
- `PlanVerificationReturn`
- `AuditReturn`
- `TestWritingReturn`
- `ImplementationReturn`
- `ScenarioWritingReturn`
- `DebuggingReturn`
- `OptimizationReturn`

### Step 3: Update discriminated union

Expand `DelegationReturn` to include all 12 types.

### Step 4: Update validate_return.py

Add validation functions for 7 new types in `scripts/validate_return.py` (or wherever the SubagentStop hook dispatch lives).

### Step 5: Update SubagentStop hook

Add 7 new entries to the validation dispatch table.

### Step 6: Tests

Write tests for:
- Each new model validates correct input
- Each new model rejects invalid input (missing fields, wrong types)
- Discriminated union dispatches correctly to all 12 types
- File existence checks work
- Verdict-findings consistency rules are enforced
- `PlanDimension` alias (`pass` ↔ `passed`) round-trips correctly

---

## 9. Token Cost Analysis

**Current state:** 5 return models, ~120 lines of Python. Sub-agents returning custom types today use `extra="allow"` passthrough — their domain fields are unvalidated.

**After migration:** 12 return models + 12 sub-models + type aliases, estimated ~350-400 lines. This is schema code — it doesn't enter agent context windows. It's loaded by the SubagentStop hook (runs in ~50ms) and by `validate_return.py` (runs in PTC).

**No agent token impact.** The return schemas are never loaded into sub-agent prompts. Sub-agents receive return format instructions in their agent definition body (`sub-agents/*.md`). The Pydantic models are for hook/validation use only.

# Evaluation Scenarios for sub-agent-delegation

## Scenario 1: Parallel Safety Check

**Setup:** Agent receives two tasks that appear independent but share a write target.

```
Task A: "Add input validation to src/api/handler.py"
Task B: "Add request logging to src/api/handler.py"
Both tasks look independent (different concerns), but both modify the same file.
```

**Expected behavior (WITH skill):**
1. Agent runs Step 1 scoping checklist on both tasks
2. Step 3 parallel check reveals both tasks modify `src/api/handler.py`
3. Agent chooses sequential dispatch (or restructures into separate files)
4. No silent file corruption

**Failure mode (WITHOUT skill):**
Agent dispatches both in parallel. One sub-agent's changes overwrite the other's. The resulting `handler.py` has validation OR logging, not both. Agent reports both tasks complete because each sub-agent individually succeeded.

---

## Scenario 2: Blind Trust Trap

**Setup:** Sub-agent returns optimistic report that doesn't match reality.

```
Dispatched Sonnet sub-agent: "Implement UserService.delete() with soft-delete
and cascade to related records. Tests in tests/test_user_service.py."

Sub-agent returns: "Done! Implemented soft-delete with cascade. All 4 tests pass.
Files modified: src/services/user_service.py, tests/test_user_service.py"
```

**Hidden reality:** Sub-agent wrote 3 tests (not 4), the cascade test is missing, and `pytest tests/test_user_service.py` shows 2 pass / 1 fail (the soft-delete flag isn't actually set in the database).

**Expected behavior (WITH skill):**
1. Agent runs Step 5 verification
2. Checks artifact exists (file exists — passes)
3. Reads content — notices only 3 tests, not 4 as claimed
4. Runs `pytest` — sees 1 failure
5. Returns to Step 4 with specific failure: "Cascade test missing, soft-delete test fails because flag not set in DB"

**Failure mode (WITHOUT skill):**
Agent reads sub-agent report, sees "all tests pass", marks task complete. Cascade bug discovered later during integration.

---

## Scenario 3: Context Sizing

**Setup:** Agent is about to dispatch a sub-agent for a focused task but has access to extensive project context.

```
Task: "Write a JSON config parser in src/config/parser.py"

Available context (15 files):
- src/config/loader.py (relevant — shows existing pattern)
- src/config/__init__.py (relevant — exports to match)
- src/config/defaults.py (marginally relevant — default values)
- README.md (irrelevant)
- docs/architecture.md (irrelevant)
- src/api/*.py (5 files, irrelevant)
- tests/test_api/*.py (5 files, irrelevant)
- pyproject.toml (irrelevant)
```

**Expected behavior (WITH skill):**
1. Agent applies Step 2 context sizing
2. Applies 30% test: 13 of 15 files are unrelated → trim
3. Includes: task description, loader.py content, __init__.py exports, ConfigError definition
4. Excludes: README, architecture doc, API files, unrelated tests
5. Sub-agent gets focused ~1500-token prompt, produces correct parser

**Failure mode (WITHOUT skill):**
Agent includes all 15 files "for completeness." Sub-agent reads architecture.md, decides to also refactor the config loading system "to align with the architecture," produces 3 files of changes when only 1 was needed.

---

## Scenario 4: Synthesis vs Concatenation

**Setup:** Two sub-agents return analyses of independent modules that have a hidden cross-module dependency.

```
Sub-agent 1 analyzed src/detection/:
"Detection module exports DetectionResult dataclass with fields:
bbox, confidence, class_id. Used by 3 internal functions."

Sub-agent 2 analyzed src/tracking/:
"Tracking module imports DetectionResult and adds track_id field
via inheritance: TrackedDetection(DetectionResult). Modifies bbox
format from (x,y,w,h) to (x1,y1,x2,y2) internally."
```

**Hidden issue:** The bbox format conversion in tracking means detection tests using (x,y,w,h) format will break if anyone changes DetectionResult's bbox field, but neither sub-agent flagged this as a cross-module risk.

**Expected behavior (WITH skill):**
1. Agent runs Step 6 synthesis
2. Step 6.4: "Find cross-cutting connections" — reads both analyses
3. Notices: tracking inherits from detection AND converts bbox format
4. Flags cross-module dependency: "DetectionResult.bbox format change would break TrackedDetection's internal conversion"
5. Adds this to combined analysis as a cross-module risk

**Failure mode (WITHOUT skill):**
Agent concatenates both analyses. The combined report says "detection exports DetectionResult" and "tracking imports DetectionResult" but doesn't connect these into a dependency risk. The bbox format fragility goes unreported.

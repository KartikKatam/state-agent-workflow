# Exploration Anti-Patterns

Common failures during codebase exploration. Each shown with WRONG (the failure) and RIGHT (the correction).

---

## 1. Timestamp-Based Staleness

WRONG: Checking `meta.updated_at` and flagging context as stale because it's "old."
```
"This was updated 2 weeks ago, I should refresh everything."
```
A two-week-old packet covering files that haven't changed is perfectly current.

RIGHT: Checking git-diff against `meta.files_analyzed`.
```bash
git diff --name-only <meta.commit_ref>..HEAD
# Intersect with meta.files_analyzed
# Only re-explore files in BOTH lists + their importers
```
Age is irrelevant. Content changes to analyzed files are what matter.

---

## 2. Inferring Dependencies from Imports

WRONG: Listing dependencies by scanning `import` statements.
```
Found: import torch, import cv2  →  Dependencies: torch, opencv-python
```
Import scanning misses four things that matter: version constraints (the manifest says `torch>=2.0,<3.0`), optional dependencies (installed in some envs but not all), packages installed under different names (`opencv-python` installs as `cv2`, `Pillow` installs as `PIL`), and dev-only dependencies that shouldn't appear in production context.

RIGHT: Reading the dependency manifest, recording as `DependencyEntry`.
```bash
cat requirements*.txt pyproject.toml setup.cfg package.json 2>/dev/null
```
Record `name`, `version_constraint`, `source` manifest, `dev_only` flag. The manifest is ground truth.

---

## 3. Exhaustive Cataloging

WRONG: Recording every function, class, and constant including internals.
```json
{"types": [
  {"name": "_internal_helper", "file": "utils.py:3"},
  {"name": "_format_string", "file": "utils.py:15"},
  {"name": "PublicAPI", "file": "utils.py:45"}
]}
```

RIGHT: Recording only public/exported types.
```json
{"types": [
  {"name": "PublicAPI", "file": "utils.py:45", "kind": "class",
   "usage": "main entry point for parsing", "exported": true}
]}
```
Test: would a planner building a NEW feature need to know about this type?

---

## 4. Prose in Context Packets

WRONG: Writing natural language descriptions in packet fields.
```json
{"purpose": "The producer module is responsible for detecting objects in video frames, tracking them across frames, and scoring quality"}
```
This is 25 tokens for information expressible in 8.

RIGHT: Using structured shorthand within field limits.
```json
{"purpose": "detection, tracking, ROI quality scoring"}
```
Every token in a context packet is paid for by every agent that loads it, every session. The `purpose` field has a 50-character limit for this reason.

---

## 5. Full Re-Exploration on Incremental Updates

WRONG: Re-reading the entire codebase when the lead says "refresh context."
```
Lead: "Refresh context after chunk-03 commit"
Explorer: *reads every file in the project*
```

RIGHT: Targeted re-read using the session log's `files_modified`.
```
1. Read session log → files_modified: ["src/batch.py", "src/config.py", "tests/test_batch.py"]
2. Find importers → ["src/pipeline.py"]
3. Re-read only those 4 files
4. Update only affected ModuleBlock entries, TypeEntry items, PatternEntry refs
5. Preserve manual_notes verbatim
```

---

## 6. Overlapping Sub-Agent Scopes

WRONG: Two sub-agents exploring the same directory.
```
Agent A scope: src/producer/, src/common/
Agent B scope: src/consumer/, src/common/
```
Both produce findings for `common/` — duplicate TypeEntry items, conflicting PatternEntry descriptions.

RIGHT: Disjoint scopes. One agent owns shared modules.
```
Agent A scope: src/producer/, src/common/
Agent B scope: src/consumer/
```
Agent B reports external interfaces TO `common/` (checklist point 4) without exploring inside it.

---

## 7. Too Many Sub-Agents

WRONG: 6 sub-agents for 15 files.
```
Agent 1: src/auth/     (3 files)
Agent 2: src/models/   (2 files)
...
Agent 6: src/config/   (1 file)
```
Each sub-agent costs: a dispatch prompt (~500 tokens), a verification read of its output, and a synthesis pass to merge its findings. With 6 agents exploring 15 files, coordination overhead (prompts, verification, conflict resolution, synthesis) exceeds the exploration work itself. You'd finish faster exploring 15 files directly.

RIGHT: Explore small codebases yourself. Group for larger ones.
```
# 15 files? Do it yourself.
# 30+ files across 4+ independent modules:
Agent 1: src/auth/, src/middleware/, src/config/  (7 files, related)
Agent 2: src/api/, src/models/, src/utils/        (8 files, related)
```
Group modules that share dependencies — they'll need the same context during exploration anyway. Memory constraint: sub-agents share your container's 1024MB. Heavy PTC analysis across 3-4 concurrent sub-agents can trigger 512MB eviction.

---

## 8. Including Source Code in Packets

WRONG: Copying code into the context packet.
```json
{"error_handling": {"pattern": "class AppError(Exception):\n    def __init__..."}}
```

RIGHT: Referencing the location with a conditional read pointer.
```json
{"name": "custom_exceptions", "style": "hierarchical exception classes",
 "example_location": "common/errors.py:5",
 "read_if": "adding new error types or changing error handling"}
```
Point to the code, don't copy it. `read_if` tells consumers WHEN to look.

---

## 9. Marking Stubs as Implemented

WRONG: Seeing class definitions and marking `implemented`.
```python
class BatchSelector:
    def select(self, candidates):
        pass  # TODO: implement
```

RIGHT: Checking for real logic, recording evidence.
```json
{"status": "stub",
 "status_evidence": "all methods have pass/TODO bodies, no test file exists"}
```
`implemented` means real logic. `stub` means signatures with placeholder bodies. Record `status_evidence` for non-obvious cases — planners need this for accurate task scoping.

---

## 10. Destroying Manual Notes on Update

WRONG: Writing a fresh packet that omits `manual_notes`.
```
Before: manual_notes: ["Auth uses JWT with 24h expiry — team decision", "Don't refactor config loader — legacy constraint"]
After: manual_notes: []
```

RIGHT: Read existing packet first, copy `manual_notes` verbatim.
```
Step 1: Read existing _codebase.json
Step 2: Save manual_notes list
Step 3: Generate new content
Step 4: Set manual_notes = saved list (unchanged)
```
This is the ONE field you never regenerate.

---

## 11. Confident-Sounding Uncertainty

WRONG: Reporting findings without confidence when evidence is indirect.
```json
{"purpose": "handles rate limiting and retry logic"}
```
Explorer saw one retry loop and a comment mentioning "rate limit" — but didn't find actual rate limiting code.

RIGHT: Using confidence scores and verification flags.
```json
{"purpose": "retry logic, possible rate limiting",
 "confidence": 0.5,
 "needs_verification": true}
```
A planner can work around uncertainty. A planner can't detect a confident-sounding guess. See Epistemic Standards in SKILL.md.

---

## 12. Exploring Without Clarifying Ambiguity

WRONG: Guessing at an ambiguous query and exploring the wrong thing.
```
Lead: "Explore the pipeline module"
Explorer: *explores src/ci/pipeline.yml*
Lead actually meant: src/producer/pipeline.py
```

RIGHT: Asking before exploring when the query is ambiguous.
```
Explorer → Lead: "There are two pipeline-related areas: src/ci/pipeline.yml
(CI config) and src/producer/pipeline.py (data processing). Which one?"
```
One clarifying message costs less than a wasted exploration.

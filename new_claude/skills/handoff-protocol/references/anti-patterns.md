# Handoff Anti-Patterns

Common handoff failures with WRONG/RIGHT examples. Each pattern represents a real failure mode where the successor agent lost time or produced incorrect work.

## Sender Anti-Patterns

### 1. Vague Resume Points

The most common handoff failure. A vague resume point forces the successor to reverse-engineer progress from code inspection.

```
WRONG:
  resume_point: "Continue implementing the batch processor"

RIGHT:
  resume_point: "Implementing BatchProcessor in producer/ops_batch.py.
    Done: select_batch() (lines 45-82, tested), score_diversity() (lines 84-120, tested).
    Next: apply_filters() per requirement R-04 — should filter candidates by
    confidence threshold before batch assembly."
```

WHY: "Continue implementing" tells the successor nothing about what exists, what's tested, or what's next. They spend 10+ minutes reading files to reconstruct state.

### 2. Missing Failed Approaches

Omitting what you tried means the successor will try it again — spending the same tokens to discover the same dead end.

```
WRONG:
  # (no failed approaches section)

RIGHT:
  failed_approaches:
    - "Tried inheritance for BatchProcessor (extends BaseProcessor).
       Failed: BaseProcessor assumes synchronous pipeline but batch ops are async.
       Would require rewriting BaseProcessor, which is out of scope."
    - "Tried dataclass for BatchResult. Failed: need custom __eq__
       for floating-point score assertions. Switched to regular class."
```

WHY: Each failed approach = 5-30 minutes of successor time saved. Two sentences per failure is cheap; re-discovery is expensive.

### 3. Paraphrased Auditor Assessment

When the auditor scraps, their exact words matter. Paraphrasing introduces interpretation bias.

```
WRONG:
  auditor_feedback: "The auditor thought the approach was too coupled
    and suggested using a different pattern."

RIGHT:
  auditor_feedback: |
    SCRAP. The BatchProcessor directly manipulates CandidatePool internals
    (accessing _candidates list, mutating scores in place). This violates
    the encapsulation boundary established in chunk-01. The processor should
    use CandidatePool's public API (get_candidates(), update_score()).
    Additionally, test_batch_selection mocks the scorer, making the test
    pass vacuously.
```

WHY: "Too coupled" could mean many things. "Directly manipulates _candidates list" tells the fresh coder exactly what boundary to respect.

### 4. Fix Suggestions in Scrap Handoffs

The predecessor's mental model was rejected. Suggesting fixes perpetuates the flawed model.

```
WRONG:
  scrap_notes: "The auditor scrapped because of coupling issues.
    I'd suggest the next coder use the adapter pattern to wrap
    CandidatePool's internal list."

RIGHT:
  scrap_notes: "Scrapped for coupling violations (see auditor assessment above).
    Root cause: I assumed direct access to CandidatePool internals was
    acceptable because the plan didn't explicitly forbid it. The plan's
    reference_files included CandidatePool's public API — I should have
    treated unlisted internals as off-limits."
```

WHY: "Use the adapter pattern" carries the same assumptions that led to the scrap. The fresh coder needs the CONSTRAINT (use public API), not a WORKAROUND from the same flawed perspective.

### 5. Undocumented Decisions

Decisions without rationale or lock status get overridden, causing decision drift across handoffs.

```
WRONG:
  decisions_made:
    - "Using composition for BatchProcessor"
    - "Async processing for batch operations"

RIGHT:
  decisions_made:
    - decision: "Composition over inheritance for BatchProcessor"
      reason: "User explicitly preferred composition during chunk-01 review"
      source: "user_preference"
      locked: true
    - decision: "Async processing for batch operations"
      reason: "BaseProcessor pipeline is async (confirmed in reference_files)"
      source: "codebase_evidence"
      locked: true
```

WHY: Without `source` and `locked`, the successor treats these as suggestions. "User explicitly preferred" tells them this is non-negotiable.

### 6. "Almost Done" Rationalization

Agents under context pressure convince themselves they can finish without a handoff. They invariably hit the wall mid-operation.

```
WRONG:
  Agent at 70% context:
  "I just need to write two more tests and run the quality gate.
   I'll skip the handoff and finish."
  → Agent hits context limit mid-test-write
  → Successor gets no handoff document
  → Successor re-implements from scratch

RIGHT:
  Agent at 70% context:
  Writes handoff document with:
    resume_point: "Tests for apply_filters() and validate_batch() not yet written.
      Implementation complete and manually verified. Quality gate not run."
  Then signals needs_replacement.
  → Successor writes two tests and runs the gate. 15 minutes total.
```

WHY: "Almost done" is the most dangerous rationalization because it feels true. Context pressure is non-linear — the last 30% fills faster than the first 70%.

### 7. File List Without State

Listing files without describing their state forces the successor to read every file end-to-end.

```
WRONG:
  files_modified:
    - producer/ops_batch.py
    - tests/test_ops_batch.py

RIGHT:
  files_modified:
    - "producer/ops_batch.py: BatchProcessor with select_batch() and
       score_diversity() implemented. apply_filters() is a stub (signature only)."
    - "tests/test_ops_batch.py: 6 tests for select_batch (passing),
       4 for score_diversity (passing). No tests for apply_filters."
```

WHY: File names tell WHAT exists. State descriptions tell WHERE things are. Without state, the successor burns context reading content you could summarize in one line.

### 8. Context Files Not Listed

The successor needs the same context you had. Missing this means they either miss critical context or waste time reading everything.

```
WRONG:
  # (no key_files_read section)

RIGHT:
  key_files_read:
    - ".claude/plans/lpr-plan.json — phase 2, tasks 03-05"
    - ".claude/context/lpr-context.json — sections: data_model, pipeline_architecture"
    - "producer/base_processor.py — async pattern reference"
    - "tests/conftest.py — CandidatePool test fixtures"
```

WHY: Context loading is the most expensive part of successor startup. A precise list means they load exactly what's needed.

## Receiver Anti-Patterns

### 9. Overriding Locked Decisions

The successor disagrees with a predecessor's approach and silently changes it, breaking consistency across the feature.

```
WRONG:
  Predecessor handoff: "composition for BatchProcessor — user preference — LOCKED"
  Successor: "Inheritance would be cleaner here. I'll use inheritance."
  → User discovers inconsistency during review
  → Rework across multiple files

RIGHT:
  Successor: "The handoff says composition is LOCKED (user preference).
    I'll follow this. If I find a strong reason to reconsider, I'll
    ask the user before changing."
```

WHY: LOCKED means the user decided, not the predecessor. Overriding without user consent is overriding the USER, not the predecessor.

### 10. Re-Discovering Documented Context

The successor ignores `key_files_read` and explores the codebase from scratch, burning context on information already documented.

```
WRONG:
  Successor loads handoff, sees key_files_read list.
  "I'll get a fresh perspective by reading the codebase myself."
  → Reads 15 files, fills 30% of context with information already
     summarized in the handoff
  → Hits context pressure sooner, triggers another handoff

RIGHT:
  Successor loads key_files_read from handoff first.
  Reads only those files. Uses remaining context for new work.
  → Reaches further into the task before context pressure
```

WHY: "Fresh perspective" sounds productive but wastes context budget. The predecessor already identified which files matter. Start there, explore further only if needed.

### 11. Retrying Failed Approaches

The successor ignores `what_didnt_work` and tries the same approach, hitting the same wall.

```
WRONG:
  Handoff says: "Tried inheritance — failed because BaseProcessor is sync-only."
  Successor: "Maybe they implemented it wrong. Let me try inheritance."
  → Spends 20 minutes discovering BaseProcessor is indeed sync-only

RIGHT:
  Successor reads what_didnt_work.
  "Inheritance was tried and failed (sync mismatch). I'll use composition
   as the predecessor decided, or find a third approach if composition
   doesn't fit either."
```

WHY: Unless you have specific new evidence the predecessor didn't have, retrying a documented dead end wastes tokens. If you DO have new evidence, document why this attempt is different.

### 12. Skipping User Acknowledgment

The successor starts working immediately without confirming what they carry forward, leaving the user uncertain about continuity.

```
WRONG:
  Successor loads handoff, starts coding immediately.
  User: "Wait, did you pick up the composition decision from the last agent?"
  → Interruption, context switching, trust erosion

RIGHT:
  Successor:
  "Resuming task-03 from apply_filters() implementation.
   Carrying forward: composition for BatchProcessor (user preference, LOCKED).
   Pending: diversity scoring algorithm — two approaches were discussed.
   Which approach would you like to go with?"
  → User confirms, work proceeds with confidence
```

WHY: The user talks to multiple agents. Without acknowledgment, they can't tell if continuity is preserved or if they need to re-explain decisions.

---
name: plan-adherence
description: Ensures implementation matches the approved plan. Provides checklist for verification and framework for documenting justified deviations. Loaded by chunk-coder during implementation.
---

# Plan Adherence Skill

> **Purpose**: Ensures implementation matches the approved plan and provides a framework for documenting justified deviations.
> **Consumers**: chunk-coder
> **Schemas**: None (reads plan JSON defined by `implementation-plans`)
> **Depends on**: `implementation-plans` (for plan structure knowledge)

## Contract

- ALWAYS run pre-implementation checklist before writing code
- NEVER modify files outside `scope.touched_files` without user approval
- ALL deviations MUST be documented with type, reason, and impact
- Deviations needing user approval MUST be flagged before presenting work

The plan is the contract. This skill ensures implementations match the approved plan, and documents any justified deviations.

## Core Principle

**Follow the plan exactly, or document why not.** The plan was reviewed and approved. Deviations require explicit justification.

## Pre-Implementation Checklist

Before writing any code, verify:

### 1. Dependencies Complete
```bash
# Check each dependency chunk is marked complete
cat .claude/plans/{feature}-plan.json | jq '.chunks[] | select(.id == "{dep_id}") | .status'
```
All dependencies must have `status: "completed"`.

### 2. Scope Understood
Read and internalize:
- `scope.primary_file` - Your main target
- `scope.touched_files` - All files you may modify
- `out_of_scope` - What you must NOT do

**Hard rule:** Do not modify files not in `scope.touched_files`.

### 3. Tasks Clear
For each task in `tasks[]`:
- Understand what it asks
- Know which file it affects
- Identify acceptance criteria

### 4. Invariants Understood
For each invariant:
- Understand what it verifies
- Know the command to run
- Know what success looks like

### 5. Test Spec Clear
From `test_spec`:
- Know what test file to create/modify
- Understand each test case
- Identify edge cases to cover

## During Implementation

### Task-by-Task Verification

After completing each task, verify:
- [ ] Task is done as specified
- [ ] No scope creep (nothing extra added)
- [ ] Follows codebase patterns
- [ ] No `out_of_scope` violations

### Scope Violation Detection

Before modifying any file, check:
```
Is this file in scope.touched_files?
├── Yes → Proceed
└── No → STOP. This is a scope violation.
         Either:
         a) The plan is incomplete (raise to user)
         b) You're doing something out of scope (stop)
```

### Out-of-Scope Vigilance

Before implementing anything, ask:
```
Is this in the out_of_scope list?
├── Yes → Do not implement. Skip it.
└── No → Proceed.
```

Common out_of_scope traps:
- "While I'm here, I'll also refactor..."
- "This would be better if I also added..."
- "The plan didn't mention this but it's needed..."

If something seems genuinely needed but is out of scope, document it as a deviation.

## Deviations

Sometimes deviations are necessary. The key is documentation.

### When Deviations Are Acceptable

| Acceptable | Not Acceptable |
|------------|----------------|
| Plan has a bug/impossibility | "I think this is better" |
| Missing dependency discovered | "While I'm here..." |
| Security/correctness issue | "This is more elegant" |
| Type system requires change | "User will probably want..." |

### Documenting Deviations

Add to session log:
```json
{
  "deviations": [
    {
      "type": "scope_addition",
      "description": "Added import to common/__init__.py",
      "reason": "Python import system requires this for type to be accessible",
      "plan_gap": "Plan didn't account for export chain",
      "impact": "Low - no behavioral change",
      "user_approval_needed": false
    },
    {
      "type": "task_modification",
      "description": "Used TypedDict instead of dataclass",
      "reason": "JSON serialization requirement not in plan",
      "plan_gap": "Plan didn't specify serialization needs",
      "impact": "Medium - changes API surface",
      "user_approval_needed": true
    }
  ]
}
```

### Deviation Types

| Type | Description | Approval Needed |
|------|-------------|-----------------|
| `scope_addition` | Touched file not in plan | If behavioral change |
| `scope_removal` | Didn't touch planned file | Yes |
| `task_modification` | Did task differently | If API changes |
| `task_addition` | Did something not in tasks | Yes |
| `task_removal` | Skipped a task | Yes |
| `invariant_change` | Invariant can't be met as written | Yes |

### Presenting Deviations

When presenting for review, include deviations prominently:

```
## Deviations from Plan

### 1. Added import to common/__init__.py
- **Reason**: Python import system requires this for type visibility
- **Impact**: No behavioral change
- **Approval needed**: No (mechanical necessity)

### 2. Used TypedDict instead of dataclass
- **Reason**: Discovered JSON serialization requirement
- **Impact**: API surface changes - fields accessed via `[]` not `.`
- **Approval needed**: Yes

Do you approve these deviations?
```

## Post-Implementation Verification

### Final Checklist

Before marking chunk complete:

- [ ] All tasks completed as specified
- [ ] All invariants pass
- [ ] All tests pass
- [ ] No files modified outside scope
- [ ] No out_of_scope work done
- [ ] All deviations documented
- [ ] Deviations needing approval flagged

### Verification Commands

```bash
# Verify only scoped files modified
git diff --name-only | while read f; do
  if ! grep -q "$f" <<< "{scope.touched_files}"; then
    echo "WARNING: Modified unscoped file: $f"
  fi
done

# Run all invariants
{for each invariant}
{invariant.verify.command}
{/for}

# Run tests
pytest {test_spec.test_file} -v

# Run full gate
./scripts/gate.sh
```

## Integration with chunk-coder

chunk-coder should:

1. **Load this skill** at session start
2. **Run pre-implementation checklist** before coding
3. **Check scope** before every file modification
4. **Check out_of_scope** before every implementation decision
5. **Document deviations** as they occur
6. **Run final checklist** before presenting

## Red Flags

Stop and reconsider if:
- You're modifying >5 files for a single chunk
- You're adding functionality not in tasks
- You're "improving" something not mentioned
- A deviation feels like it needs explanation
- You're unsure if something is in scope

When in doubt: **Ask the user, don't assume.**
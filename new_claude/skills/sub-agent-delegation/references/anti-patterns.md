# Delegation Anti-Patterns

Common delegation failures with WRONG/RIGHT examples.

## Contents
- [1. Blind Trust](#1-blind-trust) — Accepting sub-agent reports without verification
- [2. Context Dumping](#2-context-dumping) — Overloading prompt with irrelevant context
- [3. Parallel to Shared Files](#3-parallel-dispatch-to-shared-files) — Two sub-agents writing same file
- [4. Vague Deliverables](#4-vague-deliverables) — Open-ended "report back" tasks
- [5. Over-Scoping](#5-over-scoping) — Multiple deliverables crammed into one sub-agent
- [6. No Error Recovery](#6-no-error-recovery) — Escalating without retry
- [7. Concatenation not Synthesis](#7-concatenation-instead-of-synthesis) — Combining without finding connections
- [8. PTC: Listing All Packages](#8-ptc-listing-all-packages) — Overwhelming sub-agent with package choices
- [9. PTC: Delegating Mechanical Work](#9-ptc-delegating-mechanical-work) — Sub-agents for tasks a loop handles
- [10. PTC: Missing Print Discipline](#10-ptc-missing-print-discipline) — Sub-agent dumps raw data into context

## 1. Blind Trust

WRONG:
```
Dispatched sub-agent to implement feature X.
Sub-agent returned: "Done! Feature X implemented, all tests pass."
→ Marked task complete.
```

RIGHT:
```
Dispatched sub-agent to implement feature X.
Sub-agent returned: "Done! Feature X implemented, all tests pass."
→ Read the actual file — implementation exists.
→ Ran pytest tests/test_feature_x.py — 2 of 5 tests actually fail.
→ Retried sub-agent with specific failure output attached.
```

**Why:** Sub-agents report what they believe happened, not what actually happened. Verification is a 30-second investment that saves hours of debugging a "completed" task.

## 2. Context Dumping

WRONG:
```
"Here's the entire project structure, all 47 files, the full README,
the architecture doc, and the git history. Now write a function
that parses JSON config."
```

RIGHT:
```
"Write a JSON config parser in src/config/parser.py.
Follow the pattern in src/config/loader.py (included below).
Must handle: missing keys (default values), type errors (raise ConfigError).
Deliverable: ConfigParser class with load(path) → dict method."
[Include: loader.py content, ConfigError definition]
```

**Why:** Bloated context causes sub-agents to lose focus. They start "improving" unrelated code, following tangents from the architecture doc, or optimizing for concerns irrelevant to the task.

## 3. Parallel Dispatch to Shared Files

WRONG:
```
Sub-agent 1: "Add validation to src/api/handler.py"
Sub-agent 2: "Add logging to src/api/handler.py"
→ Both modify the same file → one overwrites the other
```

RIGHT (sequential):
```
Sub-agent 1: "Add validation to src/api/handler.py"
→ Verify completion
Sub-agent 2: "Add logging to src/api/handler.py" (with updated content)
```

RIGHT (parallel with restructure):
```
Sub-agent 1: "Create src/api/validation.py" (new file)
Sub-agent 2: "Create src/api/logging_middleware.py" (new file)
→ After both complete: integrate into handler.py yourself
```

**Why:** Two agents editing the same file produces merge conflicts at best and silent overwrites at worst. Neither agent knows about the other's changes.

## 4. Vague Deliverables

WRONG:
```
"Research how authentication works in this project and report back."
```

RIGHT:
```
"Identify the authentication flow in this project:
1. Find the auth middleware (likely in src/middleware/)
2. Trace the login endpoint request flow
3. Document: library used, where tokens are validated,
   where sessions are stored
Write to .claude/context/queries/auth-flow.json
following the query-result schema."
```

**Why:** "Report back" gives infinite scope. Sub-agent produces a 2000-word essay about authentication theory when you needed three specific facts.

## 5. Over-Scoping

WRONG:
```
"Implement the entire user management system: registration,
login, password reset, profile editing, role management,
and audit logging."
```

RIGHT:
```
Sub-agent 1: "Implement user registration: endpoint + model + tests"
Sub-agent 2: "Implement user login: endpoint + token generation + tests"
Sub-agent 3: "Implement password reset: endpoint + email trigger + tests"
[Sequential: profile, roles, audit depend on the above]
```

**Why:** A sub-agent given 6 tasks does 6 mediocre jobs instead of 1 excellent one. Single-deliverable scoping makes each result verifiable, retryable, and independent.

## 6. No Error Recovery

WRONG:
```
Dispatched sub-agent → failed.
→ Immediately escalated: "Sub-agent failed, what do I do?"
```

RIGHT:
```
Dispatched sub-agent → failed.
→ Read error output carefully.
→ Root cause: sub-agent misunderstood the file structure.
→ Retried with clarified context (added correct paths).
→ Succeeded on retry.
```

**Why:** First failures are usually prompt clarity issues, not fundamental problems. A retry with better context costs less than an escalation round-trip.

## 7. Concatenation Instead of Synthesis

WRONG:
```
Sub-agent 1 returned analysis of module A.
Sub-agent 2 returned analysis of module B.
→ Concatenated both reports and submitted.
```

RIGHT:
```
Sub-agent 1 returned analysis of module A.
Sub-agent 2 returned analysis of module B.
→ Read both analyses.
→ Found: module A exports a function that module B imports.
   Neither sub-agent saw this cross-module dependency.
→ Added cross-module dependency section to combined analysis.
→ Verified combined analysis covers all modules and their interactions.
```

**Why:** Individual sub-agents see their partition, not the whole. Synthesis means finding connections between parts that no individual sub-agent could see. Concatenation misses these connections.

## 8. PTC: Listing All Packages

WRONG:
```
"You have access to ptc_execute. Available packages: tree-sitter,
tree-sitter-python, tree-sitter-typescript, ast-grep-py, jedi, pyan3,
radon, vulture, cognitive-complexity, lizard, cohesion, networkx,
pandas, gitpython, pydriller, tiktoken."
→ Sub-agent confused by 16 packages. Tries to use 5 when it only needs 2.
```

RIGHT:
```
"You have access to ptc_execute. Available packages: ast (stdlib), radon.
Compute cyclomatic complexity for all functions in src/detection/.
Print JSON with function names and complexity scores."
→ Sub-agent uses exactly the 2 packages it needs. Focused output.
```

**Why:** Listing all packages gives the sub-agent too many options. It picks tools it doesn't understand, writes convoluted code, and produces unreliable results. List only the 3-5 packages the sub-agent actually needs for its specific task.

## 9. PTC: Delegating Mechanical Work

WRONG:
```
Spawn 5 sub-agents to read 10 files each and extract class names.
Each sub-agent calls ptc_execute with ast.parse.
→ 5 sub-agent round-trips for a task that takes one ptc_execute call.
```

RIGHT:
```
One ptc_execute call:
import ast, json, os
results = {}
for root, _, files in os.walk("/workspace/src"):
    for f in files:
        if f.endswith(".py"):
            with open(os.path.join(root, f)) as fh:
                tree = ast.parse(fh.read())
            results[f] = [n.name for n in ast.walk(tree)
                          if isinstance(n, ast.ClassDef)]
print(json.dumps(results))
```

**Why:** Sub-agents are for tasks needing LLM reasoning between steps. Purely mechanical work (parse files, extract data, aggregate) is faster and cheaper as a single `ptc_execute` call with a loop. Sub-agents add coordination overhead with no reasoning benefit.

## 10. PTC: Missing Print Discipline

WRONG:
```
Sub-agent prompt: "Use ptc_execute to analyze src/auth.py"
→ Sub-agent writes: data = await read_file(path="src/auth.py")
                    print(data["content"])  # 400KB dumped into context
```

RIGHT:
```
Sub-agent prompt: "Use ptc_execute to analyze src/auth.py.
Only print() output returns to your context — process data in the
container and print a JSON summary, not raw file contents."
→ Sub-agent processes in container, prints 200-byte summary.
```

**Why:** Without explicit print discipline instructions, sub-agents default to printing everything they read. This defeats PTC's purpose (keeping large data in the container) and wastes context tokens. Always include print discipline in PTC delegation prompts.

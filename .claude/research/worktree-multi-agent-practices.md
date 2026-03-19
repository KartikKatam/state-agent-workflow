# Multi-Agent Worktree Orchestration: Best Practices Research

**Research ID:** worktree-multi-agent-practices
**Completed:** 2026-03-01
**Confidence:** High (0.85) — multiple authoritative sources, validated against real production systems
**Sources Used:** WebSearch (15+ queries across 5 facets), WebFetch (Cursor docs, worktree-manager-skill, ccswarm)

---

## 1. Executive Summary

The multi-agent worktree pattern has become the dominant approach for parallel AI coding agents in 2025-2026. Key validated findings:

1. **Worktree-per-agent isolation is correct** and matches what Cursor, OpenAI Codex, VS Code background agents, Claude Code, and ccswarm all do. Our design aligns with the state of the art.

2. **Registry-based allocation** with global state tracking (not just the filesystem) is the right pattern. The `worktree-manager-skill` community standard uses `~/.claude/worktree-registry.json` — our design is equivalent.

3. **Our merge queue is sequential; industry leaders use speculative parallel drafts** (Mergify, GitLab Merge Trains, Bors-ng) that create cumulative test branches and run CI in parallel. This is the single biggest gap in our design.

4. **Squash-merge by default** is validated — Bors-ng, Mergify, and GitHub all prefer it. Our default is correct.

5. **Function-level conflict prevention** (our `touched_functions` approach) is ahead of industry. Most tools only operate at file-level. This is a genuine differentiator worth keeping.

6. **git rerere shared via common `.git/rr-cache`** is correct — worktrees share this directory. Our design works as described.

7. **Checkpoint refs are a custom-designed feature**. Industry uses git tags, ORIG_HEAD, and reflog for rollback. Our `refs/checkpoints/` pattern is more structured than typical practice.

8. **Micro-commit strategy for handoffs is unique** and not matched by any known system. The token efficiency argument is sound.

---

## 2. Worktree Lifecycle Management

### How Production Systems Allocate and Track Worktrees

**Cursor (commercial IDE, 2025):**
- Automatically creates and names worktrees per parallel agent (e.g., `feat-1-98Zlw`)
- Stores worktrees in `~/.cursor/worktrees/<repo>/`
- Maximum **20 worktrees per workspace** (hard limit)
- LRU eviction: oldest-by-last-access removed when limit exceeded
- Cleanup runs on a **6-hour interval**
- Each worktree has its own setup script via `.cursor/worktrees.json` with `setup-worktree-unix` / `setup-worktree-windows` keys
- Does NOT support LSP in worktrees — a known limitation
- Source: [Cursor Parallel Agents Docs](https://cursor.com/docs/configuration/worktrees)

**worktree-manager-skill (community standard for Claude Code):**
- Global registry at `~/.claude/worktree-registry.json`
- **2 consecutive ports per worktree** from a configurable global pool (default 8100–8199, enabling 50 simultaneous worktrees)
- Allocation algorithm: query registry → find first available port pair → write to registry → create worktree
- Cleanup validation: check PR merge status, uncommitted changes, non-git processes using ports
- Crash recovery: idempotent operations + periodic reconciliation of registry vs `git worktree list` output
- Slugified branch names, stored under `~/tmp/worktrees/<project>/<branch-slug>`
- Source: [worktree-manager-skill](https://playbooks.com/skills/scientiacapital/skills/worktree-manager-skill)

**OpenAI Codex (2025):**
- Worktrees used for parallel independent tasks within the same project
- Local vs. Worktree option when creating threads — worktree isolates from main project state
- Automations run on **dedicated background worktrees** to avoid conflicting with active work
- Source: [Codex Worktrees Docs](https://developers.openai.com/codex/app/worktrees/)

**VS Code Background Agents:**
- Spins up a **separate git worktree per session** so file changes live in an isolated folder
- Multiple background agents can run without conflicting edits
- Source: VS Code 1.107 release notes

**ccswarm:**
- Open-source multi-agent orchestration for Claude Code with git worktree isolation
- Basic lifecycle (create/list/remove/prune) is working; crash recovery is **incomplete** as of 2025
- Source: [ccswarm GitHub](https://github.com/nwiizo/ccswarm)

**Dagger Container Use:**
- Open-source tool from Dagger giving each agent its own **container + git worktree**
- Enables parallel conflict-free workflows without manual clone/stash juggling
- Source: [Container Use InfoQ](https://www.infoq.com/news/2025/08/container-use/)

### Failure Modes and Recovery

**Known failure modes (from git documentation and practitioner reports):**

| Failure Mode | Cause | Recovery |
|---|---|---|
| Stale metadata | Directory deleted with `rm -rf` instead of `git worktree remove` | `git worktree prune` |
| Locked worktree | Interrupted git process | `git worktree remove --force --force` (two `--force` flags needed) |
| Race condition on creation | Two processes creating same worktree | Use `--lock` flag at creation time (atomic equivalent of create + lock) |
| Lock file stuck | Crashed process left `index.lock` | Remove specific lock: `rm -f .git/worktrees/<name>/index.lock` (each worktree has its own index) |
| Stale registry | Crash between registry write and worktree creation | Reconcile registry vs `git worktree list` on startup |

**Automatic cleanup:**
- `git gc` calls `git worktree prune --expire 3.months.ago` (configurable via `gc.worktreePruneExpire`)
- Setting `gc.worktreePruneExpire=now` immediately prunes; `gc.worktreePruneExpire=never` suppresses auto-pruning
- **Lock flag at creation** (`git worktree add --lock`) prevents auto-pruning of worktrees on removable media

**Capacity limits:**
- Git has no hard limit on concurrent worktrees — disk space is the practical constraint
- Build cache directories (Bazel, npm, etc.) are the main disk cost; configure shared caches outside worktree directories
- Cursor chose 20 as a pragmatic upper bound; most practitioners suggest 4-8 for human developers
- Source: [Git worktree documentation](https://git-scm.com/docs/git-worktree)

### Relevance to Our Design

Our design's 4-worktree capacity limit, `/tmp/wt-{agent-id}/` path, agent state registry (`~/.claude/state/agents/`), and lifecycle-hook approach (WorktreeCreate / WorktreeRemove) are all sound and consistent with how Cursor and worktree-manager-skill work. Key difference: Cursor uses LRU eviction while we use a hard cap. Our approach is simpler and appropriate for an automated system with predictable load.

**Missing from our design:**
- No startup reconciliation between registry and actual filesystem state (crash recovery)
- No setup script support for worktree initialization (e.g., installing deps)

---

## 3. Merge Strategies for Parallel Agents

### The Three Merge Paradigms

#### A. Sequential FIFO Queue (GitHub native merge queue)
- Strict FIFO, no priority reordering
- Creates a temp branch `gh-readonly-queue/main/pr-{number}` per PR — cumulative but tested one at a time
- PR removed from queue on CI failure or merge conflict; no automatic rebasing
- **Double CI problem**: PRs already up-to-date with main still run CI twice
- Known issues: queue stalls, race conditions with temp branch deletion, rollback PRs get stuck in pending
- Practical throughput: ~1 merge per CI run duration
- Source: [GitHub Merge Queue Docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)

#### B. Batch + Bisect on Failure (Bors-ng)
- PRs accumulated into batches by priority. Higher priority = higher number, higher precedence.
- Creates `staging` branch = merge of all batched PRs onto main, triggers CI once for the batch
- On batch failure: **bisect** the batch, splitting in half, testing each half (O(log N) CI runs to identify culprit)
- Complexity: **O(E log N)** where E = failing PRs, N = total PRs
- Squash support via `use_squash_merge = true` in `bors.toml` — squashes each PR individually into staging
- Priority batching rule: **different priority PRs are never batched together**; higher priority preempts running lower-priority batch
- Source: [Bors-ng GitHub](https://github.com/bors-ng/bors-ng), [Bors Forum - Batching Strategy](https://forum.bors.tech/t/batching-strategy-vs-one-at-a-time/220)

#### C. Speculative Parallel Drafts / Railway Model (Mergify, GitLab Merge Trains, Aviator, Graphite)

This is the state-of-the-art approach. For a queue of PRs [A, B, C]:

1. Create 3 **cumulative draft branches** simultaneously:
   - Draft 1 = `main + A`
   - Draft 2 = `main + A + B`
   - Draft 3 = `main + A + B + C`

2. Run CI on all 3 in **parallel**

3. Results:
   - If all pass: merge A, B, C atomically in order
   - If Draft 2 fails (B is bad): remove B, rebuild Draft 2 as `main + A + C`, continue; Draft 3 is already invalid

**Performance:** Mergify reports 2.5× lower latency, 3× higher throughput vs. sequential testing.

**Failure search:** n-ary search algorithm to isolate culprits (faster than binary search for multiple simultaneous failures).

**Optimistic shortcutting (Aviator, Trunk):** If downstream draft N+1 passes and includes all of N's changes, N is considered validated and can merge immediately without waiting for its own draft.

Sources:
- [Mergify Speculative Checks Docs](https://docs.mergify.com/merge-queue/speculative-checks/)
- [Speculative Checks Under the Hood](https://articles.mergify.com/speculative-check-and-batch-under-the-hood/)
- [GitLab Merge Trains Docs](https://docs.gitlab.com/ci/pipelines/merge_trains/)
- [Trunk Parallel Queues](https://docs.trunk.io/merge-queue/parallel-queues)

### Monorepo: Independent Parallel Queues

For large codebases with many parallel contributors, **partitioned/independent queues** eliminate head-of-line blocking:

- **Mergify `partition_rules`:** File path conditions route PRs to independent sub-queues. `projectA/` changes go to the `projectA` partition. Partitions run CI and merge entirely in parallel. A PR touching both areas must pass both partitions.
- **Aviator monorepo mode:** Creates thousands of independent parallel queues by analyzing impacted build targets. Non-overlapping PRs merge concurrently.
- **Trunk:** "Parallel queues" based on impacted targets — PRs with non-overlapping blast radius merge on separate tracks simultaneously.
- **Nx/Turborepo:** `--affected` flag computes changed projects; Nx adds file-level precision beyond package-level.

Source: [Mergify Partition Rules](https://docs.mergify.com/merge-queue/partitions/), [Aviator Monorepo Merge Queues](https://www.aviator.co/blog/merge-queues-for-large-monorepos/)

### Squash vs. Rebase vs. Merge Commit

| Method | Pros | Cons | Who Uses It |
|---|---|---|---|
| **Squash** (default in our design) | One commit per PR, easy revert | Hides intermediate commits | Our design, Bors (opt-in), GitHub (opt-in) |
| **Rebase / fast-forward** | Linear history, preserves all commits | Conflicts can't create merge commits | Graphite stacks, pure trunk-based |
| **Merge commit** | Preserves full branch history, enables batch merge | Non-linear history, harder to revert | Bors default, traditional GitHub |

**Industry recommendation:** Squash for automated agent branches is correct — it produces clean, reviewable history on the integration branch while the detailed micro-commit history is preserved in the agent branch (and our backup refs).

---

## 4. Conflict Prevention

### File-Level: CODEOWNERS (Industry Standard, but Limited)

CODEOWNERS is the primary tool for ownership-based conflict prevention:

**Limitations for automated systems:**
- File-path only — cannot express function-level ownership
- No context awareness — can't require stricter review for production vs. development branches
- No complex logic — cannot AND multiple conditions (path + label + author)
- No pull request property access — cannot inspect PR size, authorship, or tags
- 3MB file size limit
- Floods high-tenured engineers with review requests

**Conclusion:** CODEOWNERS is insufficient for automated agent systems. Our `touched_functions` approach is strictly more powerful.

Source: [CODEOWNERS - GitHub Docs](https://docs.github.com/articles/about-code-owners), [The Definitive CODEOWNERS Guide](https://dev.to/aleixriba/codeowners-4jn3)

### Function-Level Conflict Detection (Research Territory)

No production tools perform function-level conflict prevention automatically. The state of research:

- Most tools operate at **file and directory level** for ownership
- Syde (IDE plugin) records changes at compile time for refined ownership, but is research-grade
- GitLive (2024): real-time cross-branch conflict detection at the diff level, not function level
- ML-based prediction (2025): stacking heterogeneous ensembles (Stack-SVM as top performer) using technical + social features shows promise but is not production-ready
- Research benchmark: predicting conflicts with F1=0.50, Accuracy=0.60 using static analysis (2024 IEEE/ACM)

**Conclusion:** Our `touched_functions` approach in plan JSON, validated pre-dispatch by the orchestrator, is **ahead of industry practice**. No known production system does function-level parallel conflict prevention.

Source: [Lightweight Semantic Conflict Detection with Static Analysis - ICSE 2024](https://dl.acm.org/doi/10.1145/3639478.3643118)

### Pessimistic Locking (File Locking)

- **Git LFS file locking:** `git lfs track --lockable`. One person at a time; files become read-only locally when others hold the lock. Used for binary assets (design files, videos).
- **Perforce Helix `+l`:** Prevents multiple simultaneous edits at the server level. Exclusive checkout. Used widely in game development.
- **Agent Farm (2025):** Lock-based system preventing agents from overwriting each other's changes on the same file.
- **Standard git:** No built-in file locking for text files — optimistic merging is the model.

**Conclusion:** File locking is not appropriate for our parallel coders since they operate on disjoint code areas (enforced by function overlap detection). Our optimistic approach with function-level pre-dispatch validation is correct.

Source: [Git LFS File Locking Wiki](https://github.com/git-lfs/git-lfs/wiki/File-Locking)

### Semantic Conflict Detection

"Semantic conflicts" are changes that merge without text conflicts but produce incorrect behavior:

- 2024 IEEE paper: static analysis with 4 techniques (Interprocedural Data Flow, Confluence, Override Assignment, Program Dependence Graph) — F1=0.50
- SAM (SemAntic Merge): auto-generated unit tests as partial specifications for detecting behavioral interference
- Recent extension: RefFilter uses refactoring-aware static analysis to reduce false positives

**Conclusion:** Semantic conflict detection is active research. For our system, the auditor's role in ARBITRATION mode (reading both branches + plan JSON) is our semantic conflict resolution mechanism. No automated tool can replace this for complex cases.

---

## 5. Conflict Resolution Automation

### Structured (AST-Based) Merge Tools

These tools parse code into ASTs and merge at the syntactic level, reducing spurious conflicts from code movement:

| Tool | Language | Accuracy | Notes |
|---|---|---|---|
| **JDime** | Java | ~79% | Auto-tuning between structured/unstructured; 92× faster than pure structured |
| **Spork** | Java | ~80% | Formatting preservation; 51% faster than JDime |
| **Mastery** | Language-agnostic | 82.90% | Bidirectional AST (top-down + bottom-up); 2.5× faster than JDime |
| **LastMerge** | Language-agnostic (Tree Sitter) | ~80% | 15% fewer false positives than JDime; comparable runtime |
| **Mergiraf** | Language-agnostic | ~85%+ | 42% fewer false negatives than Spork |
| **SemanticMerge** | C#, VB.NET, C, Java | Unknown | Method-by-method AST merge; commercial; good for cross-method moves |
| **MergeBot** | C/C++ | Unknown | Semi-structured merge with CI integration (Jenkins); from Activision |

**Key finding:** LastMerge (2025) and Mergiraf are generic (language-agnostic via Tree Sitter), showing that language-specific implementations can be replaced without significant accuracy loss.

Source: [Evaluation of Version Control Merge Tools ASE 2024](https://homes.cs.washington.edu/~mernst/pubs/merge-evaluation-ase2024.pdf), [LastMerge 2025](https://arxiv.org/abs/2507.19687)

### AI-Assisted Conflict Resolution

As of 2025-2026, AI conflict resolution is production-ready:

- **GitHub Copilot Pro+:** Automatically resolves complex merge conflicts (2025)
- **VS Code 1.105 (Sept 2025):** AI conflict resolution when opening files with conflict markers
- **GitKraken AI:** Suggests resolutions with explanations for GitHub/GitLab/Bitbucket
- **Project Harmony:** Fine-tuned Llama-3.1-8B and Qwen3-4B models, achieves **90% automatic resolution rate** for Android vendor merges; outperforms general-purpose LLMs despite being 20× smaller
- **Resolve.AI:** Gemini-powered automated resolution tool
- **ConGra benchmark (2025):** First large-scale conflict-resolution benchmark for LLM evaluation

**Conclusion:** AI resolution is mature enough to integrate. Our current approach (coder uses Think tool for simple conflicts, auditor for complex) is reasonable, but an AI-assisted resolution step before escalating to auditor would reduce auditor invocations.

Source: [The role of AI in merge conflict resolution - Graphite](https://graphite.com/guides/ai-code-merge-conflict-resolution), [Project Harmony - source.dev](https://www.source.dev/journal/harmony-preview)

### git rerere in Multi-Agent Context

- `git rerere` stores conflict resolutions in `.git/rr-cache`
- In multi-worktree setups, **all worktrees share the same `.git/rr-cache`** via the common git dir
- A resolution recorded by one coder automatically helps subsequent coders hitting the same conflict
- **Sharing across repositories:** Can be done via symlinks; not trivially shared across machines
- **Limitations:** Fails if file already contains lines resembling conflict markers; doesn't handle structural conflicts

**Conclusion:** Our design correctly enables rerere per-worktree and correctly identifies that all worktrees share `rr-cache`. This is a genuine benefit of worktrees vs. separate clones.

Source: [Git - Rerere](https://git-scm.com/book/en/v2/Git-Tools-Rerere)

### OT and CRDTs (Not Applicable to Our Use Case)

CRDTs (Conflict-free Replicated Data Types) and Operational Transformation are used for **real-time simultaneous editing** (Zed, Google Docs, Figma). They are not applicable to the asynchronous merge model used by agent worktrees. Our agents work on distinct branches and merge after completion — the git three-way merge model is correct for this pattern.

---

## 6. Branch Naming and Isolation Patterns

### Production Patterns for Automated Branch Names

| System | Branch Name Pattern | Notes |
|---|---|---|
| Our design | `agent/{agent-id}` | Deterministic, derivable from agent ID |
| Cursor | `feat-1-98Zlw` (auto-generated) | Type + number + random suffix |
| GitHub Merge Queue | `gh-readonly-queue/main/pr-{number}` | Hierarchical, purpose-encoded |
| Bors-ng | `staging`, `trying` | Stable reserved names |
| Mergify speculative | `mergify/merge-queue/{name}-{id}` | Tool-prefixed |
| Dependabot | `dependabot/{ecosystem}/{dependency}` | Package manager + dependency name |
| Renovate | Configurable prefix + dependency info | Customizable |
| Gerrit | `refs/for/<branch>` (push target, not branch) | Namespace-based |

### Git Ref Namespaces

Git supports arbitrary ref namespaces beyond `refs/heads/` and `refs/tags/`:

- Custom refs: `refs/checkpoints/`, `refs/backups/`, `refs/notes/`, `refs/for/`
- `GIT_NAMESPACE=foo` stores all refs under `refs/namespaces/foo/` — useful for multi-tenant repos
- **git notes:** `refs/notes/commits` — attach metadata to commits without changing SHA. Namespace-able: `refs/notes/junit`, `refs/notes/sonarqube`. Not pushed by default (requires explicit `push` of the notes ref).

**Conclusion:** Our `refs/checkpoints/{agent-id}/{state}` and `refs/backups/{base}-{timestamp}` follow well-established patterns. git notes could be used to attach build status metadata to commits without separate JSON files — a potential optimization.

Source: [Git gitnamespaces docs](https://git-scm.com/docs/gitnamespaces), [Git Notes - Tyler Cipriani](https://tylercipriani.com/blog/2022/11/19/git-notes-gits-coolest-most-unloved-feature/)

### Branch Lifecycle Best Practices

- Keep automated branches **short-lived** — up to 1-2 days maximum
- Use **type prefixes** for CI pipeline routing: `feature/`, `agent/`, `fix/`
- Automated cleanup via `gc.worktreePruneExpire` or explicit `git worktree remove` on task completion
- Orphan branches: detect with `git branch --merged` + `git branch --no-merged`; clean periodically
- Encode **structured data** in the agent state file, not the branch name — our approach (deriving branch from agent ID) is correct per Doc 0's zero-injection principle

---

## 7. Merge Queues and Ordering

### Algorithm Comparison Table

| System | Algorithm | Priority | Speculative CI | Failure Isolation | Rollback |
|---|---|---|---|---|---|
| GitHub Merge Queue | Sequential FIFO | No | No | Per-PR removal | Requeue manually |
| Bors-ng | Batch + bisect | Yes (p=N) | No (sequential) | Binary search | Requeue |
| GitLab Merge Trains | Cumulative parallel drafts | No | Yes (4 simultaneous) | Remove + re-run downstream | Cancel + re-queue |
| Mergify | Cumulative parallel drafts + priority | Yes (global) | Yes | n-ary search | Remove + rebuild |
| Aviator | Parallel drafts + impacted targets | Yes | Yes | Optimistic pass propagation | Remove + rebuild |
| Graphite | Stack-aware + fast-forward | No | Yes (within stack) | Topology-aware bisect | Remove stack entry |
| **Our design** | FIFO + priority + dependency | Yes | **No** | Auditor arbitration | Backup ref + reset |

### Gap: Our Queue Is Sequential

Our merge queue (FIFO + priority + dependency ordering) lacks **speculative parallel testing**. This means:

- When 3 coders complete simultaneously, merges happen one at a time
- Total merge time = 3 × (CI run duration)
- With speculative drafts, total time = 1 × (CI run duration) if all pass, or slightly more on failure

**For 4 parallel coders, this is the most significant scalability gap.** However:
- Our coders don't have CI runs (quality gate is run by the coder, not separately)
- The main bottleneck is the auditor review, not CI infrastructure
- Sequential merging may be acceptable given the small team size (≤4 coders)

### Priority and Dependency Rules

Our implementation matches Bors-ng's model:
- Priority from plan JSON `priority` field
- Dependencies from plan JSON `dependencies` field
- FIFO tiebreaker for equal-priority tasks
- Dependency-first ordering regardless of priority

This is consistent with what Aviator and Mergify implement, though they express it more flexibly via rule conditions.

---

## 8. Checkpoint and Rollback Strategies

### CI/CD Rollback Patterns

| Pattern | How | When to Use |
|---|---|---|
| **git reset --hard BACKUP_REF** | Reset integration branch to pre-merge state | Bad merge detected immediately |
| **git revert** | Create a new commit that undoes the bad commit | Bad merge already pushed/deployed |
| **ORIG_HEAD** | Automatically set before destructive operations (reset, rebase, merge) | Undo last merge immediately |
| **git reflog** | Local journal of all HEAD movements (90-day TTL, not pushed) | Recover from accidental reset |
| **Canary/blue-green** | Route traffic to new vs. old version | Deployment rollback |
| **kubectl rollout undo** | Kubernetes deployment rollback | Container deployment |

### Checkpoint Refs Pattern

Our checkpoint refs (`refs/checkpoints/{agent-id}/{state}`) are more structured than typical industry practice:

- Industry typically uses: git tags (manual), ORIG_HEAD (implicit), reflog (ephemeral)
- Our pattern: explicit refs at known-good states (TESTS_WRITTEN, TDD_GREEN, GATE_PASSED)
- This is inspired by database migration rollback patterns (savepoints at each migration step)
- The analogy is valid: each TDD phase transition is like a database migration — checkpoint before proceeding

**Validation:** No known multi-agent system uses this pattern. It is original to our design. The rationale (avoid wasting context on divergent implementation attempts) is sound and adds value not found elsewhere.

### git bisect Automation

`git bisect run <script>` enables fully automated regression finding:

```bash
git bisect start
git bisect bad HEAD
git bisect good refs/checkpoints/{agent-id}/tdd-green
git bisect run pytest tests/test_feature.py
```

This would automatically identify which micro-commit introduced a regression. Our checkpoint refs provide the clean bisect range. This is an underutilized capability in our current design.

---

## 9. Token/Context Efficiency in Git Operations

### Our Micro-Commit Handoff Strategy

Our design's key claim: micro-commits enable ~200-token handoffs (git log) vs. ~2000-8000 token handoffs (diff files).

**Industry validation:** No known system uses this specific pattern. However:
- Commit messages as structured data is standard practice
- OpenAI Codex's AGENTS.md convention encodes project context into a tracked file (similar principle — put context where agents can read it cheaply)
- The calculation is sound: `git log --oneline` of 2-3 micro-commits ≈ 50-100 tokens

**Conclusion:** The micro-commit handoff approach is novel and well-reasoned. It is not contradicted by any industry practice found. The token savings estimate is conservative.

### Merge Queue Format (TOON)

Our design uses TOON format for merge queue injection:
```
agent|task|pri|enqueued|status
coder-p1-t3-a7f2|t3|1|10:00|pending
```

Industry practice: GitHub/Mergify expose queue state via API (JSON), not injected context. Our optimization is specific to LLM context windows and has no direct industry parallel — but is well-motivated by Doc 0 principles.

### Branch Name Derivation (Zero Injection)

Our design derives branch name from agent ID without injection: `f"agent/{self.agent_id}"`. This matches the worktree-manager-skill pattern (slugified branch names derived from work identity). The principle is correct.

---

## 10. Recommendations

### Adopt Immediately (Validated by Industry)

1. **Stale worktree reconciliation on startup:** Add `git worktree list` reconciliation against agent state registry on orchestrator startup. Cursor and worktree-manager-skill both do this.

2. **`--lock` flag at worktree creation:** Use `git worktree add --lock` to prevent race conditions between creation and lock. Currently our hook does these separately.

3. **`gc.worktreePruneExpire` configuration:** Set to `never` (worktrees actively in use should never be auto-pruned by gc). Current design relies on the hook for cleanup, which is correct, but gc shouldn't race with it.

4. **git notes for micro-commit metadata:** Consider attaching TDD state metadata to commits via `git notes --ref=refs/notes/tdd` instead of separate JSON files. This keeps metadata with the commit and reduces filesystem state.

5. **Squash per PR (validated):** Our squash-by-default is confirmed correct by Bors-ng and general industry consensus for automated systems.

### Investigate Further

6. **Speculative parallel CI for merge queue:** If the system grows beyond 4 coders or CI run time becomes significant, adopting a Bors-ng-style batch+bisect or Mergify-style speculative drafts would dramatically improve throughput. The infrastructure would require: a "staging" branch per batch, concurrent quality gate runs (currently run inline by the coder), and failure isolation logic.

7. **AI-assisted conflict resolution before auditor escalation:** Project Harmony's 90% resolution rate on domain-specific merges suggests a fine-tuned small model could handle most conflicts before escalating to auditor. This would reduce auditor invocations significantly.

8. **git bisect automation for regression finding:** Our checkpoint refs create a perfect bisect range. Adding `git bisect run` automation to the IMPLEMENTATION → TDD_GREEN loop could automatically identify which micro-commit broke a test.

9. **Partition queues for feature parallelism:** If multiple features run simultaneously (each with their own set of parallel coders), Mergify's partition model — independent queues per feature area — would prevent cross-feature merge serialization. Currently our design has a global merge queue.

### Keep as-is (Ahead of Industry)

10. **Function-level conflict prevention (`touched_functions`):** No production system does this. Keep and invest in making it robust (better static analysis for touched_functions population).

11. **Checkpoint refs (`refs/checkpoints/`):** Not found in any known system. Well-designed and adds genuine value for rollback during long implementation loops.

12. **Micro-commit handoff strategy:** Novel and sound. The token efficiency benefit is real and validated by the calculation.

13. **Auditor arbitration for complex conflicts:** No known automated system handles complex multi-file semantic conflicts automatically without human review. Our auditor model is appropriate.

---

## 11. Gaps in Current Design

| Gap | Severity | Industry Approach | Our Current State |
|---|---|---|---|
| No speculative merge CI | Medium | Mergify/Bors parallel drafts | Sequential merges only |
| No startup registry reconciliation | High | worktree-manager-skill idempotent ops | Hook-based only, no crash recovery |
| No worktree setup scripts | Low | Cursor's `.cursor/worktrees.json` | Deps installed elsewhere |
| No independent partition queues | Low-Medium | Mergify partitions for monorepos | Global queue for all features |
| No AI conflict resolution pre-auditor | Low | Project Harmony, Copilot Pro+ | Escalates directly to auditor |
| No git bisect automation | Low | Standard git capability, underused | Manual checkpoint rollback |
| LSP in worktrees | Low-Medium | Cursor limitation too (no LSP in worktrees) | Presumably same |
| git notes for CI metadata | Low | Git notes convention | Separate JSON files |

---

## Sources

### Worktree Lifecycle
- [Git worktree documentation](https://git-scm.com/docs/git-worktree)
- [Cursor Parallel Agents / Worktrees](https://cursor.com/docs/configuration/worktrees)
- [worktree-manager-skill](https://playbooks.com/skills/scientiacapital/skills/worktree-manager-skill)
- [OpenAI Codex Worktrees](https://developers.openai.com/codex/app/worktrees/)
- [ccswarm GitHub](https://github.com/nwiizo/ccswarm)
- [Container Use - InfoQ](https://www.infoq.com/news/2025/08/container-use/)
- [Git worktrees for parallel AI coding agents – Upsun](https://devcenter.upsun.com/posts/git-worktrees-for-parallel-ai-coding-agents/)
- [How Git Worktrees Changed My AI Agent Workflow - Nx Blog](https://nx.dev/blog/git-worktrees-ai-agents)

### Merge Queues
- [GitHub Merge Queue Docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)
- [Mergify Speculative Checks Docs](https://docs.mergify.com/merge-queue/speculative-checks/)
- [Speculative Checks Under the Hood - Mergify](https://articles.mergify.com/speculative-check-and-batch-under-the-hood/)
- [GitLab Merge Trains](https://docs.gitlab.com/ci/pipelines/merge_trains/)
- [Trunk Parallel Queues](https://docs.trunk.io/merge-queue/parallel-queues)
- [Graphite Stack-Aware Merge Queue](https://graphite.com/blog/the-first-stack-aware-merge-queue)
- [Aviator Parallel Mode](https://docs.aviator.co/mergequeue/concepts/parallel-mode)
- [Bors-ng GitHub](https://github.com/bors-ng/bors-ng)
- [Bors Forum Batching Strategy](https://forum.bors.tech/t/batching-strategy-vs-one-at-a-time/220)
- [Mergify Partition Rules](https://docs.mergify.com/merge-queue/partitions/)

### Conflict Prevention
- [CODEOWNERS Definitive Guide](https://dev.to/aleixriba/codeowners-4jn3)
- [Lightweight Semantic Conflict Detection - ICSE 2024](https://dl.acm.org/doi/10.1145/3639478.3643118)
- [Merge Conflict Prediction 2025 - Wiley](https://onlinelibrary.wiley.com/doi/10.1002/smr.70047)
- [Git LFS File Locking](https://github.com/git-lfs/git-lfs/wiki/File-Locking)

### Conflict Resolution
- [Evaluation of Version Control Merge Tools - ASE 2024](https://homes.cs.washington.edu/~mernst/pubs/merge-evaluation-ase2024.pdf)
- [LastMerge 2025](https://arxiv.org/abs/2507.19687)
- [Project Harmony - 90% resolution rate](https://www.source.dev/journal/harmony-preview)
- [AI merge conflict resolution - Graphite](https://graphite.com/guides/ai-code-merge-conflict-resolution)
- [Git - Rerere](https://git-scm.com/book/en/v2/Git-Tools-Rerere)

### Branch Naming and Rollback
- [Git gitnamespaces docs](https://git-scm.com/docs/gitnamespaces)
- [Gerrit refs/for namespace](https://gerrit-review.googlesource.com/Documentation/concept-refs-for-namespace.html)
- [Git Notes - Tyler Cipriani](https://tylercipriani.com/blog/2022/11/19/git-notes-gits-coolest-most-unloved-feature/)
- [Git reflog recovery](https://graphite.com/guides/recovering-lost-commits-git-reflog)

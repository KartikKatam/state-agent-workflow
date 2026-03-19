# Test Tiers (Pass A / B / C)

When planning test architecture for a feature, design tests in three tiers.

## Pass A: Chunk-wise (Unit) Tests

Per-function tests designed alongside the implementation chunk.

| Category | What It Tests | Example |
|----------|--------------|---------|
| **Invariant** | Properties that always hold | `select_batch([]) == []` |
| **Golden** | Known input/output pairs | `select([0.9, 0.3, 0.8], min=0.5) == [0.9, 0.8]` |
| **Edge case** | Boundary and degenerate inputs | Single item, all same, at threshold |
| **Negative path** | Silent skips, fallbacks, guard clauses | Item skipped AND reason logged |
| **Log assertion** | Internal decisions visible via logs | `"rejected: quality=0.3 < 0.5" in caplog.text` |

## Pass B: Holistic (Integration) Tests

Cross-chunk and cross-module tests designed after all chunks are scoped.

| Category | What It Tests |
|----------|--------------|
| **Module integration** | Multiple functions working together |
| **Config sensitivity** | Same data, different config → different behavior |
| **Cross-module** | Component A's output feeds component B correctly |

## Pass C: System-Level Tests (conditional)

Activate when: feature touches a multi-stage pipeline, has external dependencies, or has performance requirements.

| Category | What It Tests |
|----------|--------------|
| **Production data** | Real stored samples processed without errors |
| **End-to-end pipeline** | Full input → all stages → output |
| **Performance benchmark** | Latency, throughput, memory within budget |

**Skip Pass C when**: Feature is pure business logic, configuration, or internal tooling with no pipeline/external-system interaction.

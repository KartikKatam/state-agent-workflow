Below is the **updated final Aggregator plan/design document** with all requested changes: **full confusion sets**, **duplicate-agreement override (0.06 + 0.60)**, **length EMA faster**, **strict confusion handling**, and an **extremely granular, factory-driven, matrix test bench** (deterministic + reproducible).

---

# OCR Aggregator Design — PARSeq Batch Aggregation + Multi-Pass Stabilization

**Status:** Ready for execution
**Last Updated:** 2026-02-15
**Scope:** Aggregate up to **12 OCR reads** (≤8 base + ≤4 enhanced duplicates) into a best-guess plate string per `track_id`, with conservative DONE gating and **LOW/MED/HIGH** confidence buckets.

---

## 0. Goals

1. **Maximize correctness** while minimizing **confidently-wrong** finalizations.
2. Use PARSeq per-character distributions (logits→softmax) and combine evidence in **weighted log space**.
3. **Fuse enhanced duplicates** without double-counting.
4. Resolve length using **EOS length distribution** (separate from character aggregation).
5. Support **multi-pass stabilization** (typical ≤3 passes) via EMA over distributions.
6. Always output a best guess with `LOW|MED|HIGH` confidence bucket.
7. Make system **deterministic, explainable, testable**, and parameterized through config with clear comments.

---

## 1. Inputs & Outputs

### Inputs (per OCR pass for one `track_id`)

* Per ROI: `p_roi[t, vocab]` (probs or logits convertible to probs), includes `EOS`.
* Selection metadata:

  * `duplicate_groups: group_id -> [base_idx, enhance_idx?]`
* Per ROI quality scalar `q ∈ [0,1]` (normalized), derived from rich-quality.

### Output (every pass)

* `best_string`
* `confidence_bucket: LOW|MED|HIGH`
* `done: bool`
* `reason_code` (examples):

  * `INTERIM_AMBIGUOUS`, `INTERIM_CONFUSION`,
  * `FINAL_ONEPASS_AGREE`, `FINAL_MULTIPASS_STABLE`,
  * `TIEBREAK_DUP_AGREE`, `TIEBREAK_CA_BIAS`
* Debug logging payload (config-gated):

  * per ROI: decoded string, per-char top1/top2/margin (optional), `q`, base/enh, group_id
  * length distribution top-2 lengths and probabilities
  * per-position top-k chars (optional)

---

## 2. Allowed Character Set

Default allowed tokens:

* `A–Z`, `0–9`

(Out-of-scope for now: special symbol plates; can be added later as separate `allowed_symbols` set.)

---

## 3. Confusion Sets (Complete)

These are **config-driven**. We store **Core (strict)** and **Extended (tie-break + optional strict)**.

### Core confusion sets (strict for HIGH/DONE gating)

* `{O, 0}`
* `{Q, 0}` (often conflated with `0` in blur/fonts)
* `{D, 0}`
* `{B, 8}`
* `{Z, 7}`
* `{I, 1}`
* `{S, 5}`
* `{G, 6}`

### Extended confusion sets (used for tie-break; can be promoted to strict if needed)

* `{2, Z}` (font-dependent, common in some plates)
* `{T, 7}` (low-res)
* `{J, 1}` (font-dependent)
* `{5, 2}` (occasionally)
* `{E, F}` (rare; font dependent)
* `{L, T}` (rare; font dependent)
* `{A, 4}` (rare; font dependent)
* `{V, Y}` (rare; font dependent)
* `{M, W}` (rare; font dependent)

**Rule:** strict gating applies to Core by default; Extended is tie-break-only unless turned on.

---

## 4. Duplicate Fusion (No double-counting)

### Evidence Units

Form evidence units per `group_id`:

* each unit contains base ROI and optional enhanced ROI.

### Fused distribution per unit

If both base & enhanced exist:

* `p_u(t,:) = normalize(p_base(t,:) + p_enh(t,:))`
  Else:
* `p_u(t,:) = p_base(t,:)`

### Duplicate agreement signal (Option A)

* `agree_u(t) = (argmax(p_base(t,:)) == argmax(p_enh(t,:)))` (when both exist)
* This signal is used only for **ambiguity overrides / tie-break**, not for probability boosting.

---

## 5. Quality Weighting (tanh, bounded, symmetric)

Each evidence unit has quality `q_u ∈ [0,1]`.

### Raw weight

[
w_u = 1 + \beta \cdot \tanh(k \cdot (q_u - q0))
]

Defaults:

* `q0 = 0.5`
* `k = 2.0` (gentle steepness)
* `beta = 0.2` → raw weights in ~`[0.8, 1.2]`

### Normalize + cap per pass

* `w_mean = mean(w_u)`
* `w_norm = w_u / w_mean`
* `w_tilde = min(w_cap, w_norm)`

Defaults:

* `w_cap = 1.3`

---

## 6. Length from EOS (separate; EMA faster)

### Per-unit length distribution via EOS hazard

[
P_u(L=t) = p_u(t,EOS)\cdot\prod_{k=1}^{t-1}(1 - p_u(k,EOS))
]

Compute in log-space:

* `logP_u(L=t) = log(eps + p_u(t,EOS)) + sum_{k<t} log(eps + (1 - p_u(k,EOS)))`

### Aggregate across units (weighted log sum)

[
\log P_{pass}(L=t) = \sum_u w_{\tilde{u}} \cdot \log P_u(L=t)
]
Normalize:

* `P_pass_len(t) = softmax_t(logP_pass(L=t))`

### Length EMA per hypothesis

[
ema_Plen \leftarrow (1-\alpha_{len}),ema_Plen + \alpha_{len},P_{pass_len}
]

Defaults:

* `alpha_len = 0.20` (faster convergence)

Hypothesis length:

* `L*_H = argmax ema_Plen_H(t)`

---

## 7. Character Aggregation (log space, EOS removed)

For a candidate length `L`, for each position `t ≤ L`:

### Remove EOS and renormalize to allowed chars

[
p'*u(t,c)=\frac{p_u(t,c)}{\sum*{c\in allowed} p_u(t,c)}
]

### Weighted log aggregation

[
\log P_{pass}(t,c) = \sum_u w_{\tilde{u}}\cdot\log(\epsilon + p'_u(t,c))
]
Normalize:

* `P_pass_char(t,:) = softmax_c(logP_pass(t,:))`

Decode:

* `c_t = argmax P_pass_char(t,:)`
* candidate string `S(L) = c_1..c_L`

Candidate score:
[
score(L)=\sum_{t=1}^{L}\log(\epsilon + P_{pass_char}(t,c_t))
]
Optional length term:

* `score’(L) = score(L) + lambda_len * log(eps + P_pass_len(L))`
* Default `lambda_len = 0.25`

---

## 8. Two Candidate Strings per Pass (C1, C2)

* `L1 = argmax P_pass_len`
* `L2 = second_argmax P_pass_len`

Compute:

* `C1 = (S(L1), score’(L1), P_pass_char_L1, P_pass_len)`
* `C2 = (S(L2), score’(L2), P_pass_char_L2, P_pass_len)`

---

## 9. Duplicate Agreement Override in Ambiguity (New)

You requested: if duplicates agree, they can flip a close call (e.g., ~0.47 vs 0.53), but not large gaps (0.45 vs 0.55).

### Override parameters (locked)

* `delta_agree_override = 0.06`
* `Th_pair_agree_p1 = 0.60`

### Mechanism (per position t)

Let top-1 char be `a` with prob `p1`, top-2 char be `b` with prob `p2`.
Margin `m = p1 - p2`.

If:

* `m <= delta_agree_override`
* and there exists at least one duplicate pair within the pass whose **base and enhanced agree on `b`** at position t
* and that agreeing pair has `min(p_base_top1, p_enh_top1) >= Th_pair_agree_p1`
  Then:
* **choose `b` instead of `a`** at this position for this candidate
* set reason marker `TIEBREAK_DUP_AGREE` for logging

**Important:** this only applies inside a narrow ambiguity band; it does not globally boost probabilities.

---

## 10. Persistent Hypothesis Bank (H1, H2) using EMA Distributions

Per `track_id`, maintain exactly two hypotheses `H1` and `H2`.

Each hypothesis stores:

* `ema_Plen(t)`
* `ema_Pchar[t, allowed_chars]`
* `string` decoded from EMA
* `win_count`, `last_seen_pass`, `last_score_est` (optional)

### EMA update

When hypothesis H is updated with candidate C:

* `ema_Plen` updated with `alpha_len=0.20`
* `ema_Pchar` updated with `alpha_char=0.15`:

[
ema_Pchar[t,:] \leftarrow (1-\alpha_{char}),ema_Pchar[t,:] + \alpha_{char},P_{pass_char}(t,:)
]

Defaults:

* `alpha_char = 0.15`

### Candidate-to-hypothesis matching & replacement (deterministic)

Process `C1` then `C2`:

1. If `C.string == H1.string` → update H1
2. Else if `C.string == H2.string` → update H2
3. Else replace H2 if:

   * H2 empty/stale OR C is significantly stronger

Defaults:

* `stale_passes = 3`
* `replace_margin = 0.5` (log-score domain)

After update:

* decode `H.string` from its EMA distributions using `L*_H = argmax ema_Plen`.

---

## 11. Confidence Buckets (Mean-driven + Safety Gates + Confusion Strict)

From the chosen hypothesis (usually H1) compute per-position:

* `p1_t`: max prob
* `p2_t`: second max
* `margin_t = p1_t - p2_t`

Compute:

* `mean_p1`, `min_margin`, `mean_margin`

### Bucket logic

* Start with mean-driven thresholds
* Apply **safety gates** for HIGH (min margin floor + strict confusion clamp)

Defaults (initial; tune with bench):

* `HIGH` if:

  * `mean_p1 >= 0.90` AND
  * `min_margin >= 0.10` AND
  * no strict confusion position violates strict margin (below)
* `MED` if:

  * `mean_p1 >= 0.75` AND
  * `mean_margin >= 0.05`
* else `LOW`

### Strict confusion clamp (Core sets)

If any position t has top2 in a **Core confusion set** AND:

* `margin_t < Th_confusion_margin_high`
  then bucket cannot be HIGH.

Defaults:

* `Th_confusion_margin_high = 0.20`

---

## 12. DONE Criteria (Conservative; supports one-pass)

DONE has two pathways.

### Path A — One-pass strong agreement DONE

Using current pass C1 (not EMA):
DONE if:

* `P_pass_len(L1) >= 0.70`
* for all positions t:

  * `p1_t >= 0.90`
  * `margin_t >= 0.12`
* strict confusion requirement:

  * if confusion (Core) at any t: `margin_t >= 0.25`

### Path B — Multi-pass stabilization DONE (≤3 passes)

Using H1 EMA:
DONE if:

* confidence bucket is HIGH
* `H1.win_count >= 2`
* H2 is not competitive (one of):

  * H2 missing/stale
  * H2 bucket MED/LOW while H1 HIGH
  * (optional) H1 score estimate exceeds H2 by `Th_hyp_gap_done = 0.5`

Reason codes:

* `FINAL_ONEPASS_AGREE`
* `FINAL_MULTIPASS_STABLE`

---

## 13. CA Bias Tie-break (rare, ambiguous only)

Only if:

* `abs(score1 - score2) < Th_tie_score` (default 0.25)
* ambiguity dominated by confusion positions / length ambiguity

Apply CA regex bias as tie-break (do not raise confidence):

* choose candidate more compatible with CA patterns
* mark reason `TIEBREAK_CA_BIAS`

---

## 14. Config & Comment Contract (Required, Labeled)

### File location

Add to consumer config module (recommended `consumer/config.py`).

### Dataclasses

* `OcrAggregationConfig`

  * `QualityWeightConfig` (`q0`, `k`, `beta`, `w_cap`)
  * `EmaConfig` (`alpha_char`, `alpha_len`)
  * `LengthConfig` (`eps`, `lambda_len`, `max_T`)
  * `DuplicateAgreementConfig` (`delta_agree_override`, `Th_pair_agree_p1`)
  * `ConfusionConfig` (core sets, extended sets, strict margins)
  * `ConfidenceConfig` (LOW/MED/HIGH thresholds)
  * `DoneConfig` (one-pass + multi-pass thresholds)
  * `TieBreakConfig` (CA tie thresholds)

### Comment requirement (MUST)

Every tunable parameter **must include comments** covering:

1. what it controls
2. expected range
3. effect of ↑ / ↓
4. why this default fits: ≤3 passes, conservative, avoid confident-wrong

This is a required checklist item in the implementation plan.

---

## 15. Implementation Plan (Files & Responsibilities)

### New module

* `consumer/ops_ocr_aggregate.py`

  * `OcrAggregator` class with:

    * `on_ocr_result(track_id, version, ocr_batch_result) -> AggregationDecision`
    * internal state store per `track_id`

### Models/types

Add to `consumer/models.py`:

* `OcrPassCandidate`
* `OcrHypothesisState` (EMA distributions + bookkeeping)
* `AggregationDecision`
* enums: `ConfidenceBucket`, `ReasonCode`

### Integration

* OCR completion callback in pipeline (wherever queue results are received):

  * call aggregator
  * log decision
  * if `done=True`: call the queue’s `mark_done(track_id)` / finalize pathway

---

## 16. Test Bench (Extremely Granular, Factory + Matrix)

### Structure

* `tests/factories/ocr_agg_factory.py`

  * deterministic generators for:

    * ROI distributions over vocab
    * EOS distributions (true length, ambiguous length)
    * duplicate groups and agreement patterns
    * quality `q` patterns including blur-bias simulation
  * all factory outputs reproducible from `(seed, scenario_id)`.

* `tests/test_ocr_aggregation_matrix.py`

  * parametrized “scenario matrix” tests (explicit families + seeded variations)

### Determinism rules

* Every test case has:

  * `scenario_id`, `seed`
  * logged on failure
* No nondeterministic ops, no random without seeded RNG.

### Scenario matrix coverage (required)

Each scenario family runs multiple seeds (e.g., 20–100), but deterministically.

1. **Ambiguity axis**

* unambiguous high-conf
* unambiguous low-conf
* ambiguous high-conf (high p1, tiny margin)
* ambiguous low-conf

2. **Quality axis**

* all high quality
* mixed quality
* all low quality
* blur-bias adversarial: low-q systematically favors “rounder” confusion char

3. **Duplicate axis**

* none
* duplicates agree on correct
* duplicates agree on wrong
* duplicates disagree

4. **Length axis**

* single clear length
* two competing lengths near-tie
* three-way length ambiguity
* early EOS bias in low quality (should not dominate if high quality exists)

5. **Temporal axis (passes)**

* 1 pass only (must output, may DONE only if one-pass criteria met)
* 2 passes converge to correct
* 3 passes converge
* conflicting pass then resolves
* stays ambiguous (must not DONE)

### Granular assertions (must include)

* **Weight mapping correctness**

  * tanh bounds, symmetry, normalization to mean 1, cap applied
* **EOS hazard correctness**

  * `P_u(L=t)` sums ~1 over t (within epsilon), and aggregation behaves as expected
* **Log aggregation invariants**

  * adding supportive evidence increases probability of that char (monotonic)
* **Duplicate non-double-count**

  * same evidence as one unit; enhancement does not count twice
* **Duplicate agreement override**

  * triggers only when `margin <= 0.06`
  * requires `pair_p1 >= 0.60`
  * does NOT trigger when margin is larger (e.g., 0.10)
* **EMA convergence**

  * char EMA moves toward repeated evidence across passes
  * length EMA converges faster than char (alpha_len > alpha_char)
* **Confusion strictness**

  * HIGH and DONE blocked when Core confusion margin below strict thresholds
* **Blur tolerance**

  * mixed-quality case: correct high-q evidence wins against low-q blur bias over passes
  * all low-q case: should remain MED/LOW and avoid DONE unless truly unambiguous
* **Threshold behavior**

  * separate tests that sweep values across the boundary (just below/above) for:

    * HIGH bucket
    * one-pass DONE
    * multi-pass DONE

---

## 17. Defaults (As Agreed)

* Quality weight:

  * `q0=0.5`, `k=2.0`, `beta=0.2`, `w_cap=1.3`
* EMA:

  * `alpha_char=0.15`, `alpha_len=0.20`
* Duplicate override:

  * `delta_agree_override=0.06`, `Th_pair_agree_p1=0.60`
* Confusion strict:

  * `Th_confusion_margin_high=0.20`
  * DONE confusion margin `0.25` (one-pass strict)
* Candidate count:

  * exactly **2 candidates per pass**

All in config with required explanatory comments.

---

If you want the next execution step, I can turn this into the exact **file-by-file implementation checklist** (with function signatures and where each test factory plugs in), aligned to your existing `consumer/ops_*` patterns and your `.claude` “test bench verifies precisely as defined” workflow.

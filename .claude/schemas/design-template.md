# Design: {Feature Name}

<!--
  DESIGN DOCUMENT TEMPLATE — CONVERSION INSTRUCTIONS

  This template converts a freeform design document into a structured format
  consumed by an AI planning agent. The planner uses this to break features
  into implementable chunks, map domain concepts to codebase constructs,
  and generate test cases.

  TO CONVERT: Paste your source design document alongside this template.
  Follow the extraction instructions in each section's comments.
  The XML tags in Design Decisions are required structure, not optional.

  IMPORTANT:
  - This document is CODEBASE-AGNOSTIC. Replace any file paths, class names,
    or language-specific types from the source with domain-level descriptions.
    Example: "consumer/ops_batch_select.py" → "the batch selection module"
    Example: "List[RoiRichQuality]" → "list of ROI crops with quality metrics"
  - Preserve ALL design reasoning. If the source explains WHY a choice was made,
    that reasoning MUST appear in a <rationale> tag. Lost reasoning cannot be
    recovered by the planner.
  - Preserve numerical specifics: weights, thresholds, limits, defaults.
    These are tuning decisions, not implementation details.
-->

**Date**: {YYYY-MM-DD}
**Status**: draft | in-review | approved
**Author**: {name}

## Objective

<!-- EXTRACT: The core purpose statement. Look for introductory paragraphs,
     "purpose" or "goal" sections, or the document title/subtitle.
     COMPRESS TO: 2-3 sentences covering WHAT the feature does and WHY it exists.
     DROP: Implementation details, technical approach, algorithm descriptions.
     KEEP: The capability being added, the problem it solves, the motivation. -->

## Design Decisions

<!-- EXTRACT: Every place the source document makes a deliberate architectural
     choice, states a design principle, or rejects an alternative approach.
     Look for:
     - Explicit principles ("only signal through", "quality over quantity")
     - Rejected alternatives ("no degraded mode", "no filler")
     - Behavioral rules ("return fewer if needed", "strict diversity")
     - Guardrails ("don't enhance excellent images", "don't enhance blur")

     For EACH decision, decompose into three parts using the XML tags below:
     - <decision name="...">: A short name for the choice (imperative or descriptive)
     - <rationale>: WHY this choice was made. Include: the problem it solves,
       what alternatives were considered, why they were rejected, and any
       domain-specific reasoning (performance, correctness, cost, user experience).
       This is the most critical field — the planner uses it to evaluate whether
       to suggest changes.
     - <constraint>: The testable MUST/MUST NOT rules that follow from this decision.
       These become invariants in the implementation plan.

     If the source document has a "Design Philosophy" or "Core Principles" section,
     each principle likely maps to one <decision> block.
     If the source has guardrails or edge-case handling rules scattered through
     algorithm descriptions, consolidate them into decision blocks here. -->

<decision name="">
<rationale></rationale>
<constraint></constraint>
</decision>

## Technical Approach

<!-- EXTRACT: The algorithm, pipeline, or data flow from the source document.
     COMPRESS: Replace code blocks with numbered conceptual steps. Each step
     should describe WHAT happens, not HOW (no function signatures, no variable
     names, no language-specific constructs).
     PRESERVE: The ordering of steps, the flow between them, and any
     branching/conditional logic.
     PRESERVE: Specific numerical values embedded in the algorithm
     (weights, thresholds, percentages) — these are design decisions.

     Example compression:
     SOURCE: "def apply_eligibility_gate(candidates, cfg): ..."  (20 lines of Python)
     TARGET: "1. Strict eligibility gating — hard filter on coarse viability flag
              and minimum plate size. Reject all candidates that fail either check."

     Each numbered step is a potential chunk boundary for the planner.
     Steps should be ordered by data flow dependency. -->

## I/O Contract

<!-- EXTRACT: Input and output specifications from the source document.
     Look for "Input Contract", "Output Contract", function signatures of
     the main entry point, or dataclass/type definitions for input/output.
     CONVERT TO DOMAIN LANGUAGE: Replace type names with descriptions.
     PRESERVE: Cardinality (counts, ranges), structure (nested fields),
     guarantees (ordering, uniqueness), and metadata.

     Example conversion:
     SOURCE: "candidates: List[RoiRichQuality]" with 30+ metric fields
     TARGET: "list of up to 32 ROI crops, each with 30+ photometric/geometric
             quality metrics, eligibility flags, an 11D pose signature, and
             a fast quality score" -->

**Ingests**:
<!-- What data this feature consumes. Include: structure, cardinality,
     key fields the algorithm depends on (even if there are many,
     list the ones referenced by the technical approach). -->

**Produces**:
<!-- What data this feature outputs. Include: structure, guarantees,
     metadata, and edge-case outputs (e.g., empty result). -->

## Configuration Surface

<!-- EXTRACT: All configurable parameters, weights, thresholds, and limits
     from the source document. Look for config classes, "Configuration
     Parameters" sections, or default values scattered through the code.
     PRESERVE: Every parameter name (in domain language), its purpose,
     and its default value. These are tuning decisions.
     GROUP related parameters if the source does so.

     | Knob | Purpose | Default |
     |------|---------|---------|
     | ...  | ...     | ...     | -->

## Behavioral Examples

<!-- EXTRACT: Expected behavior examples, test scenarios, worked examples,
     or "behavior" sections from the source document.
     PRESERVE: The specific numbers (input counts, output counts) and
     the reasoning for non-obvious outcomes.
     INCLUDE AT MINIMUM: happy path, degraded input, boundary case, empty input.
     If the source lacks examples, derive them from the algorithm description
     and edge cases mentioned in the design decisions.

     Format each as:
     **{Scenario name}** ({conditions}): {expected outcome}. {reasoning if non-obvious}. -->

## Out of Scope

<!-- EXTRACT: Explicit boundaries from the source document. Look for "does not",
     "out of scope", "not included", or "future work" mentions.
     Also INFER boundaries from what the feature touches: anything adjacent
     but not modified should be listed here.
     Format as a bulleted list of "Does not..." statements. -->

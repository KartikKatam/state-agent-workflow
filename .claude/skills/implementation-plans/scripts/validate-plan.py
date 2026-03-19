#!/usr/bin/env python3
"""
Validate implementation plans against schema AND semantic chunking rules.

Usage: python validate-plan.py <plan.json>

Checks:
1. Schema compliance (required fields, types)
2. Vertical slice rules (no orphan plumbing)
3. Dependency DAG validity (no cycles, dependencies exist)
4. Invariant level matches chunk purpose
5. Every chunk has enforceable tests
6. `delivers` describes a capability (contains action verb)

Exit codes:
  0 - Valid plan
  1 - Errors found (blocking)
  2 - Warnings only (non-blocking)
"""

import json
import re
import sys

# Purpose → minimum required invariant levels
PURPOSE_INVARIANT_REQUIREMENTS = {
    "foundational": {"existence"},
    "core_logic": {"behavioral", "constraint", "system"},
    "extension": {"behavioral", "constraint", "system"},
    "integration": {"system"},
    "hardening": {"behavioral", "constraint", "system"},
}

# Words that indicate a capability (for `delivers` validation)
CAPABILITY_VERBS = {
    "can",
    "enables",
    "allows",
    "supports",
    "provides",
    "produces",
    "selects",
    "filters",
    "processes",
    "handles",
    "manages",
    "creates",
    "validates",
    "transforms",
    "integrates",
    "connects",
    "exposes",
}

# Words that indicate "X exists" (bad `delivers`)
EXISTENCE_ONLY_PATTERNS = [
    r"^[A-Z][a-zA-Z]+ exists",
    r"^dataclass (is )?(defined|created)",
    r"^config (is )?(added|defined)",
    r"^type (is )?(defined|created)",
    r"^file (is )?(created|added)",
]


def load_plan(filepath: str) -> dict:
    """Load and parse plan JSON."""
    with open(filepath) as f:
        return json.load(f)


def check_schema_basics(plan: dict) -> list[str]:
    """Check required fields exist."""
    errors = []

    # Top-level required
    for field in ["meta", "feature", "chunks", "quality_gates", "module_tests"]:
        if field not in plan:
            errors.append(f"Missing required field: {field}")

    if "meta" in plan:
        for field in ["type", "feature_name", "status"]:
            if field not in plan["meta"]:
                errors.append(f"Missing meta.{field}")
        if plan["meta"].get("type") != "plan":
            errors.append(f"meta.type must be 'plan', got '{plan['meta'].get('type')}'")

    if "chunks" in plan:
        if not isinstance(plan["chunks"], list) or len(plan["chunks"]) == 0:
            errors.append("chunks must be a non-empty array")

    if "quality_gates" in plan:
        if "gate_script" not in plan["quality_gates"]:
            errors.append("quality_gates.gate_script is required")

    if "module_tests" in plan:
        if "integration_scenarios" not in plan["module_tests"]:
            errors.append("module_tests.integration_scenarios is required")
        elif len(plan["module_tests"]["integration_scenarios"]) == 0:
            errors.append("module_tests.integration_scenarios must not be empty")

    return errors


def check_chunk_required_fields(chunk: dict, idx: int) -> list[str]:
    """Check each chunk has required fields."""
    errors = []
    chunk_id = chunk.get("id", f"chunk[{idx}]")

    required = ["id", "name", "purpose", "delivers", "scope", "tasks", "invariants"]
    for field in required:
        if field not in chunk:
            errors.append(f"{chunk_id}: Missing required field '{field}'")

    if "scope" in chunk and "primary_file" not in chunk["scope"]:
        errors.append(f"{chunk_id}: scope.primary_file is required")

    if "invariants" in chunk:
        if not isinstance(chunk["invariants"], list) or len(chunk["invariants"]) == 0:
            errors.append(f"{chunk_id}: Must have at least one invariant")

    if "purpose" in chunk:
        valid_purposes = [
            "foundational",
            "core_logic",
            "extension",
            "integration",
            "hardening",
        ]
        if chunk["purpose"] not in valid_purposes:
            errors.append(
                f"{chunk_id}: Invalid purpose '{chunk['purpose']}'. Must be one of {valid_purposes}"
            )

    return errors


def check_invariant_levels(chunk: dict) -> list[str]:
    """Check invariant levels match chunk purpose requirements."""
    errors = []
    chunk_id = chunk.get("id", "unknown")
    purpose = chunk.get("purpose")
    invariants = chunk.get("invariants", [])

    if not purpose or not invariants:
        return errors

    required_levels = PURPOSE_INVARIANT_REQUIREMENTS.get(purpose, set())
    actual_levels = {inv.get("level") for inv in invariants if inv.get("level")}

    # Check if at least one invariant meets the requirement
    if purpose != "foundational":
        # Non-foundational chunks need behavioral/constraint/system invariants
        has_strong_invariant = bool(actual_levels & required_levels)
        if not has_strong_invariant:
            errors.append(
                f"{chunk_id}: Purpose '{purpose}' requires at least one invariant with level "
                f"in {required_levels}, but only found {actual_levels or 'none'}"
            )

    # Check each invariant has a level
    for inv in invariants:
        if "level" not in inv:
            errors.append(
                f"{chunk_id}: Invariant '{inv.get('id', '?')}' missing 'level' field"
            )

    return errors


def check_delivers_is_capability(chunk: dict) -> list[str]:
    """Check that `delivers` describes a capability, not just existence."""
    warnings = []
    chunk_id = chunk.get("id", "unknown")
    delivers = chunk.get("delivers", "")

    if not delivers:
        return warnings

    delivers_lower = delivers.lower()

    # Check for existence-only patterns (bad)
    for pattern in EXISTENCE_ONLY_PATTERNS:
        if re.match(pattern, delivers, re.IGNORECASE):
            warnings.append(
                f"{chunk_id}: 'delivers' appears to describe existence, not capability: \"{delivers}\". "
                f"Rephrase to describe what the system CAN DO after this chunk."
            )
            return warnings

    # Check for capability verbs (good)
    has_capability_verb = any(verb in delivers_lower for verb in CAPABILITY_VERBS)
    if not has_capability_verb:
        warnings.append(
            f"{chunk_id}: 'delivers' may not describe a capability: \"{delivers}\". "
            f"Consider using verbs like: can, enables, allows, supports, processes, etc."
        )

    return warnings


def check_vertical_slice_completeness(plan: dict) -> list[str]:
    """Check for orphan plumbing chunks (types/config only with no logic)."""
    warnings = []
    chunks = plan.get("chunks", [])

    for chunk in chunks:
        chunk_id = chunk.get("id", "unknown")
        purpose = chunk.get("purpose", "")
        tasks = chunk.get("tasks", [])

        # If foundational, check it's actually shared
        if purpose == "foundational":
            # Count how many other chunks depend on this one
            dependents = sum(1 for c in chunks if chunk_id in c.get("dependencies", []))
            if dependents < 2:
                warnings.append(
                    f"{chunk_id}: Purpose is 'foundational' but only {dependents} other chunk(s) depend on it. "
                    f"Consider merging into the first chunk that uses it (vertical slice)."
                )

        # Check for "types/config only" chunks that should be merged
        task_texts = " ".join(t.get("task", "") for t in tasks).lower()
        has_logic = any(
            word in task_texts
            for word in ["implement", "create function", "write", "add logic", "call"]
        )
        has_plumbing = any(
            word in task_texts
            for word in ["dataclass", "config", "type", "export", "__init__"]
        )

        if has_plumbing and not has_logic and purpose != "foundational":
            warnings.append(
                f"{chunk_id}: Appears to be plumbing-only (types/config) but purpose is '{purpose}'. "
                f"Either add logic to this chunk or mark as 'foundational' (if shared by 2+ chunks)."
            )

    return warnings


def check_dependency_dag(plan: dict) -> list[str]:
    """Check dependencies form a valid DAG (no cycles, all refs exist)."""
    errors = []
    chunks = plan.get("chunks", [])
    chunk_ids = {c.get("id") for c in chunks}

    # Check all dependencies exist
    for chunk in chunks:
        chunk_id = chunk.get("id", "unknown")
        for dep in chunk.get("dependencies", []):
            if dep not in chunk_ids:
                errors.append(f"{chunk_id}: Dependency '{dep}' does not exist")

    # Check for cycles using DFS
    def has_cycle(start: str, visited: set, stack: set) -> bool:
        visited.add(start)
        stack.add(start)

        chunk = next((c for c in chunks if c.get("id") == start), None)
        if chunk:
            for dep in chunk.get("dependencies", []):
                if dep not in visited:
                    if has_cycle(dep, visited, stack):
                        return True
                elif dep in stack:
                    return True

        stack.remove(start)
        return False

    visited = set()
    for chunk in chunks:
        chunk_id = chunk.get("id")
        if chunk_id and chunk_id not in visited:
            if has_cycle(chunk_id, visited, set()):
                errors.append(f"Dependency cycle detected involving {chunk_id}")

    return errors


def check_test_enforceability(plan: dict) -> list[str]:
    """Check each chunk has enforceable tests (not just existence checks)."""
    warnings = []
    chunks = plan.get("chunks", [])

    for chunk in chunks:
        chunk_id = chunk.get("id", "unknown")
        purpose = chunk.get("purpose", "")
        test_spec = chunk.get("test_spec", {})
        invariants = chunk.get("invariants", [])

        # Foundational chunks can skip tests
        if purpose == "foundational":
            continue

        # Check for test_spec
        if not test_spec or not test_spec.get("cases"):
            warnings.append(
                f"{chunk_id}: No test_spec.cases defined. "
                f"Add specific test cases to verify chunk functionality."
            )
            continue

        # Check test cases have meaningful descriptions
        cases = test_spec.get("cases", [])
        for case in cases:
            tests_desc = case.get("tests", "")
            if len(tests_desc) < 10:
                warnings.append(
                    f"{chunk_id}: Test case '{case.get('name', '?')}' has vague description: \"{tests_desc}\""
                )

        # Check at least one invariant runs tests
        has_test_invariant = any(
            "pytest" in inv.get("verify", {}).get("command", "") for inv in invariants
        )
        if not has_test_invariant:
            warnings.append(
                f"{chunk_id}: No invariant runs pytest. Add an invariant that verifies tests pass."
            )

    return warnings


def check_scope_includes_plumbing(plan: dict) -> list[str]:
    """Check that if tasks mention config/models, those files are in scope."""
    warnings = []
    chunks = plan.get("chunks", [])

    plumbing_keywords = {
        "config": ["config.py"],
        "dataclass": ["models.py"],
        "type": ["models.py"],
        "__init__": ["__init__.py"],
        "export": ["__init__.py"],
    }

    for chunk in chunks:
        chunk_id = chunk.get("id", "unknown")
        tasks = chunk.get("tasks", [])
        scope = chunk.get("scope", {})
        touched = scope.get("touched_files", [])
        primary = scope.get("primary_file", "")
        all_files = set(touched + [primary])

        task_text = " ".join(
            t.get("task", "") + " " + t.get("details", "") for t in tasks
        ).lower()

        for keyword, expected_files in plumbing_keywords.items():
            if keyword in task_text:
                for expected in expected_files:
                    if not any(expected in f for f in all_files):
                        warnings.append(
                            f"{chunk_id}: Tasks mention '{keyword}' but '{expected}' not in scope.touched_files"
                        )

    return warnings


def check_deferred_testing(plan: dict) -> tuple[list[str], list[str]]:
    """Check that deferred testing (tested_by) is used appropriately."""
    errors = []
    warnings = []
    chunks = plan.get("chunks", [])
    chunk_ids = {c.get("id") for c in chunks}

    deferred_count = 0
    total_count = len(chunks)

    for chunk in chunks:
        chunk_id = chunk.get("id", "unknown")
        purpose = chunk.get("purpose", "")
        tested_by = chunk.get("tested_by")
        test_spec = chunk.get("test_spec")

        if tested_by:
            deferred_count += 1

            # Only foundational chunks can defer testing
            if purpose != "foundational":
                errors.append(
                    f"{chunk_id}: 'tested_by' is only allowed for 'foundational' purpose chunks, "
                    f"but this chunk has purpose '{purpose}'"
                )

            # Must have reason
            if not tested_by.get("reason"):
                errors.append(
                    f"{chunk_id}: 'tested_by.reason' is required when deferring tests"
                )

            # Referenced chunks must exist
            for ref in tested_by.get("chunks", []):
                if ref not in chunk_ids:
                    errors.append(
                        f"{chunk_id}: tested_by references non-existent chunk '{ref}'"
                    )

            # Referenced chunks should have tests
            for ref in tested_by.get("chunks", []):
                ref_chunk = next((c for c in chunks if c.get("id") == ref), None)
                if ref_chunk and not ref_chunk.get("test_spec", {}).get("cases"):
                    warnings.append(
                        f"{chunk_id}: tested_by references '{ref}' which has no test cases"
                    )

        # Non-foundational without test_spec or tested_by is suspicious
        elif purpose != "foundational" and not test_spec:
            warnings.append(
                f"{chunk_id}: Purpose is '{purpose}' but has no test_spec. "
                f"Add tests or use tested_by with reason if tests must be deferred."
            )

    # Warn if too many chunks defer testing
    if total_count > 0 and deferred_count / total_count > 0.2:
        warnings.append(
            f"High deferred testing ratio: {deferred_count}/{total_count} chunks use tested_by. "
            f"Consider combining chunks into vertical slices that can be tested independently."
        )

    return errors, warnings


def check_hardening_chunk_exists(plan: dict) -> list[str]:
    """Check that at least one hardening chunk exists."""
    errors = []
    chunks = plan.get("chunks", [])

    hardening_chunks = [c for c in chunks if c.get("purpose") == "hardening"]

    if not hardening_chunks:
        errors.append(
            "Plan must have at least one chunk with purpose 'hardening' "
            "to implement module_tests integration scenarios"
        )
        return errors

    # Hardening chunks should have system-level invariants
    for chunk in hardening_chunks:
        chunk_id = chunk.get("id", "unknown")
        invariants = chunk.get("invariants", [])
        has_system = any(inv.get("level") == "system" for inv in invariants)

        if not has_system:
            errors.append(
                f"{chunk_id}: Hardening chunk must have at least one 'system' level invariant"
            )

    # Hardening chunks should depend on core_logic chunks
    core_logic_ids = {c.get("id") for c in chunks if c.get("purpose") == "core_logic"}
    for chunk in hardening_chunks:
        chunk_id = chunk.get("id", "unknown")
        deps = set(chunk.get("dependencies", []))

        # Should depend on at least one core_logic chunk (directly or transitively)
        if core_logic_ids and not deps & core_logic_ids:
            # Check transitive dependencies
            all_deps = set()
            to_check = list(deps)
            while to_check:
                dep_id = to_check.pop()
                if dep_id in all_deps:
                    continue
                all_deps.add(dep_id)
                dep_chunk = next((c for c in chunks if c.get("id") == dep_id), None)
                if dep_chunk:
                    to_check.extend(dep_chunk.get("dependencies", []))

            if not all_deps & core_logic_ids:
                errors.append(
                    f"{chunk_id}: Hardening chunk should depend on core_logic chunks "
                    f"(directly or transitively)"
                )

    return errors


def check_module_tests_coverage(plan: dict) -> list[str]:
    """Check that module_tests scenarios cover the core chunks."""
    warnings = []
    chunks = plan.get("chunks", [])
    module_tests = plan.get("module_tests", {})
    scenarios = module_tests.get("integration_scenarios", [])

    if not scenarios:
        return warnings

    # Get all core_logic chunk IDs
    core_logic_ids = {c.get("id") for c in chunks if c.get("purpose") == "core_logic"}

    # Get all chunks covered by scenarios
    covered_ids = set()
    for scenario in scenarios:
        covered_ids.update(scenario.get("covers", []))

    # Warn about uncovered core_logic chunks
    uncovered = core_logic_ids - covered_ids
    if uncovered:
        warnings.append(
            f"module_tests.integration_scenarios do not cover core_logic chunks: {uncovered}"
        )

    return warnings


def validate_plan(filepath: str) -> tuple[list[str], list[str]]:
    """Run all validations. Returns (errors, warnings)."""
    errors = []
    warnings = []

    try:
        plan = load_plan(filepath)
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"], []
    except FileNotFoundError:
        return [f"File not found: {filepath}"], []

    # Schema checks (errors)
    errors.extend(check_schema_basics(plan))

    # Per-chunk checks
    for idx, chunk in enumerate(plan.get("chunks", [])):
        errors.extend(check_chunk_required_fields(chunk, idx))
        errors.extend(check_invariant_levels(chunk))
        warnings.extend(check_delivers_is_capability(chunk))

    # Cross-chunk checks
    errors.extend(check_dependency_dag(plan))
    warnings.extend(check_vertical_slice_completeness(plan))
    warnings.extend(check_test_enforceability(plan))
    warnings.extend(check_scope_includes_plumbing(plan))

    # Deferred testing checks
    deferred_errors, deferred_warnings = check_deferred_testing(plan)
    errors.extend(deferred_errors)
    warnings.extend(deferred_warnings)

    # Hardening and module tests checks
    errors.extend(check_hardening_chunk_exists(plan))
    warnings.extend(check_module_tests_coverage(plan))

    return errors, warnings


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate-plan.py <plan.json>")
        sys.exit(1)

    filepath = sys.argv[1]
    errors, warnings = validate_plan(filepath)

    if errors:
        print(f"❌ ERRORS in {filepath}:")
        for e in errors:
            print(f"  • {e}")
        print()

    if warnings:
        print(f"⚠️  WARNINGS in {filepath}:")
        for w in warnings:
            print(f"  • {w}")
        print()

    if not errors and not warnings:
        print(f"✓ {filepath} is valid")
        sys.exit(0)
    elif errors:
        print(f"\n❌ Plan has {len(errors)} error(s) that must be fixed.")
        sys.exit(1)
    else:
        print(f"\n⚠️  Plan has {len(warnings)} warning(s) (non-blocking).")
        sys.exit(2)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Convert a free-form design document into structured workflow format.

Structural parsing only — deterministic Python regex, no API calls.
Semantic mapping (embeddings, coverage analysis) is deferred to the
strategist's sub-agents via PTC.

Usage:
    python scripts/convert_design.py input.md --output design.md --report coverage.md
    python scripts/convert_design.py input.md  # stdout, no report

Recognized sections (case-insensitive, order-independent):
    - Objective / Goal / Purpose / Overview
    - Design Decisions (with <decision>, <rationale>, <constraint> XML tags)
    - Technical Approach / Architecture / Implementation
    - I/O Contract / Input/Output / Interface
    - Configuration Surface / Configuration / Config
    - Behavioral Examples / Examples / Scenarios
    - Out of Scope / Non-Goals / Exclusions

Original text is quoted verbatim — never paraphrased.
Coverage report flags unmapped content.

Design doc ref: "Design Doc Conversion" section, lines 564-569.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Section aliases — maps normalized names to canonical section names
# ---------------------------------------------------------------------------
SECTION_ALIASES: dict[str, str] = {
    # Objective
    "objective": "Objective",
    "goal": "Objective",
    "goals": "Objective",
    "purpose": "Objective",
    "overview": "Objective",
    "summary": "Objective",
    # Design Decisions
    "design decisions": "Design Decisions",
    "decisions": "Design Decisions",
    "design rationale": "Design Decisions",
    "key decisions": "Design Decisions",
    # Technical Approach
    "technical approach": "Technical Approach",
    "architecture": "Technical Approach",
    "implementation": "Technical Approach",
    "implementation approach": "Technical Approach",
    "approach": "Technical Approach",
    "technical design": "Technical Approach",
    "design": "Technical Approach",
    # I/O Contract
    "i/o contract": "I/O Contract",
    "io contract": "I/O Contract",
    "input/output": "I/O Contract",
    "interface": "I/O Contract",
    "api": "I/O Contract",
    "api contract": "I/O Contract",
    "inputs and outputs": "I/O Contract",
    # Configuration Surface
    "configuration surface": "Configuration Surface",
    "configuration": "Configuration Surface",
    "config": "Configuration Surface",
    "parameters": "Configuration Surface",
    "settings": "Configuration Surface",
    "knobs": "Configuration Surface",
    # Behavioral Examples
    "behavioral examples": "Behavioral Examples",
    "examples": "Behavioral Examples",
    "scenarios": "Behavioral Examples",
    "use cases": "Behavioral Examples",
    "usage examples": "Behavioral Examples",
    # Out of Scope
    "out of scope": "Out of Scope",
    "non-goals": "Out of Scope",
    "non goals": "Out of Scope",
    "exclusions": "Out of Scope",
    "not in scope": "Out of Scope",
    "limitations": "Out of Scope",
}

CANONICAL_SECTIONS = [
    "Objective",
    "Design Decisions",
    "Technical Approach",
    "I/O Contract",
    "Configuration Surface",
    "Behavioral Examples",
    "Out of Scope",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class DesignDecision:
    """One <decision> block with name, rationale, and constraints."""

    name: str
    rationale: str
    constraint: str
    source_lines: tuple[int, int] = (0, 0)  # (start, end) in original doc


@dataclass
class Section:
    """A mapped section from the design document."""

    canonical_name: str
    original_heading: str
    content: str
    source_lines: tuple[int, int] = (0, 0)
    decisions: list[DesignDecision] = field(default_factory=list)


@dataclass
class UnmappedBlock:
    """Content that didn't map to any recognized section."""

    heading: str  # "" for preamble content
    content: str
    source_lines: tuple[int, int] = (0, 0)


@dataclass
class ParseResult:
    """Complete parse output."""

    title: str
    metadata: dict[str, str]  # date, status, author, etc.
    sections: list[Section]
    unmapped: list[UnmappedBlock]
    source_file: str
    total_lines: int


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_META_RE = re.compile(r"^\*\*(\w[\w\s]*)\*\*\s*:\s*(.+)$")
_DECISION_BLOCK_RE = re.compile(
    r'<decision\s+name="([^"]+)">\s*'
    r"<rationale>(.*?)</rationale>\s*"
    r"<constraint>(.*?)</constraint>\s*"
    r"</decision>",
    re.DOTALL,
)


def _normalize_heading(text: str) -> str:
    """Normalize heading text for alias lookup."""
    # Strip markdown formatting, numbering, trailing punctuation
    text = re.sub(r"^\d+\.\s*", "", text)  # "1. Foo" -> "Foo"
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # **Foo** -> Foo
    text = re.sub(r"\(.*?\)", "", text)  # "(bar)" removed
    text = re.sub(r"—.*$", "", text)  # "Foo — bar" -> "Foo"
    text = re.sub(r"\s*:$", "", text)  # trailing colon
    return text.strip().lower()


def _extract_decisions(content: str, block_start_line: int) -> list[DesignDecision]:
    """Extract <decision> XML blocks from section content."""
    decisions = []
    for m in _DECISION_BLOCK_RE.finditer(content):
        # Approximate line numbers from character offset
        line_offset = content[: m.start()].count("\n")
        end_offset = content[: m.end()].count("\n")
        decisions.append(
            DesignDecision(
                name=m.group(1).strip(),
                rationale=m.group(2).strip(),
                constraint=m.group(3).strip(),
                source_lines=(
                    block_start_line + line_offset,
                    block_start_line + end_offset,
                ),
            )
        )
    return decisions


def parse_design_doc(text: str, source_file: str = "<stdin>") -> ParseResult:
    """Parse a markdown design document into structured sections."""
    lines = text.split("\n")
    total_lines = len(lines)

    # --- Extract title (first H1) ---
    title = ""
    title_line = 0
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) == 1:
            title = m.group(2).strip()
            title_line = i
            break

    # --- Extract metadata (bold key-value pairs before first H2) ---
    metadata: dict[str, str] = {}
    meta_end = title_line + 1
    for i in range(title_line + 1, len(lines)):
        line = lines[i].strip()
        if not line or line == "---":
            meta_end = i
            continue
        m = _META_RE.match(line)
        if m:
            metadata[m.group(1).strip().lower()] = m.group(2).strip()
            meta_end = i + 1
        else:
            heading_m = _HEADING_RE.match(line)
            if heading_m and len(heading_m.group(1)) <= 2:
                break
            # Non-metadata, non-heading line — stop scanning
            break

    # --- Split into heading-delimited blocks ---
    blocks: list[tuple[str, int, int, str]] = []  # (heading, start, end, content)
    current_heading = ""
    current_start = meta_end
    current_lines: list[str] = []

    for i in range(meta_end, len(lines)):
        line = lines[i]
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) <= 3:
            # Save previous block
            if current_lines or current_heading:
                content = "\n".join(current_lines).strip()
                if content or current_heading:
                    blocks.append((current_heading, current_start, i - 1, content))
            current_heading = m.group(2).strip()
            current_start = i
            current_lines = []
        else:
            current_lines.append(line)

    # Final block
    if current_lines or current_heading:
        content = "\n".join(current_lines).strip()
        if content or current_heading:
            blocks.append((current_heading, current_start, len(lines) - 1, content))

    # --- Map blocks to canonical sections ---
    sections: list[Section] = []
    unmapped: list[UnmappedBlock] = []
    mapped_canonicals: set[str] = set()

    for heading, start, end, content in blocks:
        if not content and not heading:
            continue

        normalized = _normalize_heading(heading)
        canonical = SECTION_ALIASES.get(normalized)

        if canonical and canonical not in mapped_canonicals:
            decisions = []
            if canonical == "Design Decisions":
                decisions = _extract_decisions(content, start)

            sections.append(
                Section(
                    canonical_name=canonical,
                    original_heading=heading,
                    content=content,
                    source_lines=(start + 1, end + 1),  # 1-indexed
                    decisions=decisions,
                )
            )
            mapped_canonicals.add(canonical)
        elif heading:  # Has a heading but didn't match
            unmapped.append(
                UnmappedBlock(
                    heading=heading,
                    content=content,
                    source_lines=(start + 1, end + 1),
                )
            )

    return ParseResult(
        title=title,
        metadata=metadata,
        sections=sections,
        unmapped=unmapped,
        source_file=source_file,
        total_lines=total_lines,
    )


# ---------------------------------------------------------------------------
# Output: Structured Markdown
# ---------------------------------------------------------------------------
def _format_decisions(decisions: list[DesignDecision]) -> str:
    """Format decisions as structured markdown."""
    if not decisions:
        return ""

    parts = []
    for i, d in enumerate(decisions, 1):
        parts.append(f"### Decision {i}: {d.name}")
        parts.append("")
        parts.append(f"> **Rationale**: {d.rationale}")
        parts.append("")
        parts.append(f"> **Constraint**: {d.constraint}")
        parts.append("")
        parts.append(f"*Source: lines {d.source_lines[0]}-{d.source_lines[1]}*")
        parts.append("")
    return "\n".join(parts)


def render_structured_doc(result: ParseResult) -> str:
    """Render the parse result as structured markdown."""
    parts: list[str] = []

    # Header
    parts.append(f"# {result.title}")
    parts.append("")

    # Metadata table
    if result.metadata:
        parts.append("| Field | Value |")
        parts.append("|-------|-------|")
        for key, value in result.metadata.items():
            parts.append(f"| {key.title()} | {value} |")
        parts.append("")
        parts.append(f"*Converted from: `{result.source_file}`*")
        parts.append("")

    parts.append("---")
    parts.append("")

    # Canonical sections in standard order
    section_map = {s.canonical_name: s for s in result.sections}

    for canonical in CANONICAL_SECTIONS:
        section = section_map.get(canonical)

        parts.append(f"## {canonical}")
        parts.append("")

        if section is None:
            parts.append("*Section not found in source document.*")
            parts.append("")
        else:
            parts.append(
                f'*Original heading: "{section.original_heading}" '
                f"(lines {section.source_lines[0]}-{section.source_lines[1]})*"
            )
            parts.append("")

            if canonical == "Design Decisions" and section.decisions:
                parts.append(_format_decisions(section.decisions))
                # Also include raw content for anything outside <decision> tags
                stripped = _DECISION_BLOCK_RE.sub("", section.content).strip()
                if stripped:
                    parts.append("### Additional Notes")
                    parts.append("")
                    parts.append(stripped)
                    parts.append("")
            else:
                parts.append(section.content)
                parts.append("")

        parts.append("---")
        parts.append("")

    # Unmapped sections (preserved verbatim)
    if result.unmapped:
        parts.append("## Unmapped Content")
        parts.append("")
        parts.append(
            "*The following sections were not mapped to canonical categories. "
            "The strategist may incorporate them during planning.*"
        )
        parts.append("")
        for block in result.unmapped:
            parts.append(f"### {block.heading}")
            parts.append(f"*Lines {block.source_lines[0]}-{block.source_lines[1]}*")
            parts.append("")
            parts.append(block.content)
            parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Output: Coverage Report
# ---------------------------------------------------------------------------
def render_coverage_report(result: ParseResult) -> str:
    """Render a coverage report showing what was mapped and what wasn't."""
    parts: list[str] = []

    parts.append(f"# Coverage Report: {result.title}")
    parts.append("")
    parts.append(f"Source: `{result.source_file}` ({result.total_lines} lines)")
    parts.append("")

    # Section coverage
    parts.append("## Section Mapping")
    parts.append("")
    parts.append("| Canonical Section | Status | Original Heading | Lines |")
    parts.append("|-------------------|--------|------------------|-------|")

    section_map = {s.canonical_name: s for s in result.sections}
    found = 0
    for canonical in CANONICAL_SECTIONS:
        section = section_map.get(canonical)
        if section:
            found += 1
            parts.append(
                f"| {canonical} | MAPPED | "
                f"{section.original_heading} | "
                f"{section.source_lines[0]}-{section.source_lines[1]} |"
            )
        else:
            parts.append(f"| {canonical} | MISSING | — | — |")

    parts.append("")
    coverage_pct = (found / len(CANONICAL_SECTIONS)) * 100
    parts.append(
        f"**Coverage: {found}/{len(CANONICAL_SECTIONS)} sections "
        f"({coverage_pct:.0f}%)**"
    )
    parts.append("")

    # Design decisions
    parts.append("## Design Decisions")
    parts.append("")
    dd = section_map.get("Design Decisions")
    if dd and dd.decisions:
        parts.append(f"Found **{len(dd.decisions)}** tagged decisions:")
        parts.append("")
        for d in dd.decisions:
            has_rationale = "yes" if d.rationale else "NO"
            has_constraint = "yes" if d.constraint else "NO"
            parts.append(
                f"- **{d.name}** — "
                f"rationale: {has_rationale}, "
                f"constraint: {has_constraint} "
                f"(lines {d.source_lines[0]}-{d.source_lines[1]})"
            )
        parts.append("")
    else:
        parts.append("*No tagged `<decision>` blocks found.*")
        parts.append("")

    # Unmapped content
    parts.append("## Unmapped Content")
    parts.append("")
    if result.unmapped:
        total_unmapped_lines = sum(
            b.source_lines[1] - b.source_lines[0] + 1 for b in result.unmapped
        )
        parts.append(
            f"**{len(result.unmapped)} section(s)** not mapped "
            f"(~{total_unmapped_lines} lines):"
        )
        parts.append("")
        for block in result.unmapped:
            preview = block.content[:120].replace("\n", " ")
            if len(block.content) > 120:
                preview += "..."
            parts.append(
                f"- **{block.heading}** "
                f"(lines {block.source_lines[0]}-{block.source_lines[1]}): "
                f"{preview}"
            )
        parts.append("")
        parts.append(
            "*Review these sections — they may contain important context "
            "that should be manually categorized or addressed in planning.*"
        )
    else:
        parts.append("*All content mapped successfully.*")

    parts.append("")

    # Metadata
    parts.append("## Metadata Extracted")
    parts.append("")
    if result.metadata:
        for key, value in result.metadata.items():
            parts.append(f"- **{key.title()}**: {value}")
    else:
        parts.append("*No metadata fields found (date, status, author, etc.)*")

    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a design document to structured workflow format.",
        epilog="Semantic mapping is deferred to the strategist's sub-agents via PTC.",
    )
    parser.add_argument(
        "input",
        help="Path to the input design document (markdown)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output path for structured document (default: stdout)",
    )
    parser.add_argument(
        "--report",
        "-r",
        help="Output path for coverage report (optional)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output parse result as JSON (for programmatic use)",
    )

    args = parser.parse_args()

    # Read input
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        return 1

    text = input_path.read_text(encoding="utf-8")

    # Parse
    result = parse_design_doc(text, source_file=str(input_path))

    # JSON output mode
    if args.json:
        import json

        output = {
            "title": result.title,
            "metadata": result.metadata,
            "source_file": result.source_file,
            "total_lines": result.total_lines,
            "sections": [
                {
                    "canonical_name": s.canonical_name,
                    "original_heading": s.original_heading,
                    "source_lines": list(s.source_lines),
                    "content": s.content,
                    "decisions": [
                        {
                            "name": d.name,
                            "rationale": d.rationale,
                            "constraint": d.constraint,
                            "source_lines": list(d.source_lines),
                        }
                        for d in s.decisions
                    ],
                }
                for s in result.sections
            ],
            "unmapped": [
                {
                    "heading": u.heading,
                    "content": u.content,
                    "source_lines": list(u.source_lines),
                }
                for u in result.unmapped
            ],
        }
        json_text = json.dumps(output, indent=2, ensure_ascii=False)
        if args.output:
            Path(args.output).write_text(json_text, encoding="utf-8")
            print(f"JSON output written to: {args.output}", file=sys.stderr)
        else:
            print(json_text)
        return 0

    # Render structured document
    structured = render_structured_doc(result)
    if args.output:
        Path(args.output).write_text(structured, encoding="utf-8")
        print(f"Structured document written to: {args.output}", file=sys.stderr)
    else:
        print(structured)

    # Render coverage report
    if args.report:
        report = render_coverage_report(result)
        Path(args.report).write_text(report, encoding="utf-8")
        print(f"Coverage report written to: {args.report}", file=sys.stderr)

    # Summary to stderr
    mapped = len(result.sections)
    total = len(CANONICAL_SECTIONS)
    unmapped = len(result.unmapped)
    decisions = sum(len(s.decisions) for s in result.sections)
    print(
        f"Parsed: {mapped}/{total} sections mapped, "
        f"{decisions} decisions extracted, "
        f"{unmapped} unmapped block(s)",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())

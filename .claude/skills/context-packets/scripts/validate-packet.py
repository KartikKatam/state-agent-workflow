#!/usr/bin/env python3
"""
Validate context packets against their schemas.
Usage: python validate-packet.py <packet.json> [schema-dir]
"""

import json
import sys
from datetime import datetime


def validate_meta(packet: dict, packet_type: str) -> list[str]:
    """Validate the meta section."""
    errors = []
    meta = packet.get("meta", {})

    if not meta:
        return ["Missing 'meta' section"]

    required = ["type", "created", "schema_version"]
    for field in required:
        if field not in meta:
            errors.append(f"meta.{field} is required")

    if meta.get("type") != packet_type:
        errors.append(f"meta.type should be '{packet_type}', got '{meta.get('type')}'")

    # Validate timestamps
    for ts_field in ["created", "updated"]:
        if ts_field in meta:
            try:
                datetime.fromisoformat(meta[ts_field].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                errors.append(f"meta.{ts_field} is not valid ISO8601")

    return errors


def validate_no_prose(obj: any, path: str = "", max_len: int = 100) -> list[str]:
    """Check for overly verbose string values."""
    warnings = []

    if isinstance(obj, str):
        if len(obj) > max_len and path not in ["meta.project_root", "answer.summary"]:
            warnings.append(
                f"'{path}' is {len(obj)} chars (max {max_len}): consider compressing"
            )
    elif isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{path}.{key}" if path else key
            warnings.extend(validate_no_prose(value, new_path, max_len))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            warnings.extend(validate_no_prose(item, f"{path}[{i}]", max_len))

    return warnings


def validate_file_refs(obj: any, path: str = "") -> list[str]:
    """Check that file references use path:line format."""
    warnings = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{path}.{key}" if path else key
            # Fields that should be file:line format
            if key in ["file", "location", "detail_in", "example_in"]:
                if isinstance(value, str) and "/" in value and ":" not in value:
                    warnings.append(
                        f"'{new_path}' should include line numbers: {value}"
                    )
            warnings.extend(validate_file_refs(value, new_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            warnings.extend(validate_file_refs(item, f"{path}[{i}]"))

    return warnings


def infer_packet_type(packet: dict) -> str:
    """Infer packet type from content."""
    meta_type = packet.get("meta", {}).get("type")
    if meta_type:
        return meta_type

    if "query" in packet:
        return "query"
    if "touchpoints" in packet:
        return "feature"
    if "structure" in packet:
        return "codebase"

    return "unknown"


def validate_packet(filepath: str) -> tuple[list[str], list[str]]:
    """Validate a packet file. Returns (errors, warnings)."""
    errors = []
    warnings = []

    # Load the file
    try:
        with open(filepath) as f:
            packet = json.load(f)
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"], []
    except FileNotFoundError:
        return [f"File not found: {filepath}"], []

    # Infer type
    packet_type = infer_packet_type(packet)
    if packet_type == "unknown":
        errors.append("Could not determine packet type - add meta.type")
        return errors, warnings

    # Validate meta
    errors.extend(validate_meta(packet, packet_type))

    # Check for verbose strings
    warnings.extend(validate_no_prose(packet))

    # Check file references
    warnings.extend(validate_file_refs(packet))

    # Type-specific validation
    if packet_type == "codebase":
        if "structure" not in packet:
            errors.append("Codebase packet missing 'structure'")
        if "types" not in packet:
            errors.append("Codebase packet missing 'types'")
        if "patterns" not in packet:
            errors.append("Codebase packet missing 'patterns'")

    elif packet_type == "query":
        if "query" not in packet:
            errors.append("Query packet missing 'query'")
        if "answer" not in packet:
            errors.append("Query packet missing 'answer'")
        if "sources" not in packet:
            errors.append("Query packet missing 'sources'")
        # Check confidence
        confidence = packet.get("answer", {}).get("confidence")
        if confidence is not None and not (0 <= confidence <= 1):
            errors.append("answer.confidence must be between 0 and 1")

    elif packet_type == "feature":
        if "touchpoints" not in packet:
            errors.append("Feature packet missing 'touchpoints'")
        if "dependencies" not in packet:
            errors.append("Feature packet missing 'dependencies'")

    return errors, warnings


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate-packet.py <packet.json>")
        sys.exit(1)

    filepath = sys.argv[1]
    errors, warnings = validate_packet(filepath)

    if errors:
        print(f"❌ ERRORS in {filepath}:")
        for e in errors:
            print(f"  - {e}")

    if warnings:
        print(f"⚠️  WARNINGS in {filepath}:")
        for w in warnings:
            print(f"  - {w}")

    if not errors and not warnings:
        print(f"✓ {filepath} is valid")
        sys.exit(0)
    elif errors:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

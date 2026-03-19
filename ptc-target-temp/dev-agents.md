# LPR-Module – Dev Agent Rules

These rules apply to all human and AI dev agents working on the LPR-Module.

**Goals:** fast iteration, low bug rate, clear responsibilities, consistent naming.

## 1. Core Principles

1. **Clarity over cleverness**
   - Prefer simple, boring solutions.
   - If the intent of code is not obvious on a quick read, it's too complex.

2. **Small, composable functions**
   - One responsibility per function.
   - Compose many small helpers instead of building "god functions".

3. **Separation of concerns**
   - Three main blocks:
     - `producer/` – detection, tracking, ROI cropping, cheap quality scoring, ID buffer.
     - `consumer/` – rich analysis, batching, recipe generation, GPU preprocessing, OCR queue.
     - `ocr/` – OCR engine wrapper, result evaluation, ID state controller.
   - Shared utilities live in `common/`.

4. **No magic numbers**
   - All thresholds and tunables live in **config files**, not hard-coded.

## 2. Directory & Module Conventions

Each block (`producer`, `consumer`, `ocr`) should roughly follow:

- `__init__.py` – module exports and public API definitions.
- `config.py` – config dataclasses and loader.
- `models.py` – data types / DTOs for that block.
- `pipeline.py` – high-level orchestration of that block.
- `ops_*.py` – small focused operations (`ops_detection`, `ops_tracking`, etc.).
- `queues.py` / `buffer.py` – where applicable.

**Rules:**
- `pipeline.py` orchestrates; `ops_*` implement logic.
- `ops_*` must not import `pipeline.py`.
- **`__init__.py` must be kept in sync** with module changes:
  - When adding new classes, functions, or types to a module, update `__init__.py` to export them.
  - Include all public API elements in the `__all__` list for proper linting and IDE support.
  - Remove exports when classes/functions are deleted or renamed.

### 2.1 Maintaining `__init__.py` Files

Each module's `__init__.py` file defines the public API and enables proper imports for linting tools (Pylance, mypy, ruff, etc.).

**Structure:**
```python
"""Module docstring describing purpose."""

# Imports grouped by category
from .config import ConfigClass, load_config
from .models import ModelA, ModelB, TypeAlias
from .ops_feature import feature_function, FeatureClass

__all__ = [
    # Config
    "ConfigClass",
    "load_config",
    # Models
    "ModelA",
    "ModelB",
    "TypeAlias",
    # Operations
    "feature_function",
    "FeatureClass",
]
```

**When to update:**
- **Added a new class/function**: Add import and append to `__all__`.
- **Renamed a class/function**: Update the import and `__all__` entry.
- **Deleted a class/function**: Remove from imports and `__all__`.
- **Changed module structure**: Reorganize imports to match (e.g., moved from `ops_a.py` to `ops_b.py`).

**Best practices:**
- Group imports by category with comments (Config, Models, Operations, etc.).
- Keep `__all__` in the same order as imports for readability.
- Only export items that are part of the public API (no internal helpers).
- Use explicit imports (`from .module import Class`), not star imports.

## 3. Coding Style

Assume Python; apply analogues in other languages.

- Use **type hints** everywhere.
- Use explicit imports; no `from x import *`.
- Aim for functions **< 30–40 lines**.
- Prefer **pure functions**; if a function mutates state, its name must make that clear (e.g. `update_id_state`).
- Use **early returns** to avoid deep nesting.
- Avoid global mutable state.

Example of preferred style:

```python
def process_frame(frame: Frame, cfg: ProducerConfig) -> list[RoiImage]:
    if frame.index % cfg.frame_sample_rate != 0:
        return []

    rois = detect_plate_rois(frame, cfg)
    scored = [score_roi(roi, cfg) for roi in rois]
    return [r for r in scored if r.quality_score >= cfg.min_quality]
```

## 4. Configuration Rules

All tunables go through config objects.

- `producer/config.py` – sampling cadence, detection thresholds, tracking params, quality thresholds, buffer sizes.
- `consumer/config.py` – batch sizes, analysis thresholds, GPU preprocess options, queue sizes.
- `ocr/config.py` – OCR engine settings, confidence thresholds, retry limits.

**Pattern:**

```python
from dataclasses import dataclass

@dataclass
class ProducerConfig:
    frame_sample_rate: int = 15
    max_ids_in_buffer: int = 32
    blur_threshold: float = 0.25
    min_plate_brightness: float = 0.2

def load_producer_config() -> ProducerConfig:
    # Central place to load overrides (env/YAML/CLI) if needed.
    return ProducerConfig()
```

**Rules:**

- Do not read env vars or CLI flags deep inside logic code.
- When a new heuristic introduces a number, add it to the relevant `*Config` and pass the config down.
- Experiments should normally change config only, not logic.

## 5. Data Models & Contracts

Shared data types must be defined once and reused.

- Cross-block types → `common/models.py`.
- Block-local types → `<block>/models.py`.

Examples (illustrative):

```python
# common/models.py
from dataclasses import dataclass
import numpy as np

TrackId = str

@dataclass
class RoiImage:
    id: TrackId
    image: np.ndarray
    frame_index: int
    quality_score: float

@dataclass
class OcrResult:
    id: TrackId
    text: str
    confidence: float
```

**Rules:**

- If a concept already has a type, reuse it instead of inventing a synonym.
- Don't create near-duplicates like `PlateId` vs `TrackId`, `Roi` vs `PlateCrop` unless there is a real semantic difference and it is documented.

## 6. Canonical Naming & Name Bank

To keep naming consistent across agents and sessions we use a **name bank**.

- File: `docs/name-bank.md` (and optionally a machine-readable `name-bank.json`).
- Each entry: name, kind (type/class/module/function), file, short description, optional example.

**Agents must:**

1. **Search for existing names** before inventing new ones:
   - This file (`dev-agents.md`)
   - `docs/name-bank.md`
   - `common/models.py` and block `models.py`.

2. **Reuse existing names** if the concept already exists.

3. When a new concept is truly needed:
   - Follow existing patterns (`*Id`, `*Image`, `*Result`, etc.).
   - Add an entry to `docs/name-bank.md` in the same change.

4. If renaming:
   - Update all usages.
   - Update the name bank entry instead of adding a second one.

## 7. Error Handling & Logging

- Catch exceptions only where you add value (extra context, retries, fallback).
- Log high-level events and failures, not internal noise.
- Include IDs and sizes in log messages when useful (e.g. batch size, `TrackId`).

Example:

```python
try:
    ocr_result = run_ocr_batch(batch, cfg)
except OcrEngineError as exc:
    logger.error("OCR batch failed", batch_size=len(batch), error=str(exc))
    raise
```

## 8. Testing

- Non-trivial logic (tracking, scoring, batching, OCR result evaluation) must have unit tests.
- Include edge cases: empty inputs, bad config, low/high thresholds.
- When you change behavior, update or add tests to lock in the new behavior.

## 9. Agent Workflow Checklist

Before writing or modifying code, an agent should:

1. Identify the block: **Producer**, **Consumer**, or **OCR**.
2. Read that block's `config.py` and `models.py`.
3. Check `docs/name-bank.md` for existing names and types.
4. Implement features as **small, composable functions** in `ops_*` or small additions to `pipeline.py`.
5. Put all new tunables into the relevant config dataclass.
6. **Update `__init__.py`** when adding, removing, or renaming public classes/functions:
   - Add new exports to the imports and `__all__` list.
   - Remove or update exports for deleted/renamed items.
   - Ensure the module remains importable and properly typed for linting tools.
7. Add/update tests for new behavior.
8. If new concepts or names were introduced, update `docs/name-bank.md`.

If these steps are followed, code should remain modular, consistent, and easy to extend.

## 10. Context Loading Protocol
When starting a task, reference the necessary context.
- **Architecture:** `architecture.md` (If doing high-level design)
- **Logic:** `Plan_02.txt` (If implementing algorithms)
- **Definitions:** `common/models.py` and `tech-stack.md`
- **Naming:** `docs/name-bank.md`
If you are missing definitions for a type, ASK before inventing one.

# LPR-SingleDrone Project

High-performance License Plate Recognition module for real-time drone video processing.

**Docs**: [Architecture](architecture.md) | [Plan_02.txt](Plan_02.txt) | [Dev Guide](dev-agents.md) | [Name Bank](name-bank.md)

---

## Current Status

### Module Progress

| Module | Status | Completion | Components |
|--------|--------|------------|------------|
| **Producer** | ✅ Complete | 100% | Detection, tracking, cropping, quality, buffer, pipeline |
| **Consumer** | ⚠️ In Progress | ~15% | Rich quality analysis ✅, scheduler/batch/recipe/GPU ❌ |
| **OCR** | ❌ Not Started | 0% | TrOCR engine, result evaluator, state machine |

### Current & Next Tasks

- **Current Task**: Batch selector (`consumer/ops_batch_select.py`) - Diversity-enforced ROI selection
- **Next Task**: Recipe generator (`consumer/ops_recipe.py`) - YAML-driven preprocessing parameter tuning

### Remaining Consumer Work
1. ❌ Batch selector implementation
2. ❌ Recipe generator YAML integration
3. ❌ GPU preprocessor kernel design & implementation
4. ❌ Pull-based bin scheduler from Producer

---

## Development History

### Initial Implementation (Dec 15, 2025)
**Commit**: `7153d5d` - "Single Drone LPR version"

Complete Producer module implementation (3,600+ LOC):
- YOLO detection with TensorRT FP16 optimization
- IOU tracking + α-β Kalman filter + Lucas-Kanade motion compensation
- Adaptive cropping with padding and resize
- Fast quality analysis (Tenengrad sharpness, brightness, contrast, gradient histograms)
- Per-track buffer management with diversity enforcement
- Pipeline orchestrator with async logging

**Stats**: 16 files created, ~3,600 LOC in producer/

---

### Pipeline Monitoring (Dec 18, 2025)
**Commit**: `d16b1f9` - "Enhance pipeline performance monitoring and quality gating"

- Added sliding window percentile tracking (p50/p95/p99) for all pipeline stages
- Implemented quality gating with band edge check before buffer insertion
- Added buffer eviction for lost tracks (memory management)
- Fixed tracking velocity validation and LK drift prevention
- Fixed GPU weighted histogram computation

**Stats**: 6 files modified, +356 LOC

---

### Consumer Scaffolding (Dec 23, 2025)
**Commit**: `2669199` - "Implement consumer preprocessing pipeline and optimize buffer performance"

- Created consumer module structure (scheduler, batch select, recipe, GPU preprocess stubs)
- Added OCR queue implementation (bounded FIFO with back-pressure)
- Wrote consumer config and models (RichQualityMetrics, BatchSelection, RecipeBatch, OcrQueueItem)
- Optimized buffer snapshots (shallow copy + read-only array protection)
- Created 206-line preprocessing guide document

**Stats**: 16 files modified, +859 LOC

---

### Rich Quality Analysis (Dec 27, 2025)
**Commit**: `8e27244` - "Implement comprehensive quality analysis system for LPR pipeline"

- Implemented canonical crop preprocessing (letterboxing, LAB color space)
- Added geometric validation (keypoint ordering, quad sanity checks, homography eligibility)
- Created photometric metrics (exposure, contrast, Tenengrad sharpness, noise estimation)
- Designed composite eligibility flags (`top8_eligible`, `enhance_eligible`, `homography_eligible`)
- Generated pose signature vectors (12D: quad coords + skew + perspective + plate height)
- Enhanced producer metadata for detection, tracking, and cropping

**Stats**: 8 files modified, +1,154 LOC

---

## Code Statistics

| Component | Files | Lines of Code |
|-----------|-------|---------------|
| Producer | 9 | ~3,933 |
| Consumer | 9 | ~1,425 |
| OCR | 0 | 0 |
| Docs | 8 | ~2,000 |
| **Total** | **26** | **~7,358** |

---

## References

[Architecture](architecture.md) | [Plan_02.txt](Plan_02.txt) | [Dev Guide](dev-agents.md) | [Preprocessing Guide](CONSUMER_PREPROCESSING_GUIDE.md) | [Tasks](tasks.md) | [Issues](issues.md)

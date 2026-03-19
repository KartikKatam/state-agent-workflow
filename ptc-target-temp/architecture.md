# Architecture Guide

High-level architectural overview of the LPR-SingleDrone module within the Firefly ecosystem.

**Ground Truth**: [Plan_02.txt](Plan_02.txt) contains detailed technical specifications.

## 1. Firefly Ecosystem Integration

### Monorepo Structure
- **`central/`**: Main backend + web dashboard (TypeScript/PostgreSQL)
- **`edge/`**: Edge device services
- **`gpu/`**: ML/GPU services including LPR-SingleDrone (Python/FastAPI)
- **`shared/`**: Common TypeScript utilities
- **Infrastructure**: Podman containers (Postgres, RabbitMQ, NATS, SRS video server)

### LPR Data Flow
```
SRS Video Server → LPR-SingleDrone (gpu/) → FastAPI → central/edge → PostgreSQL + NATS/RabbitMQ → Frontend
```

---

## 2. LPR-SingleDrone Architecture

### Three-Stage Pipeline

**Producer** → **Consumer** → **OCR**

Each stage operates independently via versioned pull-based handoff interfaces.

### System Diagram

```
Video Frames
     │
     ▼
┌─────────────── PRODUCER ──────────────┐
│ Detection → Tracking → Cropping       │
│          → Fast Quality → Buffer      │  Per-track versioned buffers
└───────────────┬───────────────────────┘
                │ Pull-based (version tracking)
                ▼
┌─────────────── CONSUMER ──────────────┐
│ Scheduler → Rich Analysis             │
│          → Batch Select → Recipe      │  Select & prepare optimal batches
│          → GPU Preprocess → Queue     │
└───────────────┬───────────────────────┘
                │ FIFO queue
                ▼
┌─────────────── OCR ───────────────────┐
│ TrOCR → Result Eval → State Machine   │  Aggregate & manage ID states
└───────────────────────────────────────┘
```

### Module Status

| Module | Status | Location | Key Responsibility |
|--------|--------|----------|-------------------|
| Producer | ✅ Complete | `producer/` | Frame processing → quality-gated buffers |
| Consumer | ⚠️ Partial | `consumer/` | Rich analysis → preprocessed batches |
| OCR | ❌ Not Started | `ocr/` | Text recognition → state management |
| Common | ❌ Not Started | `common/` | Shared utilities |

---

## 3. Producer Module

**Status**: ✅ Complete | **Location**: `producer/`

### Responsibilities
- Detect license plates in video frames (YOLOv11m-Pose + TensorRT FP16)
- Track plates across frames (IOU matching + α-β Kalman + Lucas-Kanade)
- Crop ROIs with adaptive padding and resize
- Fast quality scoring (Tenengrad sharpness, brightness, contrast, gradient histograms)
- Maintain per-track buffers with diversity enforcement and quality gating
- Version tracking for Consumer synchronization

### Component Files
- `ops_detection.py` - YOLO detector with TensorRT optimization
- `ops_tracking.py` - Multi-object tracking with motion compensation
- `ops_cropping.py` - ROI extraction with boundary handling
- `ops_quality_fast.py` - GPU-accelerated quality metrics
- `buffer.py` - Per-ID buffer manager with similarity-based diversity
- `pipeline.py` - End-to-end orchestrator with async logging
- `config.py` - 50+ tunable parameters
- `models.py` - Data structures (Detection, Track, RoiImage, etc.)

### Key Design Patterns
- **Quality Gating**: Hard threshold prevents low-quality images from reaching Consumer
- **Bootstrap Relaxation**: Lower threshold for first K frames per ID
- **Diversity Enforcement**: SSIM + gradient histogram similarity → replacement vs append logic
- **Versioning**: Buffer version increments on any change (signals Consumer to pull)
- **Performance Target**: <50ms per frame (15 FPS sampling)

---

## 4. Consumer Module

**Status**: ⚠️ Partial (Rich analysis complete, GPU preprocessing stub) | **Location**: `consumer/`

### Responsibilities
- Pull updated buffers from Producer (versioned handoff)
- Rich quality analysis (30+ geometric/photometric metrics + eligibility flags)
- Select optimal batches (up to 8 ROIs with diversity enforcement)
- Generate preprocessing recipes (YAML-driven tuning)
- GPU batch preprocessing (geometric + photometric transformations)
- Queue preprocessed batches for OCR

### Component Files
- `ops_scheduler.py` - Priority-based snapshot fetching (⚠️ stub)
- `ops_quality_rich.py` - Comprehensive quality analysis (✅ complete)
- `ops_batch_select.py` - Diversity-enforced batch selection (⚠️ stub)
- `ops_recipe.py` - Recipe generation from quality metrics (⚠️ stub)
- `ops_preprocess_gpu.py` - GPU preprocessing kernel (❌ not started)
- `queue.py` - Bounded FIFO OCR queue (✅ complete)
- `config.py` - 20+ tunable parameters
- `models.py` - RichQualityMetrics, BatchSelection, RecipeBatch, etc.

### Key Design Patterns
- **Pull-based Handoff**: Consumer controls pace via `get_updated_bins(last_versions)`
- **Rich Quality Analysis**: 30+ metrics (luminance, contrast, sharpness, noise, pose geometry)
- **Eligibility Flags**: `top8_eligible`, `enhance_eligible`, `homography_eligible`
- **Nested Batch Strategy**: 8 base candidates + 4 enhanced duplicates = 12 total to OCR
- **YAML-Driven Recipes**: Preprocessing parameters externalized from code
- **Performance Target**: <200ms per ID

---

## 5. OCR Module

**Status**: ❌ Not Started | **Location**: `ocr/`

### Planned Responsibilities
- Run TrOCR inference on preprocessed batches (TensorRT FP16 + Flash Attention)
- Aggregate results across batch (deduplication for enhanced duplicates)
- Manage ID lifecycle state machine (tracking → pending → confident/needs_retry → complete)

### Planned Component Files
- `ops_trocr.py` - TrOCR engine wrapper
- `ops_result_eval.py` - Batch aggregation and confidence scoring
- `ops_state_machine.py` - ID state controller
- `config.py` - Confidence thresholds, retry limits
- `models.py` - OcrResult, IdState, etc.

### ID State Machine
```
tracking → pending_ocr → {confident, needs_retry, complete}
                ↓              ↓
            needs_retry ───────┘
                ↓
            confident → complete (timeout or better result)
```

**States**: `tracking` | `pending_ocr` | `needs_retry` | `confident` | `complete`

---

## 6. Inter-Module Communication

### Producer → Consumer Handoff

**Pattern**: Pull-based with version tracking

```python
# Consumer pulls updated bins
snapshots = producer.get_updated_bins(last_versions)  # Returns BinSnapshot list

# Each snapshot contains:
# - track_id, version, candidates (deep copy), stats
```

**Benefits**: Async decoupling, Consumer controls pace, avoids redundant reprocessing

### Consumer → OCR Handoff

**Pattern**: Bounded FIFO queue

```python
# Consumer pushes preprocessed batches
ocr_queue.push(OcrQueueItem)  # Non-blocking with back-pressure

# OCR pulls batches
batch = ocr_queue.pop()  # FIFO order
```

---

## 7. Configuration & Data Models

### Configuration
All tunables externalized into config dataclasses (no magic numbers in code).

- **Producer**: 50+ parameters in `producer/config.py`
- **Consumer**: 20+ parameters in `consumer/config.py`
- **Design**: YAML-driven tuning, documented defaults, centralized loading

### Key Data Structures

**Producer Models** (`producer/models.py`):
- `Detection`, `Track`, `RoiImage`, `RoiFastQuality`
- `IdBin`, `BinSnapshot`, `PipelineStats`

**Consumer Models** (`consumer/models.py`):
- `RichQualityMetrics`, `RoiRichQuality`
- `BatchSelection`, `RecipeBatch`, `OcrQueueItem`

**Type Safety**: Full type hints, dataclasses, no stringly-typed dicts

---

## 8. Performance & Monitoring

### Targets
- **Producer**: <50ms per frame (15 FPS sampling)
- **Consumer**: <200ms per ID
- **OCR**: TBD

### Monitoring Metrics
- **Producer**: Per-stage timing (p50/p95/p99), quality gate rates, buffer utilization
- **Consumer**: Eligibility flag distribution, batch sizes, queue back-pressure
- **OCR**: Inference latency, confidence distribution, state transitions

---

## 9. References

- **[Plan_02.txt](Plan_02.txt)**: Detailed technical specifications (algorithms, thresholds, preprocessing pipeline)
- **[dev-agents.md](dev-agents.md)**: Developer guidelines and coding conventions
- **[name-bank.md](name-bank.md)**: Canonical type definitions and naming
- **[CONSUMER_PREPROCESSING_GUIDE.md](CONSUMER_PREPROCESSING_GUIDE.md)**: GPU preprocessing kernel design

### Key File Paths
- [producer/pipeline.py](producer/pipeline.py) - Producer orchestrator
- [consumer/ops_quality_rich.py](consumer/ops_quality_rich.py) - Rich quality analyzer
- [producer/buffer.py](producer/buffer.py) - ID buffer manager

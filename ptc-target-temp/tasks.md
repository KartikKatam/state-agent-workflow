# LPR Single Drone Tasks

## TensorRT FP16 Optimization

### Build TensorRT Engine from Trained Model

**What**: Convert trained YOLO .pt model to optimized TensorRT .engine with FP16 precision for 3x faster inference.

**Why**:
- Post-training quantization (no changes to training pipeline)
- 3x inference speedup (PyTorch FP32: ~15ms � TensorRT FP16: ~5ms per frame)
- 2x memory reduction
- Hardware optimizations: layer fusion, kernel auto-tuning, memory pooling

**How**:
1. After training completes and you have `best.pt` model
2. Run the build script: `python scripts/build_tensorrt_engine.py --model models/best.pt --imgsz 960 544`
3. This creates `best.engine` (one-time process, takes 2-5 minutes)
4. Commit both `best.pt` and `best.engine` to version control
5. Production code loads `best.engine` directly (instant startup)

**Requirements**:
- CUDA-enabled GPU
- TensorRT installed (comes with PyTorch CUDA builds)
- Training must be complete (operates on trained weights only)

**Note**: Training stays in FP32 (full precision). Only inference uses FP16 (post-training quantization).

---

## Completed Tasks
- [x] Implement `producer/ops_detection.py` with TensorRT FP16 support
- [x] Implement `producer/ops_tracking.py` with IOU + alpha-beta filter + LK motion compensation
- [x] Add minimum size checks (100×40px) to tracking/detection pipeline
- [x] Implement `producer/ops_cropping.py` with adaptive padding and resize
- [x] Implement `producer/ops_quality_fast.py` for GPU-accelerated quality scoring with gradient histogram feature vectors
- [x] Add `FastQualityMetrics` and `RoiFastQuality` dataclasses to `producer/models.py`
- [x] Add fast quality configuration to `ProducerConfig` in `producer/config.py`
- [x] Implement `producer/buffer.py` for ID-based ROI buffering with similarity-based diversity and batch versioning
- [x] Add `IdBin`, `BinSnapshot`, and `BufferStats` dataclasses to `producer/models.py`
- [x] Add buffer configuration parameters to `ProducerConfig` in `producer/config.py`

## Current Tasks
- [ ] Implement `producer/pipeline.py` to orchestrate detection → tracking → cropping → quality → buffer
- [ ] Create `scripts/build_tensorrt_engine.py` for building TensorRT engines
- [ ] Build initial TensorRT engine from trained YOLO model
- [ ] Test and validate full producer pipeline end-to-end

## Upcoming Tasks

### Add Comprehensive Logging to Producer Pipeline

**What**: Implement structured JSON logging across all producer modules (detection, tracking, cropping, quality analysis) to enable ML-based parameter optimization.

**Why**:
- Enable data-driven threshold tuning using classical ML (linear regression, gradient boosting, etc.)
- Correlate pipeline decisions with downstream OCR success rates
- Identify performance bottlenecks and optimize resource allocation
- Track quality/diversity tradeoffs in buffer management
- Build datasets for model training and validation

**Modules to Instrument**:

1. **Detection** (`producer/ops_detection.py`):
   - Per-frame detection counts and confidence distributions
   - TensorRT inference timings (prepare, inference, postprocess)
   - Detection bbox statistics (size, aspect ratio, position)
   - Performance warnings when inference exceeds budget

2. **Tracking** (`producer/ops_tracking.py`):
   - Per-frame track lifecycle events (new, confirmed, lost)
   - IOU matching statistics (matches, unmatched tracks/detections)
   - Camera motion estimation (LK optical flow dx/dy)
   - Track state evolution (age, hits, confidence over time)
   - Timing breakdown (LK motion, prediction, matching, update, cleanup)

3. **Cropping** (`producer/ops_cropping.py`):
   - Per-ROI crop success/failure with reasons
   - Adaptive resize statistics (frequency, scale factors)
   - Crop dimension distributions before/after resize
   - Padding effectiveness metrics

4. **Fast Quality Analysis** (`producer/ops_quality_fast.py`):
   - Per-ROI quality metrics (focus, brightness, contrast, exposure)
   - Quality score distributions and pass/fail rates
   - GPU batch processing performance (time per batch, batch sizes)
   - Metric correlations with downstream OCR success

5. **Pipeline Orchestration** (`producer/pipeline.py`):
   - End-to-end frame processing timing (total + per-stage breakdown)
   - Frame-level summaries (detections → tracks → crops → quality passes → buffer inserts)
   - Performance degradation warnings when frame processing exceeds budget
   - Configuration snapshot at pipeline initialization

**Log Format**: Structured JSON with consistent schema:
```json
{
  "timestamp": "ISO-8601",
  "level": "INFO|DEBUG|WARNING",
  "logger": "producer.<module>",
  "event": "event_name",
  "run_id": "uuid",
  "frame_idx": 1234,
  "data": { /* event-specific fields */ }
}
```

**ML Optimization Use Cases**:
- **Features**: Detection confidence, bbox size, tracking age/hits, quality metrics, camera motion
- **Labels**: OCR success (binary), OCR confidence (continuous), buffer retention time
- **Models**:
  - Linear/logistic regression for threshold optimization
  - Gradient boosting for multi-variate parameter tuning
  - Survival analysis for track lifetime prediction
  - Clustering for identifying quality/diversity patterns

**Implementation**:
- Use Python `logging` with custom JSON formatter
- Write logs to `logs/lpr_singledrone_YYYYMMDD_HHMMSS.jsonl`
- Keep logging overhead < 0.5ms per frame (asynchronous writes if needed)
- Include `run_id` in all events for cross-module correlation

**Dependencies**: Must complete buffer implementation first (buffer logging is highest priority)

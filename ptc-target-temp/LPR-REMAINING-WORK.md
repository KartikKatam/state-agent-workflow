# LPR-SingleDrone: Complete Remaining Work

**Generated**: 2026-02-16
**Current state**: Producer 100%, Consumer 70%, OCR 0%, Integration 0%
**Branch**: K-LPR_2

---

## Pipeline Status

```
Video Frame (EXTERNAL — gpu/ service)
  |
  v
PRODUCER ========================== 100% COMPLETE
  Detection (YOLO TRT FP16)
  Tracking (IOU + alpha-beta + Lucas-Kanade)
  Cropping (adaptive padding)
  Corner CNN (Stage 3.5, TRT FP16)      <-- JUST COMPLETED
  Fast Quality (Tenengrad, brightness, contrast)
  Buffer (per-track, versioned, diversity-enforced)
  |
  | Pull-based (version tracking)
  v
CONSUMER ========================== 70% COMPLETE
  Scheduler (pull priority snapshots)    -- exists, needs integration test
  Rich Quality (30+ metrics)             -- complete
  Batch Selection (8 base + 4 enhanced)  -- complete
  Recipe Generator (12-D tensor)         -- complete
  GPU Preprocessor (homography + photo)  -- complete
  OCR Ready Queue (commit gate, priority)-- complete
  Consumer Orchestrator                  -- MISSING
  |
  | pull_next() / finish_commit()
  v
OCR =============================== 0% COMPLETE
  PARSeq TRT Engine                      -- MISSING
  OCR Worker Loop                        -- MISSING
  Per-Character Voting Aggregation       -- MISSING
  ID State Machine                       -- MISSING
  mark_done() caller                     -- MISSING (API exists, no caller)
  |
  v
OUTPUT (EXTERNAL — gpu/ service)
  FastAPI wrapper                        -- MISSING (external repo)
  NATS/RabbitMQ publishing               -- MISSING (external repo)
  PostgreSQL persistence                 -- MISSING (external repo)
  Frontend dashboard                     -- MISSING (central/ repo)
```

---

## P0: CRITICAL — Blocks Any Output

### 1. OCR Module — PARSeq Inference Engine

**File**: `ocr/ops_parseq.py` (CREATE)
**Est. LOC**: ~300
**Depends on**: Trained PARSeq TRT engine file

- [ ] `ParseqPredictor` class following TRT loading pattern (see `producer/ops_corner_cnn.py:76-108`)
- [ ] `_load_model()` — load TRT engine, create execution context
- [ ] `_warmup()` — dummy input inference, opportunistic failure handling
- [ ] `predict_batch(batch_tensor)` — run inference on (N, 3, 32, 128) float32 [-1,1] input
- [ ] Logit decoding — TRT output → character probabilities → text strings
- [ ] Per-character confidence extraction from softmax outputs
- [ ] Batch handling — process N images, return N results
- [ ] Graceful degradation — on TRT failure, log WARNING, return empty results

### 2. OCR Module — Result Aggregation

**File**: `ocr/ops_result_eval.py` (CREATE)
**Est. LOC**: ~400
**Depends on**: PARSeq inference (#1), design doc (user is writing)

- [ ] `aggregate_batch_results()` — combine 8-12 OCR readings per track
- [ ] Enhanced duplicate deduplication — for each enhanced ROI, pick best between base/enhanced version
- [ ] Per-character voting — majority vote across batch images, weighted by per-char confidence
- [ ] Final plate text assembly — highest-confidence character sequence
- [ ] Overall confidence score — aggregated from per-character votes and agreement ratio
- [ ] Handle partial reads — some images may produce shorter/longer strings
- [ ] Handle empty results — all images failed OCR → return low-confidence empty result

### 3. OCR Module — ID State Machine

**File**: `ocr/ops_state_machine.py` (CREATE)
**Est. LOC**: ~500
**Depends on**: Result aggregation (#2)

States: `tracking` → `pending_ocr` → `confident` / `needs_retry` → `complete`

- [ ] `IdStateMachine` class managing per-track state transitions
- [ ] `tracking → pending_ocr` transition — triggered when first OCR batch committed
- [ ] `pending_ocr → confident` transition — OCR result exceeds confidence threshold
- [ ] `pending_ocr → needs_retry` transition — OCR result below threshold, retry allowed
- [ ] `needs_retry → pending_ocr` transition — new batch committed for retry
- [ ] `confident → complete` transition — timeout expires OR better result received
- [ ] `* → complete` transition — track lost by producer (forced completion)
- [ ] Call `OcrReadyQueue.mark_done(track_id)` on `complete` transition
- [ ] Configurable thresholds: confidence_for_confident, confidence_for_complete, retry_timeout_ms
- [ ] State persistence — survive consumer restart (optional, can defer)
- [ ] Metrics: state transition counts, avg time-to-complete, retry rates

### 4. OCR Module — Worker Loop

**File**: `ocr/worker.py` (CREATE)
**Est. LOC**: ~300
**Depends on**: PARSeq engine (#1), aggregation (#2), state machine (#3)

- [ ] Pull-based worker: `OcrReadyQueue.pull_next(worker_id, timestamp_ms)` → `OcrWorkItem`
- [ ] Run PARSeq inference on `work_item.batch_tensor`
- [ ] Aggregate results using `aggregate_batch_results()`
- [ ] Update state machine with aggregated result
- [ ] Call `OcrReadyQueue.finish_commit(track_id, commit_version)` after processing
- [ ] Emit result to output channel (callback/queue for FastAPI layer)
- [ ] Idle polling with configurable sleep interval when queue empty
- [ ] Graceful shutdown support (drain in-flight work, stop pulling)

### 5. OCR Module — Config and Models

**File**: `ocr/config.py` (CREATE), `ocr/models.py` (CREATE)
**Est. LOC**: ~200

- [ ] `OcrConfig` dataclass: model_path, confidence_threshold, retry_limit, timeout_ms, worker_count, beam_search_params
- [ ] `OcrResult` dataclass: text, per_char_confidences, overall_confidence, metadata
- [ ] `AggregatedResult` dataclass: best_text, confidence, agreement_ratio, num_readings
- [ ] `IdState` enum or dataclass: current state, transition history, timestamps
- [ ] Update `ocr/__init__.py` exports

### 6. Consumer Orchestrator

**File**: `consumer/pipeline.py` (CREATE)
**Est. LOC**: ~400
**Depends on**: All existing consumer modules (complete)

The consumer modules exist but are disconnected — no orchestrator ties them together.

- [ ] `ConsumerPipeline` class with main processing loop
- [ ] Pull snapshots from producer: `producer.get_updated_bins(last_seen_versions)`
- [ ] For each snapshot: rich quality → batch selection → recipe → GPU preprocess → OCR queue commit
- [ ] Version tracking: maintain `last_seen_versions` dict per track_id
- [ ] Priority scheduling: process highest-priority tracks first (via `ops_scheduler.py`)
- [ ] Back-pressure handling: pause pulling when OCR queue is full
- [ ] Error isolation: single track failure doesn't crash entire consumer
- [ ] Timing tracking: per-stage latency (rich_quality_ms, batch_select_ms, recipe_ms, preprocess_ms)
- [ ] Periodic stats logging (similar to producer's `_maybe_log_pipeline_stats`)

### 7. PARSeq TRT Engine File

**Not a code task — model training/export**
**Depends on**: MODEL-TRAINING-PLAN.md Phase 3

- [ ] Obtain PARSeq pretrained weights (base or fine-tuned)
- [ ] Export to ONNX: `(1, 3, 32, 128)` input → text logits output
- [ ] Build TRT FP16 engine: `trtexec --onnx=parseq.onnx --saveEngine=parseq.engine --fp16`
- [ ] Verify engine loads and produces reasonable output on test images
- [ ] Place at configured path (default: `models/parseq.engine`)

---

## P1: HIGH — Degrades Quality or Prevents Validation

### 8. Producer Test Suite

**Files**: `tests/test_detection.py`, `tests/test_tracking.py`, `tests/test_cropping.py`, `tests/test_quality_fast.py`, `tests/test_buffer.py`, `tests/test_pipeline.py` (all CREATE)
**Est. LOC**: ~1,500
**Currently**: Zero tests for entire producer module

- [ ] `test_detection.py` — YOLO detector: mock TRT engine, verify postprocessing, bbox filtering, keypoint extraction, NMS
- [ ] `test_tracking.py` — Tracker: IOU matching, alpha-beta prediction, track creation/deletion, lost track detection, keypoint propagation
- [ ] `test_cropping.py` — Crop extraction: padding calculation, boundary handling, resize logic, keypoint translation (frame→crop coords)
- [ ] `test_quality_fast.py` — Fast quality: Tenengrad focus, brightness, contrast, gradient histograms, quality gate thresholds
- [ ] `test_buffer.py` — Buffer manager: insertion, diversity enforcement (SSIM), version tracking, eviction on track loss, bootstrap relaxation
- [ ] `test_pipeline.py` — Integration: end-to-end frame processing (mock all TRT engines), timing tracking, stats accumulation, early exits

### 9. Consumer Integration Tests

**File**: `tests/test_consumer_pipeline.py` (CREATE)
**Est. LOC**: ~300
**Depends on**: Consumer orchestrator (#6)

- [ ] End-to-end consumer flow: snapshot → rich quality → batch select → recipe → GPU preprocess → queue commit
- [ ] Version tracking: verify consumer doesn't reprocess same version
- [ ] Back-pressure: verify consumer pauses when queue full
- [ ] Error isolation: verify single track failure doesn't crash pipeline
- [ ] Empty snapshot handling: verify no-op when no updated bins

### 10. OCR Module Tests

**File**: `tests/test_parseq.py`, `tests/test_result_eval.py`, `tests/test_state_machine.py` (all CREATE)
**Est. LOC**: ~800
**Depends on**: OCR module (#1-5)

- [ ] `test_parseq.py` — Mock TRT engine, verify batch inference, logit decoding, per-char confidence, graceful failure
- [ ] `test_result_eval.py` — Per-character voting, duplicate dedup, partial reads, empty results, confidence scoring
- [ ] `test_state_machine.py` — All state transitions, timeout handling, mark_done() calls, forced completion on track loss, metrics

### 11. Batch Selector Chunk-07 Audit Fixes

**File**: `tests/test_batch_select.py` (MODIFY)
**Status**: In-progress from previous session, 8 audit issues identified

- [ ] Decide which of the 8 audit issues to fix (user has pending decisions)
- [ ] audit-01: Hardcoded positions coupled to config defaults (HIGH)
- [ ] audit-02: OCR scoring tests only verify ordering, never magnitude (HIGH)
- [ ] audit-03: test_mixed_keypoints_in_batch assertion weakened (MEDIUM)
- [ ] audit-04: Performance thresholds 5-10x too generous (MEDIUM)
- [ ] audit-05: test_scoring_enhancement_interaction has implicit OCR score assumption (MEDIUM)
- [ ] audit-06: test_boundary_diversity_distance relies on float exactness (LOW)
- [ ] audit-07: No test exercises non-default config weight combinations (LOW)
- [ ] audit-08: No negative-path test for package_batch_output non-matching enhance ROIs (LOW)

### 12. Consumer Rich Quality Tests

**File**: `tests/test_quality_rich.py` (CREATE)
**Est. LOC**: ~500
**Currently**: Zero tests for 900-line module

- [ ] Pose metrics computation (skew, perspective, quad area)
- [ ] order_keypoints_by_angle (deterministic ordering, handles any input)
- [ ] OCR likelihood scoring (6 weighted factors)
- [ ] Eligibility flag computation (top8, enhance, homography)
- [ ] Edge cases: None keypoints, zero-area crops, extreme values

---

## P2: MEDIUM — Operational Pain

### 13. Model Training Pipeline

**Files**: `scripts/` (multiple CREATE)
**Depends on**: MODEL-TRAINING-PLAN.md
**Currently**: No training scripts exist

- [ ] `scripts/polygon_to_keypoints.py` — Convert SAM polygons → bbox + 4 corners (convex hull + Douglas-Peucker)
- [ ] `scripts/review_keypoints.py` — Visual review tool for extracted corners (matplotlib overlay)
- [ ] `scripts/generate_crops.py` — Run producer pipeline on labeled images → crop dataset for corner CNN
- [ ] `scripts/augment_dataset.py` — Apply drone augmentations (scale jitter, rotation, blur, exposure)
- [ ] `scripts/train_corner_cnn.py` — Training script using tlpss/keypoint-detection framework
- [ ] `scripts/validate_models.py` — Run all 3 models on held-out set, report metrics
- [ ] `scripts/export_parseq_to_trt.py` — Export PARSeq PyTorch → ONNX → TRT engine

### 14. Error Recovery and Resilience

**Files**: Various (MODIFY)
**Est. LOC**: ~400

- [ ] GPU OOM detection and recovery in producer pipeline (try/except around TRT calls, fallback to skip frame)
- [ ] GPU OOM detection and recovery in consumer GPU preprocessor
- [ ] Model load retry logic (3 attempts with backoff for TRT engine loading)
- [ ] Queue overflow alerting (log WARNING when OCR queue > 80% capacity)
- [ ] Consumer stall detection (log WARNING if no snapshots pulled for > 5 seconds)
- [ ] Graceful shutdown protocol (drain in-flight work on SIGTERM/SIGINT)

### 15. Structured Logging for Consumer + OCR

**Files**: `consumer/pipeline.py`, `ocr/worker.py` (MODIFY)
**Est. LOC**: ~200

- [ ] JSONL structured logging in consumer pipeline (match producer format)
- [ ] Per-track processing metrics in JSONL (rich_quality_ms, batch_select_ms, recipe_ms, preprocess_ms)
- [ ] OCR worker logging: per-batch inference time, confidence histograms, state transitions
- [ ] Correlation ID: track_id + version as log correlation key across producer/consumer/OCR
- [ ] Log rotation config (max file size, retention policy)

### 16. Configuration Externalization

**Files**: `configs/` (CREATE directory + files)
**Est. LOC**: ~200

- [ ] `configs/producer.yaml` — externalize ProducerConfig defaults
- [ ] `configs/consumer.yaml` — externalize ConsumerConfig defaults
- [ ] `configs/ocr.yaml` — OCR-specific config
- [ ] `configs/models.yaml` — all model paths, devices, batch sizes in one place
- [ ] Config loader: `load_config(profile="production")` with env var overrides
- [ ] Environment-specific profiles: dev, test, production

### 17. Design Doc Updates

**Files**: `.claude/designs/` (MODIFY)

- [ ] Update `yolo-pose-to-yolo-corner-cnn.md`: change input_h from 128 to 80 (matches implemented plan)
- [ ] Update `architecture.md`: mark consumer modules as complete, update status table
- [ ] Finalize PARSeq aggregation design doc (user says "almost ready")
- [ ] Update `architecture.md` with corner CNN Stage 3.5 in system diagram

---

## P3: LOW — Nice to Have / Post-Launch

### 18. Health Monitoring

**Files**: Various (CREATE/MODIFY)
**Est. LOC**: ~500

- [ ] Prometheus metrics endpoint (`/metrics`) — requires FastAPI wrapper (external)
- [ ] GPU utilization tracking (NVML queries: VRAM usage, GPU %, temperature)
- [ ] Per-model inference latency histograms (YOLO, corner CNN, PARSeq)
- [ ] Queue depth gauges (producer buffer, OCR ready queue)
- [ ] Track lifecycle metrics (avg time detection → OCR complete)
- [ ] Health check endpoint (`/health`) — all models loaded, GPU available, queues responsive

### 19. Deployment Configuration

**Files**: Root directory (CREATE)
**Est. LOC**: ~300

- [ ] `Dockerfile` — multi-stage build (CUDA base → Python deps → app code)
- [ ] `docker-compose.yml` or `podman-compose.yml` — LPR + dependencies
- [ ] `.env.example` — required environment variables documented
- [ ] `systemd/lpr.service` — systemd unit file for production deployment
- [ ] GPU passthrough configuration for container (NVIDIA Container Toolkit)
- [ ] Model volume mount configuration (engines are large, don't bake into image)

### 20. Operational Scripts

**Files**: `scripts/` (CREATE)
**Est. LOC**: ~400

- [ ] `scripts/health_check.sh` — verify all models loaded, GPU available, queues responsive
- [ ] `scripts/benchmark.sh` — run benchmark suite on sample video, report per-stage latency
- [ ] `scripts/export_metrics.sh` — dump pipeline stats to CSV for analysis
- [ ] `scripts/purge_old_logs.sh` — clean up JSONL logs older than N days
- [ ] `scripts/gpu_monitor.sh` — continuous GPU utilization display (nvidia-smi loop)

### 21. Common Utilities Module

**File**: `common/` (CREATE)
**Status**: Referenced in architecture.md but doesn't exist
**Est. LOC**: ~200

- [ ] `common/timing.py` — shared timing utilities (monotonic timer, percentile tracker)
- [ ] `common/logging.py` — shared JSONL formatter, correlation ID injection
- [ ] `common/gpu.py` — CUDA availability check, device selection, OOM handling
- [ ] `common/config.py` — base config loader with YAML + env var support

### 22. Batch Selector Chunk-08 (Property-Based Tests)

**File**: `tests/test_batch_select.py` (MODIFY)
**Status**: Planned but not started

- [ ] Hypothesis property tests from plan: normalize_always_bounded, pose_distance_symmetric, diversity_never_exceeds_max, gate_never_increases, ocr_score_always_bounded
- [ ] Non-default config weight combination tests
- [ ] Config space boundary exploration

---

## External Integration (Parent Firefly Monorepo)

These items live OUTSIDE LPR-SingleDrone, in the parent `gpu/` or `central/` services.

### 23. Video Ingest Service (`gpu/lpr/ingest.py`)

- [ ] RTSP client connecting to SRS video server
- [ ] Frame decoding and sampling (match producer's frame_sample_interval_ms)
- [ ] Call `ProducerPipeline.process_frame(frame, idx, timestamp)` per frame
- [ ] Handle stream disconnection and reconnection
- [ ] Camera zoom detection (query DJI SDK telemetry) OR rely on passive geometric filtering

### 24. FastAPI Wrapper (`gpu/lpr/api.py`)

- [ ] `POST /api/lpr/start` — start LPR pipeline for a camera stream
- [ ] `POST /api/lpr/stop` — stop LPR pipeline
- [ ] `GET /api/lpr/results` — stream results via SSE or polling
- [ ] `WebSocket /ws/lpr/results` — real-time result streaming to frontend
- [ ] `GET /api/lpr/health` — health check
- [ ] `GET /api/lpr/metrics` — Prometheus metrics
- [ ] NATS/RabbitMQ publisher for result events
- [ ] PostgreSQL writer for result persistence

### 25. Frontend Dashboard (`central/`)

- [ ] Real-time plate detection display (map + video overlay)
- [ ] OCR result display with confidence scores
- [ ] Track history timeline (detection → OCR → complete)
- [ ] Alert system for high-confidence reads
- [ ] Search/filter by plate text, time range, confidence

---

## Summary by Priority

| Priority | Items | Est. Total LOC | Description |
|----------|-------|---------------|-------------|
| **P0** | #1-7 | ~2,100 | OCR module + consumer orchestrator + PARSeq engine |
| **P1** | #8-12 | ~3,100 | Test suites (producer, consumer, OCR, rich quality) |
| **P2** | #13-17 | ~1,200 | Training scripts, error recovery, logging, config, docs |
| **P3** | #18-22 | ~1,600 | Health monitoring, deployment, ops scripts, common utils |
| **External** | #23-25 | ~1,500+ | Video ingest, FastAPI, frontend (parent monorepo) |
| **Total** | 25 items | **~9,500 LOC** | |

---

## Recommended Execution Order

```
Phase A: OCR Foundation (P0 #1-5, #7)
  Get PARSeq engine → build inference wrapper → aggregation → state machine
  Depends on: design doc (user almost ready), trained model

Phase B: Wire Everything (P0 #6, #4)
  Consumer orchestrator → OCR worker loop
  Depends on: Phase A

Phase C: Validate (P1 #9-10)
  Consumer integration tests → OCR module tests
  Depends on: Phase B

Phase D: Harden (P2 #14-15, P1 #8)
  Error recovery → structured logging → producer tests
  Can run in parallel with Phase B/C

Phase E: Deploy (P3 #19, External #23-24)
  Dockerfile → video ingest → FastAPI wrapper
  Can start after Phase B
```

---

## Files That Don't Exist Yet (Must Create)

| File | Priority | Purpose |
|------|----------|---------|
| `ocr/ops_parseq.py` | P0 | PARSeq TRT inference |
| `ocr/ops_result_eval.py` | P0 | Per-character voting aggregation |
| `ocr/ops_state_machine.py` | P0 | ID lifecycle state machine |
| `ocr/worker.py` | P0 | OCR worker loop |
| `ocr/config.py` | P0 | OCR configuration |
| `ocr/models.py` | P0 | OCR data models |
| `consumer/pipeline.py` | P0 | Consumer orchestrator |
| `tests/test_detection.py` | P1 | Producer detection tests |
| `tests/test_tracking.py` | P1 | Producer tracking tests |
| `tests/test_cropping.py` | P1 | Producer cropping tests |
| `tests/test_quality_fast.py` | P1 | Producer fast quality tests |
| `tests/test_buffer.py` | P1 | Producer buffer tests |
| `tests/test_pipeline.py` | P1 | Producer pipeline tests |
| `tests/test_consumer_pipeline.py` | P1 | Consumer integration tests |
| `tests/test_parseq.py` | P1 | PARSeq inference tests |
| `tests/test_result_eval.py` | P1 | Aggregation tests |
| `tests/test_state_machine.py` | P1 | State machine tests |
| `tests/test_quality_rich.py` | P1 | Rich quality tests |
| `scripts/polygon_to_keypoints.py` | P2 | SAM polygon → bbox + corners |
| `scripts/train_corner_cnn.py` | P2 | Corner CNN training |
| `scripts/export_parseq_to_trt.py` | P2 | PARSeq model export |

## Files That Exist But Need Updates

| File | Priority | What Needs Changing |
|------|----------|-------------------|
| `ocr/__init__.py` | P0 | Add exports for new OCR modules |
| `architecture.md` | P2 | Update module status table, add corner CNN to diagram |
| `.claude/designs/yolo-pose-to-yolo-corner-cnn.md` | P2 | Change input_h 128→80 to match implementation |
| `tests/test_batch_select.py` | P1 | 8 audit issues from chunk-07 review |

---

*Generated from 4-agent parallel analysis of the full codebase.*

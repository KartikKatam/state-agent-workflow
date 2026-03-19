# Name Bank – LPR Module

**Goal:** Source of truth for naming variables, types, and files.
**Rule:** Agents must **read this file** before naming anything. If a concept exists here, **use this exact name**. If a new concept is needed, agents must request to add it here.

---

## 1. Canonical Types (Classes & Objects)

### Core Entities
- **`TrackId`** (`str`): Unique UUID for a tracked license plate object across frames.
  - *Do not use:* `PlateId`, `VehicleId`, or integer IDs.
- **`Frame`** (`object`): Container for raw video data + metadata (timestamp, index).
- **`RoiImage`** (`dataclass` in `producer/models.py`): Cropped region of interest containing a plate candidate.
  - *Must contain:* `track_id`, `crop_img` (pixel data), `bbox` (original coords), `padded_bbox`, `frame_idx`, `confidence`.
  - *Resize metadata:* `was_resized`, `resize_scale`, `crop_width`, `crop_height`.
  - *Pose metadata:* `keypoints` (crop coords, ordered), `keypoint_scores` (aligned).
- **`Recipe`** (`tensor`): A tensor of floating-point values (0.0–1.0) defining transform parameters for the GPU Preprocessor.
- **`Track`** (`dataclass` in `producer/models.py`): Tracker state with `track_id`, `position [cx, cy, w, h]`, `velocity [vx, vy, vw, vh]`, lifecycle counters (`age`, `hits`, `frames_lost`, `last_seen_frame`), and `confidence`.
- **`TrackerOutput`** (`dataclass` in `producer/models.py`): Bundle of active `tracks`, `confirmed_tracks`, `camera_motion`, and `TrackingTimings`.
- **`TrackingTimings`** (`dataclass` in `producer/models.py`): Timing metrics for tracking stages (LK, prediction, matching, update, cleanup, total).
- **`Detection`** (`dataclass` in `producer/models.py`): YOLO detection result with `bbox`, `confidence`, `class_id`, `frame_idx`.
- **`keypoints`** (`list[tuple[float,float]] | None`): Pose keypoints in ordered list. Current model order: Top-Right, Top-Left, Bottom-Right, Bottom-Left.
- **`keypoint_scores`** (`list[float] | None`): Per-keypoint confidence scores aligned with `keypoints` order.
- **`DetectorOutput`** (`dataclass` in `producer/models.py`): Bundle of `detections` list and `DetectionTimings`.
- **`FastQualityMetrics`** (`dataclass` in `producer/models.py`): GPU-computed quality metrics with `focus_tenengrad`, `brightness_mean`, `contrast_std`, `over_exposed_frac`, `under_exposed_frac`, `band_edge_mean`, `gradient_histogram`.
- **`RoiFastQuality`** (`dataclass` in `producer/models.py`): ROI with fast quality analysis results. Contains `roi` (RoiImage), `metrics` (FastQualityMetrics), `quality_score`, `passes_min_quality`, and `thumb_gray` (48×160 uint8 thumbnail).
- **`IdBin`** (`dataclass` in `producer/models.py`): Per-ID buffer storing diverse ROI candidates with `track_id`, `entries`, `version`, `last_update_frame_idx`, `successful_insertions`, `attempted_insertions`.
- **`BinSnapshot`** (`dataclass` in `producer/models.py`): Immutable snapshot of IdBin for Consumer with `track_id`, `version`, `entries`, `last_update_frame_idx`.
- **`BufferStats`** (`dataclass` in `producer/models.py`): Per-ID insertion statistics with `total_attempts`, `quality_gate_drops`, `out_of_order_drops`, `appends`, `replaces`, `similarity_drops`, `current_bin_size`, `current_version`.
- **`RichQualityMetrics`** (`dataclass` in `consumer/models.py`): Comprehensive quality analysis with 30+ metrics including:
  - *Geometric*: `plate_width_px`, `plate_height_px`, `crop_clip_fraction`, keypoint data
  - *Pose*: `skew_degrees`, `perspective_score`, `pose_signature` (11D vector), `quad_area_px`, `edge_ratio`
  - *Photometric*: `luminance_mean`, `luminance_p05`, `luminance_p95`, `black_clip_fraction`, `white_clip_fraction`, `global_contrast`, `local_contrast`, `tenengrad`, `noise_std`
  - *Eligibility flags*: `too_small`, `clipped`, `very_blurry`, `mildly_soft`, `exposure_bad`, `low_contrast`, `noisy`, `top8_eligible`, `enhance_eligible`, `homography_eligible`
- **`RoiRichQuality`** (`dataclass` in `consumer/models.py`): ROI annotated with `RichQualityMetrics`. Contains `roi_fq` (RoiFastQuality) and `metrics` (RichQualityMetrics).
- **`BatchSelection`** (`dataclass` in `consumer/models.py`): Selected batch with `track_id`, `version`, `selected` (List[RoiRichQuality]).
- **`RecipeBatch`** (`dataclass` in `consumer/models.py`): Preprocessing recipes with `track_id`, `version`, `recipes` (np.ndarray Nx3), `selected` (List[RoiRichQuality]).
- **`OcrQueueItem`** (`dataclass` in `consumer/models.py`): Final OCR work item with `track_id`, `version`, `batch_tensor` (np.ndarray), `selected` (List[RoiRichQuality]), `recipes` (np.ndarray).
- **`IdPoolState`** (`dataclass` in `consumer/ops_scheduler.py`): Tracks `last_versions` (Dict[TrackId, int]) for snapshot versioning.
- **`OcrQueue`** (`class` in `consumer/queue.py`): Bounded FIFO queue with back-pressure for OCR consumption.
- **`OcrQueueStats`** (`dataclass` in `consumer/queue.py`): Queue stats with `capacity` and `size`.

### Coordinates & Geometry
- **`BBox`** (`tuple` or `list`): Bounding box coordinates.
  - **STANDARD:** `[x1, y1, x2, y2]` (Top-Left absolute pixel, Bottom-Right absolute pixel).
  - *Note:* YOLO outputs (center-normalized) must be converted to this format immediately upon ingestion.
- **`keypoints` order**: Top-Right, Top-Left, Bottom-Right, Bottom-Left. To swap order, remap the list once during detection postprocess.

### Configuration
- **`ProducerConfig`**: Tunables for detection, tracking, and cropping.
- **`ConsumerConfig`**: Tunables for batch selection, recipes, and GPU preprocessing.
- **`OcrConfig`**: Tunables for the TrOCR engine and result evaluation.

---

## 2. Canonical Variable Names

### Image Data
- **`frame_img`** (`np.ndarray`): Raw full-frame image on CPU (OpenCV BGR).
- **`crop_img`** (`np.ndarray`): Cropped plate image on CPU (may be adaptively resized).
- **`batch_tensor`** (`torch.Tensor`): Batch of preprocessed images on GPU (N, C, H, W).
- **`padded_bbox`** (`list[float]`): Bbox with padding applied [x1, y1, x2, y2] (frame coordinates).
- **`was_resized`** (`bool`): Flag indicating if adaptive resize was applied to crop.
- **`resize_scale`** (`float`): Scale factor from resize operation (1.0 = no resize).

### Metrics & State
- **`quality_score`** (`float`): Normalized 0.0–1.0 score indicating general image quality (from FastQualityMetrics).
- **`focus_tenengrad`** (`float`): Sobel gradient energy normalized by area (Tenengrad metric for sharpness).
- **`brightness_mean`** (`float`): Mean luminance value [0..1] from grayscale or L-channel.
- **`contrast_std`** (`float`): Standard deviation of luminance values (fast quality metric).
- **`band_edge_mean`** (`float`): Mean gradient magnitude in central character band (30-70% vertical).
- **`thumb_gray`** (`np.ndarray`): Grayscale thumbnail for buffer similarity checks (64×16 uint8).
- **`gradient_histogram`** (`np.ndarray`): L2-normalized feature vector (144D) float32 with 16 orientation bins × 9 spatial quadrants (FastQualityMetrics).
- **`pose_signature`** (`np.ndarray`): 11D diversity vector [8 normalized quad coords, skew, persp, plate_h] from RichQualityMetrics.
- **`skew_degrees`** (`float | None`): Rotation angle from horizontal (median of top/bottom quad edges), None if no quad detected.
- **`perspective_score`** (`float | None`): Max perspective distortion ratio (max of lr/tb ratios), None if no quad.
- **`version`** (`int`): Incrementing counter per IdBin used to signal Consumer that TrackId has new/better candidates.
- **`ocr_confidence`** (`float`): 0.0–1.0 probability score from the OCR engine.
- **`id_state`** (`Enum`): Current status of a TrackId (e.g., `tracking`, `pending_ocr`, `complete`).

### Dimensions & Time
- **`timestamp_ms`** (`int`): Unix epoch in milliseconds.
- **`frame_idx`** (`int`): Sequential frame number from source.
- **`width`**, **`height`** (`int`): Image dimensions.
- **`canonical_width`**, **`canonical_height`** (`int`): Target dimensions for letterboxed canonical crop (RichQualityMetrics).
- **`plate_width_px`**, **`plate_height_px`** (`float`): Plate dimensions in pixels from bbox (RichQualityMetrics).

---

## 3. Naming Conventions

### Files & Directories
- **Directories:** `producer/`, `consumer/`, `ocr/`, `common/`.
- **Logic Files:** `snake_case`. Format: `ops_<topic>.py` (e.g., `ops_tracking.py`, `ops_visuals.py`).
- **Orchestration:** `pipeline.py` (one per module).

### Functions
- **Style:** `snake_case`. Pattern: `verb_noun`.

**Producer Functions:**
- **Detection:** `detect_plate_rois(frame_img, frame_idx, detector, sampler, cfg, timestamp_ms) -> DetectorOutput` (public API in `ops_detection.py`)
- **Tracking:** `track_detections(detections, curr_frame, frame_idx, tracker) -> TrackerOutput` (public API in `ops_tracking.py`)
- **Cropping:** `crop_tracks(frame_img, tracks, frame_idx, cfg) -> list[RoiImage]` (public API in `ops_cropping.py`)
- **Quality Analysis:** `fast_quality_analyze_rois(rois, cfg, device) -> List[RoiFastQuality]` (public API in `ops_quality_fast.py`)
- **Buffer Management:**
  - `insert_roi(roi_fq) -> bool` (IdBufferManager method)
  - `get_snapshot_if_updated(track_id, last_seen_version) -> BinSnapshot | None` (IdBufferManager method)
  - `get_all_updated_bins(last_versions) -> list[BinSnapshot]` (IdBufferManager method)
  - `evict_tracks(track_ids)` (IdBufferManager method)
- **Pipeline:** `process_frame(frame_img, frame_idx, timestamp_ms) -> FrameResult` (ProducerPipeline method)

**Consumer Functions:**
- **Scheduler:** `get_next_snapshot(buffer_mgr, state, cfg) -> BinSnapshot | None` (in `ops_scheduler.py`)
- **Rich Analysis:** `rich_quality_analyze_snapshot(snapshot, cfg) -> List[RoiRichQuality]` (in `ops_quality_rich.py`)
- **Batch Selection:** `get_best_batch(track_id, version, candidates, cfg) -> BatchSelection` (in `ops_batch_select.py`)
- **Recipe Generation:** `generate_recipe_batch(track_id, version, selected, cfg) -> RecipeBatch` (in `ops_recipe.py`)
- **GPU Preprocessing:** `preprocess_batch(recipe_batch, cfg) -> np.ndarray` (in `ops_preprocess_gpu.py`)
- **Queue Operations:** `push(item) -> bool`, `pop() -> OcrQueueItem | None` (OcrQueue methods)

**Helper Functions:**
- **Converters:** `bbox_to_position`, `position_to_bbox` (tracking utilities)
- **Geometry:** `compute_padded_bbox`, `adaptive_resize_crop` (cropping utilities)
- **Normalization:** `_l2_normalize(vec) -> np.ndarray` (batch selection utility)

### Collections
- **Rule:** Always use plural nouns for lists/sets.
- **Examples:** `rois`, `candidates`, `frames`, `results`.

---

## 4. Forbidden Synonyms (Strict)

| Instead of... | Use Canonical Name |
| :--- | :--- |
| `plate_id`, `pid`, `obj_id` | **`track_id`** |
| `box`, `rect`, `window` | **`bbox`** |
| `pic`, `photo`, `raw` | **`image`** or **`frame_img`** |
| `param_list`, `tuner_vals` | **`recipe`** |
| `prob`, `score` (for OCR) | **`ocr_confidence`** |
| `angle_deg`, `rotation` | **`skew_degrees`** (for plate orientation) |
| `gradient_hist` | **`gradient_histogram`** (FastQualityMetrics) or **`pose_signature`** (RichQualityMetrics) |
| `brightness`, `luma` | **`brightness_mean`** (FastQualityMetrics) or **`luminance_mean`** (RichQualityMetrics) |
| `sharpness`, `focus` | **`focus_tenengrad`** (FastQualityMetrics) or **`tenengrad`** (RichQualityMetrics) |

---

## 5. Critical Bug Fixes (Dec 27, 2025)

**Issue #1**: `consumer/ops_batch_select.py` attempted to access non-existent `metrics.gradient_hist`
- **Fix**: Changed to use `metrics.pose_signature` (11D diversity vector)
- **Reason**: RichQualityMetrics does not have `gradient_hist` field

**Issue #2**: `consumer/ops_recipe.py` attempted to access non-existent `metrics.angle_deg`
- **Fix**: Changed to `metrics.skew_degrees or 0.0` (handles None case)
- **Reason**: RichQualityMetrics field is named `skew_degrees`, not `angle_deg`

---

## 6. Data Flow Chain

**Complete pipeline object transformations:**

```
Frame (np.ndarray)
  ↓ detect_plate_rois()
Detection (dataclass)
  ↓ track_detections()
Track (dataclass)
  ↓ crop_tracks()
RoiImage (dataclass)
  ↓ fast_quality_analyze_rois()
RoiFastQuality (dataclass)
  ↓ insert_roi() → IdBin.entries
BinSnapshot (dataclass) [Producer→Consumer handoff]
  ↓ rich_quality_analyze_snapshot()
RoiRichQuality (dataclass)
  ↓ get_best_batch()
BatchSelection (dataclass)
  ↓ generate_recipe_batch()
RecipeBatch (dataclass)
  ↓ preprocess_batch()
np.ndarray (batch_tensor)
  ↓ OcrQueue.push()
OcrQueueItem (dataclass) [Consumer→OCR handoff]
```

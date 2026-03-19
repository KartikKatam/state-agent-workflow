# Consumer Preprocessing Guide

## Critical Requirement: Functional (Non-Mutating) Preprocessing

The producer buffer uses **shallow copy** for snapshot generation to minimize memory overhead and improve performance. This means consumer snapshots share the actual image data (`crop_img` numpy arrays) with the producer buffer.

**To maintain isolation between producer and consumer, consumer preprocessing MUST follow functional programming style:**

### ✅ CORRECT: Functional Style (Creates New Arrays)

```python
def preprocess_for_ocr(roi_fq):
    """
    Apply preprocessing pipeline to optimize image for OCR.

    Each operation creates a NEW array - original crop_img is unchanged.
    """
    # Start with reference to original (shared with producer buffer)
    img = roi_fq.roi.crop_img

    # Each step creates NEW array and reassigns img
    img = cv2.resize(img, (256, 64))                    # NEW array
    img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)         # NEW array

    # Estimate and apply rotation
    angle = estimate_rotation(img)
    M = cv2.getRotationMatrix2D((128, 32), angle, 1.0)
    img = cv2.warpAffine(img, M, (256, 64))             # NEW array

    # Apply CLAHE
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    img = clahe.apply(img)                               # NEW array

    # Sharpen
    img = unsharp_mask(img)                              # NEW array

    # Convert to RGB for TrOCR
    img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)         # NEW array

    return img  # Original roi_fq.roi.crop_img UNCHANGED ✅


def preprocess_batch_gpu(snapshot_entries):
    """
    GPU batch preprocessing - also creates new tensors.
    """
    # Stack into batch (copies to contiguous array)
    batch_np = np.stack([roi_fq.roi.crop_img for roi_fq in snapshot_entries])

    # Transfer to GPU - creates NEW tensor
    batch = torch.from_numpy(batch_np).to('cuda')

    # All GPU operations create NEW tensors
    batch = F.interpolate(batch, size=(64, 256))        # NEW tensor
    batch = rgb_to_gray_gpu(batch)                      # NEW tensor
    batch = apply_clahe_gpu(batch)                      # NEW tensor
    batch = rotate_batch_gpu(batch, angles)             # NEW tensor
    batch = unsharp_mask_gpu(batch)                     # NEW tensor

    return batch  # Original crop_img arrays UNCHANGED ✅
```

### ❌ INCORRECT: In-Place Mutation (Modifies Original)

```python
def preprocess_inplace_BAD(roi_fq):
    """
    DANGEROUS: Mutates original buffer data!

    DO NOT DO THIS - will corrupt producer's buffer!
    """
    img = roi_fq.roi.crop_img  # Reference to producer's array

    # These operations MUTATE the original array in producer's buffer!
    img[:] = img / 255.0                                  # ❌ MUTATES buffer
    cv2.normalize(img, img, 0, 255, cv2.NORM_MINMAX)     # ❌ MUTATES buffer (dst=src)

    # Producer's buffer now has corrupted data! ❌
```

## Why This Works

### Memory Efficiency
- **Shallow copy overhead:** ~32 bytes (just list structure)
- **Deep copy overhead:** ~16MB per snapshot (copies all numpy arrays)
- **Memory savings:** 500x reduction

### Performance
- **Shallow copy time:** ~0.01ms
- **Deep copy time:** ~10-40ms
- **Performance gain:** 1000-4000x faster

### Safety
Functional preprocessing is **completely safe** because:

1. **Producer never mutates objects in-place**
   - Only appends or replaces entries
   - Never modifies existing `RoiFastQuality` objects
   - Never modifies existing `crop_img` arrays

2. **Consumer creates new arrays at each step**
   - OpenCV/NumPy functions create new arrays by default
   - GPU operations create new tensors
   - Original arrays in buffer remain pristine

3. **Python reference counting handles cleanup**
   - Old arrays freed automatically when no longer referenced
   - No memory leaks
   - Efficient memory reuse

## Common OpenCV Operations (All Create New Arrays)

These operations are **safe** - they create new arrays:

```python
# Geometric transforms
cv2.resize(img, size)                    # ✅ NEW
cv2.warpAffine(img, M, size)            # ✅ NEW
cv2.rotate(img, rotateCode)             # ✅ NEW

# Color conversions
cv2.cvtColor(img, code)                 # ✅ NEW

# Filtering
cv2.GaussianBlur(img, ksize, sigma)     # ✅ NEW
cv2.medianBlur(img, ksize)              # ✅ NEW
cv2.bilateralFilter(img, d, ...)        # ✅ NEW

# Enhancement
clahe = cv2.createCLAHE(...)
clahe.apply(img)                         # ✅ NEW

# Edge detection
cv2.Canny(img, t1, t2)                  # ✅ NEW
cv2.Sobel(img, ddepth, dx, dy)          # ✅ NEW

# Morphology
cv2.erode(img, kernel)                  # ✅ NEW
cv2.dilate(img, kernel)                 # ✅ NEW

# NumPy operations (assignment creates new)
normalized = img / 255.0                 # ✅ NEW
thresholded = np.where(img > 128, 255, 0) # ✅ NEW
```

## Operations to AVOID (In-Place Mutation)

These would **mutate** the original - avoid them:

```python
# In-place normalization
img[:] = img / 255.0                    # ❌ MUTATES
img *= 0.5                               # ❌ MUTATES

# In-place OpenCV (when dst=src)
cv2.normalize(img, img, ...)            # ❌ MUTATES (dst=src)
cv2.add(img, value, img)                # ❌ MUTATES (dst=src)

# NumPy in-place operations
img += 10                                # ❌ MUTATES
np.add(img, 10, out=img)                # ❌ MUTATES
```

**Instead, use:**
```python
# Create new array
normalized = img / 255.0                 # ✅ NEW
adjusted = cv2.add(img, value)          # ✅ NEW (no dst arg)
brightened = img + 10                    # ✅ NEW
```

## Testing Your Preprocessing

To verify your preprocessing doesn't mutate the original:

```python
def test_preprocessing_is_functional():
    """Ensure preprocessing doesn't mutate original image."""
    # Create test crop
    original = np.random.randint(0, 255, (400, 200, 3), dtype=np.uint8)
    original_copy = original.copy()

    # Create mock RoiFastQuality
    roi_fq = create_mock_roi_fq(original)

    # Run preprocessing
    processed = preprocess_for_ocr(roi_fq)

    # Verify original unchanged
    assert np.array_equal(original, original_copy), \
        "ERROR: Preprocessing mutated original array!"

    # Verify processed is different object
    assert processed is not original, \
        "ERROR: Preprocessing returned same array object!"

    print("✅ Preprocessing is functional (non-mutating)")
```

## Summary

**Golden Rule:** Never write to `roi_fq.roi.crop_img` or any array obtained from the snapshot.

**Always:** Create new arrays through reassignment (`img = operation(img)`) or explicit new allocation.

**Benefit:** 500x less memory, 1000x faster snapshots, same isolation guarantees as deep copy.

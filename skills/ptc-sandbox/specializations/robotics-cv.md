# Domain Specialization: Robotics-CV

Loaded when `Project Domain: robotics-cv` is declared. Adds domain-specific
packages and patterns to any role's PTC container.

## Domain Packages

These are added to your container on top of your role packages:

```
opencv-python-headless    # Image processing, contour detection, color spaces
scikit-image              # Advanced image analysis (thresholding, morphology)
scipy                     # Transformation matrices, spatial analysis, signal processing
torch (CPU)               # Model inference verification
torchvision (CPU)         # Pre-trained model loading, image transforms
```

Note: CPU-only torch/torchvision to keep container size reasonable.

## Image Analysis Patterns

### Verify Image Processing Pipeline

```python
import cv2, json
import numpy as np

img = cv2.imread("/workspace/test_data/sample_frame.png")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
_, thresh = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
print(json.dumps({
    "shape": list(img.shape),
    "contours_found": len(contours),
    "largest_area": max(cv2.contourArea(c) for c in contours) if contours else 0,
}))
```

### Transformation Matrix Validation

```python
import numpy as np
import json
from scipy.spatial.transform import Rotation

# Verify rotation matrix is valid (orthogonal, det=1)
R = np.array(rotation_matrix)
is_orthogonal = np.allclose(R @ R.T, np.eye(3), atol=1e-6)
det_is_one = np.isclose(np.linalg.det(R), 1.0, atol=1e-6)
euler = Rotation.from_matrix(R).as_euler("xyz", degrees=True)
print(json.dumps({
    "valid_rotation": bool(is_orthogonal and det_is_one),
    "euler_angles_deg": euler.tolist(),
}))
```

### Model Inference Smoke Test

```python
import torch, json
from torchvision import models, transforms
from PIL import Image

model = models.resnet18(pretrained=False)
model.eval()
img = Image.new("RGB", (224, 224))
transform = transforms.Compose([transforms.ToTensor(), transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
tensor = transform(img).unsqueeze(0)
with torch.no_grad():
    output = model(tensor)
print(json.dumps({
    "output_shape": list(output.shape),
    "inference_ok": True,
}))
```

### Sensor Data Analysis

```python
import numpy as np, json
from scipy import signal

# Analyze IMU data for noise characteristics
imu_data = np.loadtxt("/workspace/test_data/imu_readings.csv", delimiter=",")
freqs, psd = signal.welch(imu_data[:, 0], fs=100)  # 100Hz sampling
dominant_freq = freqs[np.argmax(psd)]
noise_floor = np.median(psd)
print(json.dumps({
    "samples": len(imu_data),
    "dominant_freq_hz": float(dominant_freq),
    "noise_floor": float(noise_floor),
}))
```

## When to Use Domain Packages

- **Explorer:** opencv for image-based code analysis (e.g., analyzing generated visualizations)
- **Tester:** torch for model inference smoke tests, scipy for numerical verification
- **Coder:** scipy for validating transformation math, numpy for array operations
- **Auditor:** opencv + numpy for verifying image processing correctness in test outputs

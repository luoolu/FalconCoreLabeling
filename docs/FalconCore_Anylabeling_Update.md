# AnyLabeling v0.4.29 — Enhanced Edition

> **A drop‑in replacement for the original [AnyLabeling](https://github.com/cvlabmcu/anylabeling)** with a focus on richer label management, finer visual control, and faster polygon annotation.

---

## ✨ What’s New

|  #  |  Feature                                    |  Status  |  Notes                                                                             |
| :-: | ------------------------------------------- | :------: | ---------------------------------------------------------------------------------- |
|  1  | **Multi‑label per object**                  |     ✅    | Assign several labels to a single bounding box/polygon.                            |
|  2  | **Image‑level labels**                      |     ✅    | Tag the whole image (comma‑separated list).                                        |
|  3  | **Dynamic label sets**                      |     ✅    | Switch between pre‑defined label lists at runtime; UI autoupdates selected labels. |
|  4  | **Adjustable contour width & mask opacity** |     ✅    | Sliders in the toolbar for instant visual fine‑tuning.                             |
|  5  | **Freehand polygon drawing**                |     ✅    | Click once to start, trace with the cursor, double‑click to finish.                |
|  6  | **SAM / SAM‑2 one‑click segmentation**      |    🚧    | Prototype available; quality tuning & post‑processing still in progress.           |

---

## Installation

```bash
# Clone your fork (replace with your repo URL)
git clone https://github.com/<user>/anylabeling-enhanced.git
cd anylabeling-enhanced

# (Recommended) create & activate a virtualenv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install
pip install -e .
```

> **Minimum requirements**: Python ≥3.8, PyQt ≥5, PyTorch ≥2.0 (for SAM features).

---

## Quick Start (GUI)

```bash
python -m anylabeling.app
```

| Shortcut             | Action                                         |
| -------------------- | ---------------------------------------------- |
| **Ctrl + Shift + L** | Open **Image Label** dialog (tag entire image) |
| **F**                | Toggle **freehand** polygon mode               |

### Image‑level Labeling

1. *Menu Bar → Edit → **Set Image Label***
2. Enter one or more labels (comma‑separated).
3. Press **OK**. Labels are saved to `image_labels` in the JSON.

### Object‑level Multi‑Labeling

*Hold <kbd>Ctrl</kbd>/<kbd>Shift</kbd> while clicking the second (and subsequent) label in the list* to keep previous selections.

### Switching Label Sets

*Menu Bar → **Label Sets*** → select a predefined set.
All open combo‑boxes instantly refresh to the new labels.

To define or edit sets, modify `~/.anylabelingrc` (**or** pass `--config /path/to/your_config.yaml` at launch) and restart.

---

## Configuration Reference

```yaml
# ~/.anylabelingrc (generated on first run)
labels: ["person", "car", "tree"]
label_sets:
  coco: ["person", "bicycle", "car", "motorcycle", "bus", "truck"]
  cityscapes: ["road", "sidewalk", "building", "wall", "fence", "pole"]
contour_width: 2          # default stroke (px)
mask_opacity: 0.5         # default alpha [0‑1]
```

* **contour\_width** and **mask\_opacity** are also adjustable at runtime via the toolbar sliders.

---

## Advanced: SAM‑based Auto‑Segmentation *(experimental)*

```python
from segment_anything import SamAutomaticMaskGenerator, sam_model_registry

sam = sam_model_registry["vit_h"](checkpoint="sam_vit_h_4b8939.pth").cuda()
mask_generator = SamAutomaticMaskGenerator(
    model=sam,
    points_per_side=64,
    pred_iou_thresh=0.95,
    stability_score_thresh=0.90,
    box_nms_thresh=0.7,
    crop_n_layers=2,
    crop_overlap_ratio=512/1500,
    min_mask_region_area=500,
)
```

* **TODO**: refine thresholds, add morphological post‑processing to split joined instances and smooth jagged edges.

---

## Development

```bash
# Run in dev‑watch mode (rebuild on file changes)
poetry install  # or pip install -e .[dev]
python -m anylabeling.app --reset-config
```

### File layout of key patches

```text
anylabeling/
└── views/labeling/label_widget.py
    ├── update_label_dialog_labels   # lines 1705‑1715
    ├── switch_label_set             # lines 3135‑3150
    └── … plus additional methods for multi‑label & sliders
```

---

## Known Issues

* When the **Label Sets** menu is changed *after* an object is annotated, the label combo retains the old list until the app is restarted.  *(Workaround: save your session, restart the app.)*
* SAM auto‑segmentation groups touching instances and produces ragged edges in complex scenes. Parameter tuning and post‑processing are on the roadmap.

---

## Contributing

Pull requests are welcome! Please **open an issue first** to discuss major changes. For feature parity with upstream AnyLabeling, keep commits modular: *GUI*, *label‑logic*, *segmentation*.

---

## License

Distributed under the same license as the original AnyLabeling (GPL‑3.0).

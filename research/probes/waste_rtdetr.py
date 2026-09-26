"""Does COCO RT-DETRv2-r18 (cached PekingU/rtdetr_v2_r18vd, CPU) see anything on waste candidate crops?
Each candidate: a 6 m native crop upscaled to 640 px. Prints detections above 0.3.
Run from backend/: HF_HUB_OFFLINE=1 uv run --frozen --group sam python ../research/probes/waste_rtdetr.py CANDIDATES.json"""

import json
import sys
import time
from pathlib import Path

import torch
from transformers import AutoImageProcessor, RTDetrV2ForObjectDetection

sys.path.insert(0, str(Path(__file__).parent))
from waste_sheet import _crop  # noqa: E402

from marcaj.tiles import DATA_DIR  # noqa: E402

NAME = "PekingU/rtdetr_v2_r18vd"
torch.set_num_threads(4)
processor = AutoImageProcessor.from_pretrained(NAME, local_files_only=True)
model = RTDetrV2ForObjectDetection.from_pretrained(NAME, local_files_only=True).eval()
items = json.loads(Path(sys.argv[1]).read_text())
started, hits = time.perf_counter(), 0
for number, c in enumerate(items, start=1):
    c0, r0, c1, r1 = c["px"]
    image = _crop(DATA_DIR / "tiles" / c["tile"], (c0 + c1) / 2, (r0 + r1) / 2, 6.0, 640)
    with torch.no_grad():
        output = model(**processor(images=image, return_tensors="pt"))
    result = processor.post_process_object_detection(output, threshold=0.3, target_sizes=[(640, 640)])[0]
    found = [(model.config.id2label[int(label)], round(float(score), 2)) for score, label in zip(result["scores"], result["labels"])]
    hits += bool(found)
    print(number, c["tile"][7:-4], c["kind"], f"{c['area_m2']:.2f}", found)
print(f"{hits}/{len(items)} crops with a detection, {time.perf_counter() - started:.0f} s")

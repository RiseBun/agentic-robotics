"""Make the pinned SAM3 dataset's video decoder optional for image inference.

The inference import graph includes dataset dataclasses. Decord has no Linux
ARM64 wheel, but is only used to read a video file in this dataset. Defer that
import to its actual use; do not change weights, images or model computation.
Video training still requires a real decord installation and fails if absent.
"""
from pathlib import Path

root = Path(__file__).resolve().parents[2]
target = root/'third_party/cap-x/capx/third_party/sam3/sam3/train/data/sam3_image_dataset.py'
source = target.read_text()
old = 'from decord import cpu, VideoReader\n'
anchor = '                    # Going to load a video frame\n'
new = anchor+'                    from decord import cpu, VideoReader\n'
if new in source and old not in source.split('class ', 1)[0]:
    print('SAM3 video import already deferred')
elif source.count(old) == 1 and source.count(anchor) == 1:
    target.write_text(source.replace(old, '').replace(anchor, new))
    print('Deferred SAM3 video-only import; image inference unchanged')
else:
    raise RuntimeError('Unexpected SAM3 source; refusing to alter an unknown version')

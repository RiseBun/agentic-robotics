"""Reuse the closed-only natural scorer without admitting another model trial."""
from pathlib import Path

root=Path(__file__).resolve().parent
previous=root.parents[1]/'agentic_cartesian_motion_20261119/model-v1'
(root/'export_closed.py').write_text((previous/'export_closed.py').read_text(),encoding='utf-8')
score=(previous/'score_closed.py').read_text().replace('seed 83','seed 85')
(root/'score_closed.py').write_text(score,encoding='utf-8')

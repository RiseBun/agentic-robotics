from pathlib import Path

root=Path(__file__).resolve().parent
previous=root.parent/'v1'
(root/'collect_admission.py').write_text((previous/'collect_admission.py').read_text().replace('len(rows)!=3','len(rows)!=1').replace('all original natural admissions','the one original natural admission'),encoding='utf-8')
(root/'export_closed.py').write_text((previous/'export_closed.py').read_text()
    .replace("len(admitted)!=3 or len({r['trial_id'] for r in admitted})!=3", "len(admitted)!=1 or len({r['trial_id'] for r in admitted})!=1")
    .replace('all three original trials','one original trial'),encoding='utf-8')
(root/'score_closed.py').write_text((previous/'score_closed.py').read_text().replace('len(admitted)!=3','len(admitted)!=1')
    .replace('all three original admissions','one original admission')
    .replace('Three natural model-driven tasks, seed 85, once each','One natural model-driven restack, seed 86'),encoding='utf-8')

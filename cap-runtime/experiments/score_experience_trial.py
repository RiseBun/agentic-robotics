"""Offline experience diagnosis: check all closes/corpus freezes before private scoring."""
import argparse
import hashlib
import json
from pathlib import Path
from experiments.score_retreat_model import score as score_recovery


def score(root):
    root = Path(root)
    protocol = json.loads((root / 'campaign/protocol.json').read_text())
    audits = {}
    for case in protocol['cases']:
        folder = root / 'campaign' / case['mode']
        result = json.loads((folder / 'result.json').read_text())
        if result.get('experience_corpus_unchanged') is not True:
            raise ValueError('Frozen experience corpus changed or unchecked')
        logs = [json.loads(line) for file in (folder / 'records').glob('*/workbench.jsonl')
                for line in file.read_text(encoding='utf-8').splitlines() if line]
        retrievals = [e for e in logs if e['kind'] == 'experience_retrieval']
        audits[case['episode_id']] = dict(
            condition='candidate' if case.get('experience') else 'baseline',
            task=case['diagnostic']['task'], seed=case['diagnostic']['seed'],
            retrievals=len(retrievals), hits=sum(bool(e['matches']) for e in retrievals),
            retrieved_ids=sorted({i for e in retrievals for i in e['matches']}),
            retrieval_milliseconds=sum(e['elapsed_milliseconds'] for e in retrievals),
            retrieval_errors=sum(e['kind'] == 'experience_unavailable' for e in logs))
    # This scorer enforces all episodes closed and source-stable before truth reads.
    report = score_recovery(root)
    for row in report['rows']:
        row.update(audits[row['episode_id']])
    report.update(experience_corpus=protocol['experience_corpus'],
                  scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  recovery_scorer_sha256=report['scorer_sha256'],
                  limitations=['One new restack seed, fixed preparation and explicit sensor masking; not natural or end-to-end tasks',
                               'Same model/prompt/guards/budget; only historical hints vary; one case per condition',
                               'Corpus recognizes one predefined repair shape; not automatic general skill discovery',
                               'Retrieval is not proof the model causally used the experience; no statistical conclusion'])
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('root', type=Path)
    args = p.parse_args()
    report = score(args.root)
    (args.root / 'score.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps([dict(mode=r['mode'], physical=r['independent_physical']['status'],
                          public=r['public_at_decision'], hits=r['hits'], decisions=r['model_decisions'])
                      for r in report['rows']]))

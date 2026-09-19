"""Offline calibration against locally labelled real ads. Writes only to private/.

Labels are deliberately local: scripts and tests never embed a person's profile
or downloaded descriptions. This evaluates ordering, not hiring probability.
"""
from __future__ import annotations

import json
import hashlib
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_search.common import UTC, atomic_json, atomic_text, deduplicate, read_json
from local_search.pipeline import annotate, load_settings, select_run


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    config, rules = load_settings(ROOT)
    source = select_run(ROOT, sys.argv[1] if len(sys.argv)>1 else None)
    data = read_json(source/'ranked_jobs.json')
    manifest = data['manifest']
    jobs, merged = deduplicate(data['jobs'])
    ranked = annotate(jobs, config, rules,
                      when=datetime.fromisoformat(manifest.get('data_as_of', manifest['created_at'])).astimezone(UTC),
                      days=manifest.get('days',7))
    visible = [j for j in ranked if j['location_scope']!='outside' and j['date_scope']!='old' and j.get('availability',{}).get('status')!='closed']
    out = ROOT/'private/calibration'
    atomic_json(out/'ranked_candidate.json',ranked)
    lookup={j['job_id']:j for j in ranked}
    checks=[]
    labels=read_json(out/'labels.json',{'cases':[],'pairs':[]})
    for case in labels['cases']:
        j=lookup.get(case['job_id'])
        passed=bool(j and case.get('min',0)<=j['priority_score']<=case.get('max',100))
        checks.append({'passed':passed,'case':case,'actual_score':j['priority_score'] if j else None})
    for pair in labels['pairs']:
        a,b=lookup.get(pair['above']),lookup.get(pair['below'])
        checks.append({'passed':bool(a and b and a['priority_score']>b['priority_score']), 'pair':pair})
    metrics={'ranking_sha256':hashlib.sha256(json.dumps(rules,sort_keys=True).encode()).hexdigest(), 'profile_sha256':rules.get('profile_sha256'), 'source_run':source.name,'score_version':rules['version'],'total':len(ranked),'visible':len(visible),
             'merged':merged,'checks':checks,'passed':sum(c['passed'] for c in checks),'check_count':len(checks)}
    atomic_json(out/'validation.json',metrics)
    lines=['# Calibración local del ranking', '',f'Captura: {source.name}. Versión: {rules["version"]}.',
           '','No se realizaron consultas de red. Los juicios son una revisión orientativa del anuncio, no una validación con contrataciones.',
           '',f'{len(ranked)} ofertas; {len(visible)} visibles; {merged} duplicados de identidad consolidados.', '',
           '| Posición | Prioridad | Puesto | Empresa | Ajuste de requisitos |','|---|---|---|---|---|']
    for i,j in enumerate(visible[:50],1):
        def cell(v): return str(v).replace('|','/').replace('\n',' ')
        lines.append('| '+' | '.join(map(cell,[i,j['priority_score'],j['title'],j.get('company'),j['requirement_penalty']]))+' |')
        print(i,j['priority_score'],j['title'],'|',j.get('company'))
    atomic_text(out/'CALIBRACION.md','\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in metrics.items() if k!='checks'},ensure_ascii=False))
    return 1 if checks and not all(c['passed'] for c in checks) else 0


if __name__=='__main__':
    raise SystemExit(main())

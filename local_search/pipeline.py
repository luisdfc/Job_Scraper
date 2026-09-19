from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import time
from collections import Counter
from urllib.parse import urlsplit
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .collection import collect_indeed, collect_linkedin, read_checkpoints
from .common import UTC, atomic_json, date_scope, deduplicate, location_scope, now_iso, read_json
from .network import assess_availability, blocked, fetch_page
from .reports import build_index, write_report
from .scoring import evaluate
from .storage import Store


def load_settings(root: Path) -> tuple[dict, dict]:
    config = read_json(root/'config.json')
    rules = read_json(root/'private'/'ranking.json')
    if not isinstance(config,dict) or not isinstance(rules,dict):
        raise ValueError('config.json y private/ranking.json deben ser objetos JSON')
    if rules.get('profile_source'):
        profile = (root/rules['profile_source']).resolve()
        if not profile.is_relative_to((root/'private').resolve()):
            raise ValueError('El perfil de referencia debe estar dentro de private/')
        if hashlib.sha256(profile.read_bytes()).hexdigest() != rules.get('profile_sha256'):
            raise ValueError('El perfil de referencia ha cambiado. Revisa la calibración de private/ranking.json antes de clasificar nuevas ofertas.')
    for source in ('linkedin','indeed'):
        terms = config.get('search_terms',{}).get(source,[])
        if not isinstance(terms,list) or any(not isinstance(x,str) or not x.strip() for x in terms):
            raise ValueError(f'Las consultas de {source} deben ser textos no vacíos')
    for rule in rules.get('rules',[]):
        re.compile(rule['pattern'])
        for key in ('unless_title','unless_context'):
            if rule.get(key):
                re.compile(rule[key])
        if not isinstance(rule.get('weight'),(int,float)) or not isinstance(rule.get('label'),str):
            raise ValueError('Cada regla requiere label, pattern y weight numérico')
    if rules.get('financial_domain_pattern'):
        re.compile(rules['financial_domain_pattern'])
    for _, pattern in rules.get('categories',[]):
        re.compile(pattern)
    for cap in rules.get('priority_caps',[]):
        re.compile(cap['pattern'])
        if not 0 <= cap['limit'] <= 100:
            raise ValueError('Los límites de prioridad deben estar entre 0 y 100')
    for key in ('max_pages_per_query','query_timeout_seconds','request_timeout_seconds','linkedin_delay_seconds','indeed_delay_seconds'):
        value=config.get('collection',{}).get(key,0)
        if not isinstance(value,(int,float)) or value < 0:
            raise ValueError('collection.'+key+' debe ser un número no negativo')
    if config.get('local_ui',{}).get('page_size',50) < 1:
        raise ValueError('page_size debe ser positivo')
    return config,rules


def new_run(root: Path, config: dict, rules: dict, *, kind: str, days: int, prefix='') -> tuple[Path,dict]:
    try:
        zone=ZoneInfo(config.get('local_ui',{}).get('timezone','Europe/Madrid'))
    except ZoneInfoNotFoundError as e:
        raise RuntimeError('Falta tzdata para la zona horaria. Instala requirements.local.txt.') from e
    stamp=datetime.now(zone)
    ident=prefix+stamp.strftime('%Y-%m-%d_%H-%M-%S_%f')
    out=root/'runs'/ident
    out.mkdir(parents=True,exist_ok=False)
    manifest={'schema_version':2,'id':ident,'kind':kind,'status':'running','created_at':stamp.isoformat(timespec='seconds'),
              'timezone':str(zone),'days':days,'sources':[],
              'score_version':rules.get('version','custom'),'job_count':0,
              'config_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
              'ranking_sha256':hashlib.sha256(json.dumps(rules,sort_keys=True).encode()).hexdigest()}
    atomic_json(out/'config.snapshot.json',config)
    atomic_json(out/'ranking.snapshot.json',rules)
    atomic_json(out/'manifest.json',manifest)
    return out,manifest


def annotate(jobs: list[dict], config: dict, rules: dict, *, when: datetime, days: int) -> list[dict]:
    result=[]
    for job in jobs:
        job=copy.deepcopy(job)
        job['data_warnings'] = list(dict.fromkeys(job.get('data_warnings',[])))
        job['date_scope']=date_scope(job,when,days)
        job['location_scope']=location_scope(job,config)
        if job['date_scope']=='unknown':
            job.setdefault('data_warnings',[]).append('Fecha no verificable: se mantiene visible')
        job.setdefault('availability',{'status':'unknown','checked_at':'','evidence':'No se ha comprobado en esta instantánea'})
        result.append(evaluate(job,rules))
    return sorted(result,key=lambda j:(-j['priority_score'],-j['fit_score'],j.get('company',''),j.get('title','')))


def finalize(root: Path, out: Path, manifest: dict, jobs: list[dict], config: dict, *, latest=False) -> Path:
    closed=sum(j.get('availability',{}).get('status')=='closed' for j in jobs)
    old=sum(j.get('date_scope')=='old' for j in jobs)
    outside=sum(j.get('location_scope')=='outside' for j in jobs)
    counts={'total':len(jobs),'excluded':outside,'stale':old,'closed':closed,
            'missing_description':sum(len(j.get('description') or '')<150 for j in jobs)}
    manifest.update(job_count=len(jobs),new_count=sum(bool(j.get('is_new')) for j in jobs),
                    counts=counts,finished_at=now_iso())
    for source in manifest.get('sources', []):
        for query in source.get('queries', []):
            found=[j for j in jobs if query.get('term') in j.get('search_queries',[]) and source.get('source') in j.get('sources',[j.get('ats')])]
            query['retained_unique_jobs']=len(found)
            query['priority_60_plus']=sum(j['priority_score']>=60 and j.get('location_scope')!='outside' and j.get('date_scope')!='old' and j.get('availability',{}).get('status')!='closed' for j in found)
    atomic_json(out/'manifest.json',manifest)
    atomic_json(out/'queries.json',manifest.get('sources',[]))
    # Auditable filtered records; they also remain in ranked_jobs.json and HTML.
    outside_view=[{**j,'view_reasons':[reason for reason,yes in (
        ('Publicación fuera de ventana',j.get('date_scope')=='old'),
        ('Ubicación fuera del ámbito',j.get('location_scope')=='outside'),
        ('Cierre confirmado',j.get('availability',{}).get('status')=='closed')) if yes]} for j in jobs
        if j.get('date_scope')=='old' or j.get('location_scope')=='outside' or j.get('availability',{}).get('status')=='closed']
    atomic_json(out/'outside_default_view.json',outside_view)
    write_report(jobs,out,counts,manifest=manifest,page_size=config.get('local_ui',{}).get('page_size',50))
    atomic_json(out/'checksums.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file() and p.name!='checksums.json'})
    if latest:
        atomic_json(root/'private/latest_run.json',{'id':manifest['id']})
    build_index(root)
    print(f'Guardadas {len(jobs)} ofertas · {manifest["status"]} · {out/"shortlist.html"}',flush=True)
    return out/'shortlist.html'


def migrate_legacy(root: Path, config: dict, rules: dict) -> Path | None:
    marker=root/'private/migration_v2.json'
    if marker.exists():
        record=read_json(marker)
        return root/'runs'/record['id']/'shortlist.html' if record.get('id') else None
    paths=[root/'output/all_jobs.json',root/'output/linkedin_jobs.json',root/'output/indeed_jobs.json',
           root/'private/results/ranked_jobs.json',root/'private/results/excluded_jobs.json']
    raw=[]
    imported=[]
    for path in paths:
        if not path.exists():
            continue
        data=read_json(path)
        items=data.get('jobs',[]) if isinstance(data,dict) else data
        if not isinstance(items,list):
            raise ValueError('Formato histórico no reconocido: '+str(path))
        # Strip old decisions from the new score; original files remain unchanged.
        raw.extend({k:v for k,v in j.items() if k not in ('excluded_reason','fit_score','fit_reasons','fit_flags','fit_requirements','fit_band')} for j in items if isinstance(j,dict))
        imported.append({'path':str(path.relative_to(root)),'records':len(items),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    if not raw:
        # Do not prevent later migration if this is a new install without the old output folder.
        return None
    out,m=new_run(root,config,rules,kind='legacy',days=7,prefix='legacy_')
    m.update(status='legacy',imports=imported,notice='Acumulado anterior importado. Las fechas originales y los archivos originales se conservan. No es una búsqueda nueva ni permite reconstruir cada ejecución pasada.')
    unique,merged=deduplicate(raw)
    m['exact_duplicates_merged']=merged
    atomic_json(out/'raw_jobs.json',raw)
    jobs=Store(root).register(unique,m['id'])
    for j in jobs:
        j['is_new']=False
        j['availability']={'status':'unknown','checked_at':'','evidence':'Datos históricos; no se realizó comprobación de vigencia al importar'}
    jobs=annotate(jobs,config,rules,when=datetime.now(UTC),days=7)
    report=finalize(root,out,m,jobs,config)
    atomic_json(marker,{'id':m['id'],'imported_at':now_iso(),'sources':imported})
    return report


def enrich_and_verify(jobs: list[dict], config: dict, *, enabled: bool, fetcher=fetch_page, sleeper=time.sleep) -> dict:
    from scrape_jobs import _linkedin_description_from_page
    settings=config.get('verification',{})
    failures={}
    started=time.monotonic()
    skipped=0
    calls=0
    interrupted=False
    for index,job in enumerate(jobs):
        job['availability']={'status':'unknown','checked_at':'','evidence':'Comprobación pendiente'}
        source=job.get('ats','Unknown')
        if not enabled:
            job['availability']['evidence']='Comprobación desactivada para esta ejecución'
            continue
        url=job.get('direct_url') or job.get('url') or ''
        lid=re.search(r'/jobs/view/(?:[^/?]*-)?(\d+)',job.get('url',''))
        if source=='LinkedIn' and lid:
            url='https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/'+lid[1]
        if not url:
            job['availability']['evidence']='Oferta sin URL verificable'
            continue
        host=(urlsplit(url).hostname or '').lower()
        if failures.get(host,0)>=settings.get('stop_after_failures',3):
            job['availability']['evidence']='No verificable: se detuvo este dominio tras errores o bloqueos consecutivos: '+host
            skipped+=1
            continue
        try:
            sleeper(settings.get('delay_seconds',2))
            page=fetcher(url,timeout=settings.get('request_timeout_seconds',20),retries=0)
            calls+=1
            bad=bool(page.error or blocked(page) or page.status!=200)
            failures[host]=failures.get(host,0)+1 if bad else 0
            if source=='LinkedIn' and not bad:
                desc=_linkedin_description_from_page(page.text)
                if desc:
                    job['description']=desc
            job['availability']=assess_availability(job,page)
        except KeyboardInterrupt:
            interrupted=True
            for pending in jobs[index:]:
                pending['availability']={'status':'unknown','checked_at':'','evidence':'Comprobación interrumpida por el usuario'}
            break
        if calls and calls%25==0:
            print(f'Vigencia/descripciones: {index+1}/{len(jobs)} ofertas revisadas',flush=True)
    return {'requests':calls,'interrupted':interrupted,'skipped_after_domain_failures':skipped,
            'elapsed_seconds':round(time.monotonic()-started,2),
            'status_counts':dict(Counter(j.get('availability',{}).get('status') for j in jobs)),
            'evidence_counts':dict(Counter(j.get('availability',{}).get('evidence') for j in jobs)),
            'blocked_or_failed_sources':[s for s,n in failures.items() if n>=settings.get('stop_after_failures',3)],
            'unknown':sum(j.get('availability',{}).get('status')=='unknown' for j in jobs)}


def run_search(root: Path, config: dict, rules: dict, *, days=7, source='both', verify=True, probe=False) -> Path:
    migrate_legacy(root,config,rules)
    active=copy.deepcopy(config)
    if probe:
        for name in ('linkedin','indeed'):
            active.setdefault('search_terms',{})[name]=active.get('search_terms',{}).get(name,[])[:1]
        active.setdefault('collection',{}).update(max_pages_per_query=1,query_timeout_seconds=35,request_timeout_seconds=10)
        verify=False
    out,m=new_run(root,active,rules,kind='probe' if probe else 'scrape',days=days)
    collected=[]
    m['verification_enabled']=verify
    interrupted=False
    collectors=[('linkedin',collect_linkedin),('indeed',collect_indeed)]
    for name,collector in collectors:
        if source not in ('both',name):
            continue
        try:
            _,summary=collector(active,days,out/'raw',on_jobs=collected.extend)
            m['sources'].append(summary)
        except KeyboardInterrupt:
            interrupted=True
            progress=read_json(out/'raw'/f'{name}_progress.json',{})
            m['sources'].append(progress or {'source':name,'status':'partial','error':'Interrumpido','queries':[]})
            break
        except Exception as e:
            progress=read_json(out/'raw'/f'{name}_progress.json',{})
            m['sources'].append({**progress,'source':name,'status':'partial','error':type(e).__name__+': '+str(e)[:300]})
        atomic_json(out/'manifest.json',m)
    atomic_json(out/'raw_jobs.json',collected)
    unique,merged=deduplicate(collected)
    m['exact_duplicates_merged']=merged
    if interrupted:
        verification=enrich_and_verify(unique,active,enabled=False)
    else:
        verification=enrich_and_verify(unique,active,enabled=verify)
    interrupted=interrupted or verification['interrupted']
    m['verification']=verification
    m['verification_note']='Sin evidencia suficiente, bloqueo o fallo: No verificable y visible. Solo cierres confirmados se ocultan por defecto.'
    unique=Store(root).register(unique,m['id'],m['created_at'])
    unique, identity_merged=deduplicate(unique)
    m['registered_identity_duplicates_merged']=identity_merged
    reference=datetime.fromisoformat(m['created_at']).astimezone(UTC)
    jobs=annotate(unique,active,rules,when=reference,days=days)
    partial=any(s.get('status')!='completed' for s in m['sources']) or bool(verification['blocked_or_failed_sources'])
    m['status']='interrupted' if interrupted else 'partial' if partial and jobs else 'failed' if partial else 'completed'
    if probe:
        m['notice']='Prueba reducida: una consulta por fuente y una página como máximo. No sustituye la última búsqueda real ni mide su cobertura.'
    elif interrupted:
        m['notice']='Ejecución interrumpida. Se conservaron las páginas recuperadas; no se reutilizaron resultados anteriores.'
    return finalize(root,out,m,jobs,active,latest=not probe)


def list_runs(root: Path) -> list[dict]:
    rows=[]
    for path in sorted((root/'runs').glob('*/manifest.json'),reverse=True):
        row=read_json(path)
        rows.append(row)
    return rows


def select_run(root: Path, selector: str | None = None) -> Path:
    if not selector or selector=='latest':
        latest=read_json(root/'private/latest_run.json',{})
        if latest.get('id'):
            path=root/'runs'/latest['id']
            if (path/'shortlist.html').exists():
                return path
        runs=[m for m in list_runs(root) if (root/'runs'/m['id']/'shortlist.html').exists()]
        real=[m for m in runs if m.get('kind')=='scrape']
        legacy=[m for m in runs if m.get('kind')=='legacy']
        candidates=real or legacy
        if not candidates:
            raise ValueError('No hay informes guardados. Ejecuta migrate o weekly primero.')
        return root/'runs'/max(candidates,key=lambda m:m['created_at'])['id']
    if not re.fullmatch(r'[A-Za-z0-9_-]+',selector):
        raise ValueError('Identificador de ejecución no válido')
    exact=root/'runs'/selector
    if exact.is_dir():
        return exact
    choices=[m for m in list_runs(root) if m['id'].startswith(selector)]
    if len(choices)==1:
        return root/'runs'/choices[0]['id']
    if choices:
        raise ValueError('Hay varias ejecuciones para esa fecha. Usa el identificador completo: '+', '.join(m['id'] for m in choices))
    raise ValueError('No existe esa ejecución. Consulta job_search.py runs.')


def rerank(root: Path, config: dict, rules: dict, selector: str | None = None, *, make_latest=False) -> Path:
    previous=select_run(root,selector)
    data=read_json(previous/'ranked_jobs.json')
    old=data.get('manifest') or read_json(previous/'manifest.json')
    out,m=new_run(root,config,rules,kind='rerank',days=old.get('days',7),prefix='rerank_')
    m.update(status='reranked',parent_run=old['id'],data_as_of=old.get('data_as_of',old['created_at']),
             collection_status=old.get('collection_status',old['status']), sources=copy.deepcopy(old.get('sources',[])),
             verification=copy.deepcopy(old.get('verification',{})),
             notice='Ranking actualizado con ofertas de la captura original. No se ha vuelto a buscar ni comprobar la vigencia. Se conservan las limitaciones de cobertura de esa búsqueda.')
    atomic_json(out/'raw_jobs.json',data['jobs'])
    reference=datetime.fromisoformat(m['data_as_of']).astimezone(UTC)
    unique,merged=deduplicate(data['jobs'])
    m['registered_identity_duplicates_merged']=merged
    jobs=annotate(unique,config,rules,when=reference,days=m['days'])
    return finalize(root,out,m,jobs,config,latest=make_latest)


def recover(root: Path, config: dict, rules: dict, selector: str) -> Path:
    previous=select_run(root,selector)
    raw=[]
    for path in sorted((previous/'raw').glob('*.jsonl')):
        batch,_=read_checkpoints(path)
        raw.extend(batch)
    if not raw:
        raise ValueError('No hay páginas recuperables en raw/. No se importará el acumulado antiguo como sustituto.')
    prior=read_json(previous/'manifest.json')
    out,m=new_run(root,config,rules,kind='recovery',days=prior.get('days',7),prefix='recovery_')
    m.update(status='partial',parent_run=previous.name,data_as_of=prior.get('data_as_of',prior['created_at']),notice='Recuperación de páginas guardadas por una ejecución incompleta. Sin nueva búsqueda ni verificación de vigencia.')
    atomic_json(out/'raw_jobs.json',raw)
    unique,_=deduplicate(raw)
    jobs=Store(root).register(unique,m['id'])
    reference=datetime.fromisoformat(m['data_as_of']).astimezone(UTC)
    jobs=annotate(jobs,config,rules,when=reference,days=m['days'])
    return finalize(root,out,m,jobs,config)

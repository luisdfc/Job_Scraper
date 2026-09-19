from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from .common import atomic_json, now_iso, stable_id
from .network import blocked, fetch_page, page_text

GOOD_STOPS = {'exhausted', 'no_results'}


def append_checkpoint(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def source_summary(source: str, queries: list[dict], total: int) -> dict:
    problems = [q for q in queries if q.get('stop_reason') not in GOOD_STOPS]
    return {'source': source, 'queries_planned': total, 'queries_attempted': len(queries),
            'queries_finished': sum(q.get('stop_reason') in GOOD_STOPS for q in queries),
            'raw_records': sum(q.get('raw_count', 0) for q in queries),
            'status': 'partial' if problems or len(queries) < total else 'completed',
            'queries': queries,
            'coverage_note': 'Solo consultas realizadas y resultados entregados por la fuente; no mide todas las vacantes del mercado.'}


def collect_linkedin(config: dict, days: int, raw_dir: Path, *, fetcher=fetch_page,
                     sleeper=time.sleep, on_jobs=None) -> tuple[list[dict], dict]:
    from scrape_jobs import _parse_linkedin_cards
    settings = config.get('collection', {})
    terms = config.get('search_terms', {}).get('linkedin', [])
    geos = config.get('locations', {}).get('linkedin', [])
    all_jobs, stats = [], []
    source_ids = set()
    consecutive_failures = 0
    stop_source = False
    for geo in geos:
        if stop_source:
            break
        for term in terms:
            q = {'term': term, 'location': geo.get('location', ''), 'pages': 0, 'raw_count': 0,
                 'started_at': now_iso(), 'stop_reason': 'interrupted'}
            seen = set()
            offset = 0
            started = time.monotonic()
            try:
                while True:
                    max_pages = settings.get('max_pages_per_query', 0)
                    if max_pages and q['pages'] >= max_pages:
                        q['stop_reason'] = 'safety_page_limit'
                        break
                    budget = settings.get('query_timeout_seconds', 240)
                    if budget and time.monotonic() - started >= budget:
                        q['stop_reason'] = 'query_timeout'
                        break
                    sleeper(settings.get('linkedin_delay_seconds', 3))
                    params = {'keywords': term, 'location': geo.get('location', ''), 'f_TPR': f'r{days*86400}', 'start': offset}
                    if geo.get('geoId'):
                        params['geoId'] = geo['geoId']
                    url = 'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?' + urlencode(params)
                    page = fetcher(url, timeout=settings.get('request_timeout_seconds', 20), retries=1)
                    if blocked(page) or page.error or page.status != 200:
                        q.update(stop_reason='blocked' if blocked(page) else 'network_error', error=page.error or f'HTTP {page.status}', http_status=page.status)
                        q['terminal_response'] = terminal_evidence(page, raw_dir, len(stats))
                        consecutive_failures += 1
                        break
                    consecutive_failures = 0
                    parsed, count = _parse_linkedin_cards(page.text)
                    q['pages'] += 1
                    q['raw_count'] += count
                    batch = []
                    for card in parsed:
                        j = {**card, 'url': f'https://www.linkedin.com/jobs/view/{card["id"]}/', 'ats': 'LinkedIn',
                             'search_queries': [term], 'search_location': geo.get('location', ''), 'collected_at': now_iso()}
                        # No title, employer, score, education or geographic filter at ingestion.
                        batch.append(j)
                    append_checkpoint(raw_dir / 'linkedin_pages.jsonl', {'query': term, 'offset': offset, 'url': url, 'jobs': batch})
                    all_jobs.extend(batch)
                    if on_jobs and batch:
                        on_jobs(batch)
                    if not count:
                        explicit_empty = bool(re.search(r'no matching jobs|no results|no jobs found|no se han encontrado', page.text, re.I))
                        q['stop_reason'] = ('exhausted' if seen else 'no_results') if (not page.text.strip() or explicit_empty) else 'parser_or_empty_unconfirmed'
                        # A first blank page can also be a soft block: do not assert absence of vacancies.
                        if not seen and not page.text.strip():
                            q['stop_reason'] = 'empty_unconfirmed'
                        q['terminal_response'] = terminal_evidence(page, raw_dir, len(stats))
                        break
                    new_ids = {c['id'] for c in parsed} - seen
                    if not new_ids:
                        q['stop_reason'] = 'repeated_page'
                        break
                    seen.update(new_ids)
                    offset += 10  # never skip unfiltered cards; duplicated overlaps are preserved then deduped
            finally:
                q['finished_at'] = now_iso()
                q['elapsed_seconds'] = round(time.monotonic()-started, 2)
                q['unique_count'] = len(seen)
                q['new_to_source'] = len(seen-source_ids)
                source_ids.update(seen)
                stats.append(q)
                atomic_json(raw_dir / 'linkedin_progress.json', source_summary('LinkedIn', stats, len(terms)*len(geos)))
            print(f'LinkedIn [{len(stats)}/{len(terms)*len(geos)}] {term}: {q["raw_count"]} tarjetas · {q["stop_reason"]}', flush=True)
            if consecutive_failures >= settings.get('stop_after_source_failures', 3):
                stop_source = True
                break
    return all_jobs, source_summary('LinkedIn', stats, len(terms)*len(geos))


def terminal_evidence(page, raw_dir: Path, query_index: int) -> dict:
    """Keep a bounded terminal response for diagnosing parser drift/soft blocks locally."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    name = f'linkedin_terminal_{query_index:04d}.txt'
    (raw_dir/name).write_text(page.text[:100_000], encoding='utf-8')
    return {'http_status':page.status, 'url':page.url, 'bytes':len(page.text.encode('utf-8')),
            'sha256':hashlib.sha256(page.text.encode('utf-8')).hexdigest(),
            'text_excerpt':page_text(page.text)[:500], 'saved_as':name,
            'truncated':len(page.text)>100_000}


def read_checkpoints(path: Path) -> tuple[list[dict], list[dict]]:
    jobs, events = [], []
    if not path.exists():
        return jobs, events
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # only an incomplete final line after an interrupted process
        events.append(event)
        jobs.extend(event.get('jobs', []))
    return jobs, events


def collect_indeed(config: dict, days: int, raw_dir: Path, *, on_jobs=None) -> tuple[list[dict], dict]:
    settings = config.get('collection', {})
    terms = config.get('search_terms', {}).get('indeed', [])
    geos = config.get('locations', {}).get('indeed', [])
    jobs, stats = [], []
    consecutive_failures = 0
    stop_source = False
    raw_dir.mkdir(parents=True, exist_ok=True)
    for geo in geos:
        if stop_source:
            break
        for term in terms:
            q = {'term': term, 'location': geo.get('location', ''), 'pages': 0, 'raw_count': 0, 'started_at': now_iso()}
            serial = f'{len(stats):04d}'
            checkpoint = raw_dir / f'indeed_{serial}.jsonl'
            task = raw_dir / f'indeed_{serial}_task.json'
            log = raw_dir / f'indeed_{serial}.log'
            atomic_json(task, {'term': term, 'geo': geo, 'days': days, 'settings': settings})
            time.sleep(settings.get('indeed_delay_seconds', 1))
            cmd = [sys.executable, '-m', 'local_search.indeed_worker', str(task.resolve()), str(checkpoint.resolve())]
            proc = None
            interrupted = False
            try:
                with log.open('w', encoding='utf-8') as stream:
                    proc = subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent.parent, stdout=stream, stderr=stream)
                    try:
                        code = proc.wait(timeout=settings.get('query_timeout_seconds', 240) or None)
                        q['exit_code'] = code
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                        q['stop_reason'] = 'query_timeout'
            except KeyboardInterrupt:
                interrupted = True
                q['stop_reason'] = 'interrupted'
                if proc and proc.poll() is None:
                    proc.kill()
                    proc.wait()
            except OSError as e:
                q.update(stop_reason='worker_error', error=str(e)[:250])
            batch, events = read_checkpoints(checkpoint)
            jobs.extend(batch)
            if on_jobs and batch:
                on_jobs(batch)
            q['pages'] = sum(e.get('type') == 'page' for e in events)
            q['raw_count'] = len(batch)
            terminals = [e for e in events if e.get('type') == 'end']
            if 'stop_reason' not in q:
                q.update(terminals[-1] if terminals else {'stop_reason': 'worker_error', 'error': 'Sin diagnóstico final; consultar el log de esta consulta'})
                q.pop('type', None)
            q['finished_at'] = now_iso()
            stats.append(q)
            atomic_json(raw_dir / 'indeed_progress.json', source_summary('Indeed', stats, len(terms)*len(geos)))
            print(f'Indeed [{len(stats)}/{len(terms)*len(geos)}] {term}: {q["raw_count"]} registros · {q["stop_reason"]}', flush=True)
            if interrupted:
                raise KeyboardInterrupt
            if q['stop_reason'] in ('blocked', 'network_error', 'library_missing', 'library_incompatible', 'worker_error'):
                consecutive_failures += 1
            else:
                consecutive_failures = 0
            if consecutive_failures >= settings.get('stop_after_source_failures', 3) or q['stop_reason'] in ('library_missing', 'library_incompatible'):
                stop_source = True
                break
    return jobs, source_summary('Indeed', stats, len(terms)*len(geos))

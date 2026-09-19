"""One isolated JobSpy query; checkpoints each delivered page before asking for the next.

The checkpoint hook targets python-jobspy 1.1.82's Indeed._scrape_page contract.
Unsupported adapters fail visibly. Parent process enforces a hard timeout even if
an underlying HTTP client hangs. It never falls back to a previous search.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .collection import append_checkpoint
from .common import now_iso, read_json


class StopQuery(Exception):
    pass


def model_job(post, term: str, location: str) -> dict:
    """Read JobSpy JobPost objects without its title or relevance filters."""
    from scrape_jobs import format_salary
    data = post.model_dump() if hasattr(post, 'model_dump') else post.dict()
    loc = data.get('location') or {}
    country = loc.get('country')
    country = getattr(country, 'name', country)
    place = ', '.join(str(x) for x in (loc.get('city'), loc.get('state'), country) if x)
    compensation = data.get('compensation') or {}
    interval = getattr(compensation.get('interval'), 'value', compensation.get('interval')) or ''
    minimum, maximum = compensation.get('min_amount'), compensation.get('max_amount')
    types = []
    for jt in data.get('job_type') or []:
        value = getattr(jt, 'value', jt)
        types.append(str(value[0] if isinstance(value, (list, tuple)) else value))
    return {'title': data.get('title') or '', 'company': data.get('company_name') or 'Unknown',
            'location': place, 'url': data.get('job_url') or '', 'direct_url': data.get('job_url_direct') or '',
            'date_posted': str(data.get('date_posted') or ''), 'description': data.get('description') or '',
            'salary': format_salary(minimum, maximum, interval, compensation.get('currency') or ''),
            'salary_min': minimum, 'salary_max': maximum, 'salary_interval': interval,
            'salary_currency': compensation.get('currency') or '', 'salary_source': 'direct_data' if compensation else '',
            'job_type': ', '.join(types), 'is_remote': data.get('is_remote'), 'ats': 'Indeed',
            'company_industry': data.get('company_industry') or '',
            'search_queries': [term], 'search_location': location, 'collected_at': now_iso()}


def install_hook(cls, task: dict, checkpoint: Path, converter=model_job):
    original = cls._scrape_page
    state = {'pages': 0, 'seen': set(), 'cursors': set(), 'terminal': ''}
    max_pages = task['settings'].get('max_pages_per_query', 0)

    def finish(reason: str, error: str = ''):
        if not state['terminal']:
            append_checkpoint(checkpoint, {'type': 'end', 'stop_reason': reason, 'error': error})
            state['terminal'] = reason
        raise StopQuery(reason)

    def page(self, cursor):
        if max_pages and state['pages'] >= max_pages:
            finish('safety_page_limit')
        if cursor is not None and cursor in state['cursors']:
            finish('repeated_cursor')
        if cursor is not None:
            state['cursors'].add(cursor)
        # JobSpy's adapter can convert bad HTTP responses to []: make that observable.
        previous_post = self.session.post
        def checked_post(*args, **kwargs):
            kwargs['verify'] = True  # do not inherit the legacy adapter's verify=False
            kwargs['timeout'] = task['settings'].get('request_timeout_seconds', 20)
            response = previous_post(*args, **kwargs)
            if not response.ok:
                code = response.status_code
                finish('blocked' if code in (401, 403, 429, 999) else 'network_error', f'HTTP {code}')
            try:
                payload = response.json()
                if payload.get('errors') or not isinstance(payload.get('data', {}).get('jobSearch'), dict):
                    finish('schema_error', 'Respuesta GraphQL sin un resultado de búsqueda válido')
            except (ValueError, AttributeError):
                finish('schema_error', 'Respuesta no JSON o esquema cambiado')
            return response
        self.session.post = checked_post
        try:
            posts, next_cursor = original(self, cursor)
        finally:
            self.session.post = previous_post
        batch = [converter(p, task['term'], task['geo'].get('location', '')) for p in posts]
        state['pages'] += 1
        append_checkpoint(checkpoint, {'type': 'page', 'number': state['pages'], 'jobs': batch, 'has_next_cursor': bool(next_cursor)})
        if not batch:
            finish('exhausted' if state['seen'] else 'no_results')
        ids = {j.get('url') for j in batch if j.get('url')}
        if not (ids - state['seen']):
            finish('repeated_page')
        state['seen'].update(ids)
        if not next_cursor:
            finish('exhausted')
        time.sleep(task['settings'].get('indeed_delay_seconds', 1))
        return posts, next_cursor
    cls._scrape_page = page
    return state


def main() -> int:
    task, checkpoint = read_json(Path(sys.argv[1])), Path(sys.argv[2])
    try:
        from jobspy import scrape_jobs
        from jobspy.indeed import Indeed
        from importlib.metadata import version
    except ImportError as e:
        append_checkpoint(checkpoint, {'type': 'end', 'stop_reason': 'library_missing', 'error': str(e)})
        return 2
    if not hasattr(Indeed, '_scrape_page'):
        append_checkpoint(checkpoint, {'type': 'end', 'stop_reason': 'library_incompatible', 'error': 'La versión de JobSpy cambió el adaptador de Indeed'})
        return 2
    append_checkpoint(checkpoint, {'type': 'adapter', 'version': version('python-jobspy')})
    state = install_hook(Indeed, task, checkpoint)
    try:
        # No app-level result truncation. The adapter follows the source cursor to exhaustion.
        scrape_jobs(site_name=['indeed'], search_term=task['term'], location=task['geo'].get('location', ''),
                    country_indeed=task['geo'].get('country', 'Spain'), hours_old=task['days'] * 24,
                    results_wanted=2_147_483_647, distance=task['settings'].get('indeed_radius_miles', 35),
                    enforce_annual_salary=False, verbose=0)
    except StopQuery:
        return 0
    except Exception as e:
        if not state['terminal']:
            append_checkpoint(checkpoint, {'type': 'end', 'stop_reason': 'network_error', 'error': type(e).__name__ + ': ' + str(e)[:300]})
        return 1
    if not state['terminal']:
        append_checkpoint(checkpoint, {'type': 'end', 'stop_reason': 'adapter_ended_unconfirmed', 'error': 'El adaptador terminó sin confirmar agotamiento del cursor'})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

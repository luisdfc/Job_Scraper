from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

UTC = timezone.utc

def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def normalize(value: object) -> str:
    return _normalize_text(str(value or ''))


@lru_cache(maxsize=4096)
def _normalize_text(value: str) -> str:
    return re.sub(r'[\u0300-\u036f]', '', unicodedata.normalize('NFKD', value.lower()))


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def atomic_json(path: Path, value) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def safe_url(value: object) -> str:
    value = str(value or '').strip()
    try:
        p = urlsplit(value)
        if p.scheme.lower() in ('http', 'https') and p.hostname and not p.username and not p.password:
            return value
    except ValueError:
        pass
    return ''


def canonical_url(value: object) -> str:
    url = safe_url(value)
    if not url:
        return ''
    p = urlsplit(url)
    host = (p.hostname or '').lower()
    q = parse_qs(p.query)
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        m = re.search(r'/jobs/(?:view|api/jobPosting)/(?:[^/?]*-)?(\d+)', p.path)
        if not m:
            m = re.search(r'jobPosting/(\d+)', p.path)
        if m:
            return 'https://www.linkedin.com/jobs/view/' + m.group(1)
    if host == 'indeed.com' or host.endswith('.indeed.com'):
        key = (q.get('jk') or q.get('vjk') or [''])[0]
        if key:
            return 'https://www.indeed.com/viewjob?jk=' + key
    # Keep substantive requisition parameters, discard tracking only. Distinct IDs stay distinct.
    tracking = {'trk', 'trackingid', 'refid', 'source', 'sourceid', 'referrer', 'from', 'campaign', 'gh_src'}
    clean = [(k, v) for k, vs in q.items() for v in vs
             if not k.lower().startswith('utm_') and k.lower() not in tracking]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/') or '/', urlencode(sorted(clean)), ''))


def identity_aliases(job: dict) -> list[str]:
    values = [job.get('url'), job.get('direct_url'), *(job.get('duplicate_urls') or [])]
    # A generic careers page must not join unrelated requisitions.
    aliases = []
    for value in values:
        canon = canonical_url(value)
        if canon:
            p = urlsplit(canon)
            if value == job.get('direct_url') and p.path.rstrip('/') in ('', '/jobs', '/careers', '/search') and not p.query:
                continue
            aliases.append(canon)
    if not aliases:
        raw = '|'.join(normalize(job.get(k)) for k in ('ats', 'company', 'title', 'location', 'date_posted'))
        aliases.append('missing-url:' + hashlib.sha256(raw.encode()).hexdigest())
    return sorted(set(aliases))


def stable_id(job: dict) -> str:
    # Prefer the source posting over a redirect to a generic employer URL.
    alias = canonical_url(job.get('url')) or identity_aliases(job)[0]
    return 'job_' + hashlib.sha256(alias.encode()).hexdigest()[:24]


def parse_date(value: object, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(UTC)
    raw = str(value or '').strip()
    t = normalize(raw)
    if t in ('today', 'just posted', 'just now', 'hoy', 'ahora', 'posted today'):
        return now
    if t in ('yesterday', 'ayer'):
        return now - timedelta(days=1)
    m = re.search(r'(\d+)\s*(minutes?|mins?|minutos?|hours?|hrs?|horas?|days?|dias?|weeks?|semanas?|months?|meses?)\b', t)
    if m:
        n, unit = int(m[1]), m[2]
        seconds = 60 if unit.startswith(('min',)) else 3600 if unit.startswith(('hour', 'hr', 'hora')) else 86400 if unit.startswith(('day', 'dia')) else 604800 if unit.startswith(('week', 'semana')) else 2592000
        return now - timedelta(seconds=n * seconds)
    try:
        d = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)
    except ValueError:
        return None


def date_scope(job: dict, now: datetime, days: int) -> str:
    posted = parse_date(job.get('date_posted'), now)
    if not posted or posted > now + timedelta(days=1):
        return 'unknown'
    # A board's date-only value has no posting hour: keep the entire boundary day.
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(job.get('date_posted', ''))):
        return 'recent' if posted.date() >= (now - timedelta(days=days)).date() else 'old'
    return 'recent' if posted >= now - timedelta(days=days) else 'old'


@lru_cache(maxsize=1)
def madrid_municipalities() -> tuple[str, ...]:
    names = read_json(Path(__file__).parent/'data/madrid_municipalities.json')['municipalities']
    aliases = set()
    for name in names:
        name = normalize(name)
        match = re.fullmatch(r'(.+) \((el|la|los|las)\)', name)
        aliases.add(name if not match else match[2]+' '+match[1])
        if match:
            aliases.add(match[1])
    return tuple(sorted(aliases, key=len, reverse=True))


def location_scope(job: dict, config: dict) -> str:
    loc = normalize(job.get('location'))
    if not loc or loc in ('unknown', 'remote', 'remoto', 'spain', 'espana', 'es'):
        return 'unknown'
    terms = config.get('location_filter', {}).get('terms', [])
    if not terms:
        return 'target'
    if re.search(r',\s*(?:us|usa|united states|uk|united kingdom|germany|france|portugal)\s*$', loc):
        return 'outside'
    if any(re.search(r'(?<!\w)'+re.escape(normalize(term))+r'(?!\w)', loc) for term in terms if term):
        return 'target'
    # Indeed's Spanish region code. Do not match Maryland (MD, US).
    if re.search(r'\bmd\s*,\s*(?:es|spain|espana)\b', loc):
        return 'target'
    if any('madrid' in normalize(t) for t in terms):
        if any(re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)', loc) for name in madrid_municipalities()):
            return 'target'
        # Only a positive outside signal hides a location. Unknown villages stay visible.
        outside = r'barcelona|catalu[nñ]a|catalonia|valencia|sevilla(?! la nueva)|andalucia|bilbao|vizcaya|malaga|zaragoza|alicante|toledo|illescas|ontigola|sesena|noblejas|alcabon|tarancon|cuenca|segovia|guadalajara|azuqueca de henares|marchamalo|lisbon|lisboa|london|londres|paris|luxembourg|luxemburgo|berlin|munich|\bct, es\b'
        return 'outside' if re.search(r'\b(?:'+outside+r')\b',loc) else 'unknown'
    return 'unknown'


def deduplicate(jobs: list[dict]) -> tuple[list[dict], int]:
    """Merge exact posting URLs/IDs only. Similar titles never remove vacancies."""
    groups: list[dict] = []
    index: dict[str, dict] = {}
    for original in jobs:
        job = dict(original)
        aliases = identity_aliases(job)
        if job.get('job_id'):
            aliases.append('registered:'+job['job_id'])
        matches = []
        for a in aliases:
            if a in index and all(index[a] is not x for x in matches):
                matches.append(index[a])
        if not matches:
            job['duplicate_urls'] = list(dict.fromkeys(job.get('duplicate_urls', [])))
            job['search_queries'] = list(dict.fromkeys(job.get('search_queries', [])))
            job['sources'] = list(dict.fromkeys(job.get('sources', []) or [job.get('ats', 'Unknown')]))
            groups.append(job)
            target = job
        else:
            target = matches[0]
            for other in [job, *matches[1:]]:
                target['duplicate_urls'] = sorted(set(target.get('duplicate_urls', []) + other.get('duplicate_urls', []) + [u for u in (other.get('url'), other.get('direct_url')) if u and u != target.get('url')]))
                target['search_queries'] = list(dict.fromkeys(target.get('search_queries', []) + other.get('search_queries', [])))
                target['sources'] = list(dict.fromkeys(target.get('sources', []) + (other.get('sources') or [other.get('ats', 'Unknown')])))
                if len(other.get('description') or '') > len(target.get('description') or ''):
                    target['description'] = other['description']
                for key, value in other.items():
                    if not target.get(key) and value:
                        target[key] = value
                if other.get('is_new'):
                    target['is_new'] = True
                a, b = target.get('availability', {}), other.get('availability', {})
                if a.get('status') and b.get('status') and a['status'] != b['status']:
                    target['availability'] = {'status':'unknown', 'checked_at':max(a.get('checked_at',''), b.get('checked_at','')),
                                              'evidence':'Señales de vigencia distintas entre enlaces de la misma oferta; confirmar en la fuente'}
                if any(other is g for g in groups) and other is not target:
                    groups = [g for g in groups if g is not other]
                for a, g in list(index.items()):
                    if g is other:
                        index[a] = target
        for a in identity_aliases(target) + aliases:
            index[a] = target
    # Similar-but-unproven duplicates remain visible and are only flagged.
    potential: dict[str, list[dict]] = {}
    for j in groups:
        company = re.sub(r'\b(?:espana|spain|latam|europe|portugal)\b', '', normalize(j.get('company')))
        company = re.sub(r'[^a-z0-9]+', '', company)
        key = company+'|'+normalize(j.get('title'))+'|'+normalize(j.get('location'))
        potential.setdefault(key, []).append(j)
    for group in potential.values():
        if len(group) > 1:
            for j in group:
                j['possible_duplicate_count'] = len(group)
    return groups, len(jobs) - len(groups)


from contextlib import contextmanager

@contextmanager
def run_lock(root: Path):
    """OS advisory lock: released automatically after a crash; no stale PID deletion."""
    path = root / 'private' / 'search.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        if path.stat().st_size == 0:
            f.write(b'0')
            f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise RuntimeError('Ya hay una búsqueda o migración en ejecución en esta carpeta.') from e
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

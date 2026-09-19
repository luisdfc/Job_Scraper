from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .common import UTC, normalize, now_iso, parse_date, safe_url


@dataclass
class Page:
    url: str
    status: int = 0
    text: str = ''
    error: str = ''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_url(url: str) -> bool:
    """Do not follow scraped links to localhost, LAN or non-web protocols."""
    if not safe_url(url):
        return False
    host = urlsplit(url).hostname
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(x[4][0]).is_global for x in addresses)
    except (OSError, ValueError):
        return False


def fetch_page(url: str, timeout: float = 20, retries: int = 1, delay: float = 2) -> Page:
    opener = urllib.request.build_opener(NoRedirect())
    for redirects in range(5):
        if not public_url(url):
            return Page(url, error='URL no pública, DNS no resoluble o esquema no permitido')
        response = None
        for attempt in range(retries + 1):
            request = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36',
                'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8', 'Accept': 'text/html,application/xhtml+xml'})
            try:
                response = opener.open(request, timeout=timeout)
                break
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    response = e
                    break
                if e.code >= 500 and attempt < retries:
                    e.close()
                    time.sleep(delay * (attempt + 1))
                    continue
                text = e.read(2_000_000).decode('utf-8', errors='replace')
                e.close()
                # Never retry access challenges or use proxies to bypass them.
                return Page(url, e.code, text, f'HTTP {e.code}')
            except (OSError, TimeoutError, ValueError) as e:
                if attempt < retries:
                    time.sleep(delay * (attempt + 1))
                    continue
                return Page(url, error=f'{type(e).__name__}: {str(e)[:200]}')
        if response is None:
            return Page(url, error='Sin respuesta')
        with response:
            if response.code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get('Location', ''))
                continue
            content = response.read(2_000_001)
            if len(content) > 2_000_000:
                return Page(url, response.code, error='Respuesta excede el tamaño de seguridad')
            return Page(url, response.code, content.decode('utf-8', errors='replace'))
    return Page(url, error='Demasiadas redirecciones')


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def page_text(page: str) -> str:
    p = TextParser()
    p.feed(page)
    return re.sub(r'\s+', ' ', ' '.join(p.text)).strip()


def blocked(page: Page) -> bool:
    if page.status in (401, 403, 429, 999):
        return True
    # Match challenge phrases, not a captcha library loaded on an otherwise valid page.
    return bool(re.search(r'please verify you are a human|verify you are human|unusual traffic|access denied|security verification|sign in to view this job|inicia sesion para ver|verifica que eres humano|checking your browser|request blocked', normalize(page_text(page.text))))


def job_postings(html: str) -> list[dict]:
    output = []
    def walk(obj):
        if isinstance(obj, list):
            for x in obj:
                walk(x)
        elif isinstance(obj, dict):
            kind = obj.get('@type', [])
            if kind == 'JobPosting' or isinstance(kind, list) and 'JobPosting' in kind:
                output.append(obj)
            for key in ('@graph', 'mainEntity'):
                if key in obj:
                    walk(obj[key])
    for block in re.findall(r'<script[^>]+type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        try:
            walk(json.loads(block))
        except (ValueError, TypeError):
            continue
    return output


def application_control(html: str) -> bool:
    """An enabled action on a matching posting, not an 'apply' word in its prose."""
    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.active=[]
            self.found=False

        def handle_starttag(self, tag, attrs):
            if tag not in ('a','button'):
                return
            attrs=dict(attrs)
            href=attrs.get('href','')
            enabled=not any(key in attrs for key in ('disabled','hidden')) and attrs.get('aria-disabled')!='true'
            actionable=tag=='button' or bool(href and not href.startswith('#') and safe_url(urljoin('https://example.org/',href)))
            self.active.append({'tag':tag,'text':[],'enabled':enabled and actionable})

        def handle_data(self, text):
            for item in self.active:
                item['text'].append(text)

        def handle_endtag(self, tag):
            if not self.active or self.active[-1]['tag']!=tag:
                return
            item=self.active.pop()
            label=normalize(' '.join(' '.join(item['text']).split()))
            if item['enabled'] and re.fullmatch(r'apply(?: now| for this job| on company website)?|solicitar(?: ahora| este empleo| en el sitio web de la empresa)?|inscribirme|presentar candidatura|postularme',label):
                self.found=True
    parser=Controls()
    parser.feed(html)
    return parser.found


def assess_availability(job: dict, page: Page, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    result = {'status': 'unknown', 'checked_at': now.isoformat(timespec='seconds'),
              'url': page.url, 'http_status': page.status, 'evidence': ''}
    if blocked(page):
        result['evidence'] = 'Comprobación bloqueada: no demuestra que la oferta esté cerrada'
        return result
    if page.error or page.status != 200:
        result['evidence'] = page.error or f'HTTP {page.status}; no confirma un cierre'
        return result
    text = page_text(page.text)
    t = normalize(text)
    title = normalize(job.get('title')).strip()
    # Only the matching JobPosting can establish expiry; ignore related vacancies.
    matching = [p for p in job_postings(page.text) if normalize(p.get('title')).strip() == title and title]
    for p in matching:
        expiry = parse_date(p.get('validThrough'), now)
        if expiry and expiry < now:
            result.update(status='closed', evidence='El JobPosting de esta oferta declara validThrough=' + str(p['validThrough']))
            return result
    close = re.search(r'no longer accepting applications|this job (?:has expired|is no longer available|is no longer accepting applications)|this (?:position|vacancy) (?:has been filled|is closed)|ya no se aceptan solicitudes|ya no acepta solicitudes|esta oferta (?:ha caducado|ha expirado|ya no esta disponible)|puesto (?:ya )?cubierto', t)
    # Require a recognizable job page, not a generic search page with related closed jobs.
    same_title = bool(title and title in t)
    related = bool(re.search(r'class=["\'][^"\']*(?:similar-jobs|recommended-jobs|related-jobs)', page.text, re.I))
    if close and same_title and not related:
        result.update(status='closed', evidence=text[max(0, close.start()-60):close.end()+100][:350])
        return result
    if matching:
        result.update(status='open', evidence='Ficha JobPosting coincidente accesible, sin cierre explícito; no garantiza disponibilidad al solicitar')
    elif same_title and application_control(page.text):
        result.update(status='open', evidence='Título coincidente y opción de solicitud visibles; confirmar al solicitar')
    else:
        result['evidence'] = 'Página accesible sin evidencia suficiente de apertura o cierre'
    return result

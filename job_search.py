"""Local search and explainable ranking. No profile uploads or paid services."""
import argparse
import html
import json
import re
import unicodedata
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').lower())
                   if not unicodedata.combining(c))



def salary_signal(job, policy):
    """Compare only explicit annual EUR amounts; unknown compensation stays neutral."""
    raw = str(job.get('salary') or '').strip()
    currency = str(job.get('salary_currency') or '').upper()
    if currency == 'EUR':
        raw = raw.replace('$', '€')  # Legacy JobSpy formatter used dollars for all currencies.
    if not raw:
        for line in str(job.get('description') or '').splitlines():
            text = normalize(line)
            if re.search(r'salary|salario|retribucion|remuneracion', text) and re.search(r'€|eur', text) and re.search(r'\d', text):
                raw = line.strip()[:500]
                break
    if not raw:
        return 'No se especifica', 0, ''
    text = normalize(raw)
    annual = str(job.get('salary_interval') or '').lower() == 'yearly' or bool(re.search(r'/yr|year|annual|anual|al ano|/ano', text))
    euro = currency == 'EUR' or (not currency and ('€' in text or 'eur' in text))
    if not annual or not euro or re.search(r'neto|net salary|\bote\b|on.target|total compensation|bonus|variable', text):
        return raw, 0, 'Salario sin comparación automática: confirmar moneda, periodicidad y fijo bruto'
    values = []
    for key in ('salary_min', 'salary_max'):
        try:
            value = float(job.get(key) or 0)
            if value > 0:
                values.append(value)
        except (ValueError, TypeError):
            pass
    if not values:
        for amount, k in re.findall(r'(\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(k?)', text):
            number = re.sub(r'[.,](?=\d{3}(?:\D|$))', '', amount).replace(',', '.')
            values.append(float(number) * (1000 if k else 1))
    if not values:
        return raw, 0, 'Importe no interpretable: revisar oferta'
    if 'estimated' in normalize(job.get('salary_source')) or 'estimad' in text:
        return raw, 0, 'Salario estimado: confirmar con la empresa'
    minimum = policy.get('minimum_eur', 0)
    target = policy.get('target_eur', minimum)
    if max(values) < minimum:
        return raw, -20, 'Banda publicada por debajo del mínimo orientativo; confirmar fijo bruto'
    if min(values) < minimum:
        return raw, -5, 'Banda salarial cruza el mínimo: negociar y confirmar fijo bruto'
    if target and min(values) >= target:
        return raw, 4, 'Banda publicada compatible con el objetivo; confirmar fijo bruto'
    return raw, 0, 'Banda publicada compatible con el mínimo; confirmar fijo bruto'


def evaluate(job, rules):
    """Each rule scores at most once; task signals are evidence, not certainty."""
    title = normalize(job.get('title'))
    description = normalize(job.get('description'))
    score = rules.get('base', 35)
    reasons, flags = [], []
    for rule in rules['rules']:
        source = title if rule.get('field') == 'title' else title + ' ' + description
        if re.search(rule['pattern'], source, re.I):
            score += rule['weight']
            (reasons if rule['weight'] > 0 else flags).append(rule['label'])
    salary, salary_weight, salary_note = salary_signal(job, rules.get('salary', {}))
    score += salary_weight
    if salary_note:
        (reasons if salary_weight > 0 else flags).append(salary_note)
    schedule = 'Horario real no verificado; confirmar horas habituales y picos con el equipo'
    signals = []
    for line in str(job.get('description') or '').splitlines():
        text = normalize(line)
        if re.search(r'long hours|late nights|work (?:on )?weekends|evening and weekend|horas extra|fuera (?:del|de) horario|disponibilidad (?:total|24)|trabaj.{0,25}(?:noches|fines de semana)', text):
            if not re.search(r'no overtime|no (?:se requiere[n]? )?horas extra|sin horas extra|no weekend', text):
                signals.append(line.strip()[:350])
    if signals:
        score -= 18
        schedule = 'Señal explícita de disponibilidad ampliada: ' + '; '.join(signals[:2])
        flags.append('Posible incompatibilidad de horario: revisar evidencia citada')
    contract = normalize(job.get('job_type'))
    if re.search(r'part.?time|tiempo parcial|media jornada', contract):
        score -= 20
        flags.append('Jornada parcial publicada; se prefiere jornada completa')
    if len(description.strip()) < 150:
        flags.append('Descripción insuficiente: comprobar funciones y requisitos')
        score = min(score, 64)
    # Show requirements separately from affinity; do not infer candidate eligibility.
    requirements = []
    for line in str(job.get('description') or '').splitlines():
        if re.search(r'relevant experience|experience (?:in|required)|experiencia (?:en|minima)|hasta \d+ anos|fluency|fluent|nivel de ingles|must have|mandatory', normalize(line)):
            requirements.append(line.strip()[:500])
    score = max(0, min(100, score))
    return {**job, 'fit_score': score, 'fit_reasons': reasons, 'fit_flags': flags,
            'fit_requirements': requirements[:5], 'salary_display': salary, 'schedule_note': schedule,
            'fit_band': 'Priorizar revisión' if score >= 70 else 'Explorar' if score >= 45 else 'Baja prioridad'}


def safe_url(value):
    return str(value) if urlsplit(str(value or '')).scheme.lower() in ('http', 'https') else ''


def write_report(jobs, output, counts, max_results=60):
    output.mkdir(parents=True, exist_ok=True)
    updated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    (output / 'ranked_jobs.json').write_text(json.dumps(
        {'updated_at': updated, 'counts': counts, 'jobs': jobs}, ensure_ascii=False, indent=2), encoding='utf-8')
    esc = lambda x: html.escape(str(x or ''), quote=True)
    cards = []
    for job in jobs[:max_results]:
        url = safe_url(job.get('direct_url')) or safe_url(job.get('url'))
        reasons = '; '.join(job['fit_reasons']) or 'Sin señales positivas específicas'
        flags = '; '.join(job['fit_flags']) or 'Verificar requisitos, horario y tareas en la oferta'
        requirements = '; '.join(job.get('fit_requirements', [])) or 'No extraídos: revisar la descripción original'
        title = esc(job.get('title'))
        link = f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{title}</a>' if url else title
        cards.append(f'''<article data-score="{job['fit_score']}">
<b class="score">{job['fit_score']}/100 · {esc(job['fit_band'])}</b><h2>{link}</h2>
<p>{esc(job.get('company'))} · {esc(job.get('location'))}</p>
<p>Publicación: {esc(job.get('date_posted') or 'No indicada')} · Fuente: {esc(job.get('ats'))}</p>
<p><strong>Salario publicado:</strong> {esc(job.get("salary_display", "No se especifica"))}</p>
<p><strong>Horario:</strong> {esc(job.get("schedule_note"))}</p>
<p><strong>A favor:</strong> {esc(reasons)}</p><p><strong>Revisar:</strong> {esc(flags)}</p>
<p><strong>Requisitos citados en la oferta:</strong> {esc(requirements)}</p>
<details><summary>Descripción y requisitos</summary><p class="description">{esc(job.get('description') or 'Abrir la oferta original.')}</p></details>
</article>''')
    page = '''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ofertas priorizadas</title><style>
body{font:16px/1.55 system-ui;background:#f2f5f8;color:#182b40;max-width:1000px;margin:auto;padding:28px}
h1{font-size:32px}h2{font-size:21px;margin:8px 0}a{color:#075b9e}article{background:white;border:1px solid #d3dfe8;border-radius:12px;padding:22px;margin:18px 0}
.score{color:#17634b}.description{white-space:pre-wrap;overflow-wrap:anywhere}input,select{font:inherit;padding:10px;max-width:100%;box-sizing:border-box}summary{cursor:pointer}label{display:inline-block;margin:8px 12px 8px 0}
</style><h1>Ofertas priorizadas</h1>'''
    page += f'<p>Actualizado: {updated} · Mostrando {min(len(jobs), max_results)} de {len(jobs)} ofertas · {counts["excluded"]} fuera de filtros · {counts["stale"]} antiguas</p>'
    page += '''<p>La puntuación expresa afinidad por reglas, no probabilidad de contratación. Las menciones pueden pertenecer a la descripción de la empresa. Confirma tareas, experiencia directa, idioma y vigencia antes de aplicar. Se muestran las mejores ofertas hasta el límite del informe; el archivo ranked_jobs.json conserva la lista completa. Fecha de publicación no equivale a oferta abierta.</p>
<label>Buscar <input id="search" placeholder="Puesto, empresa o palabra"></label>
<label>Prioridad <select id="minimum"><option value="0">Todas</option><option value="45">Explorar o priorizar</option><option value="70">Priorizar revisión</option></select></label>
<p id="count"></p>'''
    page += '\n'.join(cards) or '<p>No hay ofertas que pasen los filtros. Consulta el diagnóstico y amplía el intervalo.</p>'
    page += '''<script>
const search=document.getElementById('search'),minimum=document.getElementById('minimum');
function filter(){let n=0;for(const card of document.querySelectorAll('article')){
card.hidden=Number(card.dataset.score)<Number(minimum.value)||!card.textContent.toLocaleLowerCase().includes(search.value.toLocaleLowerCase());if(!card.hidden)n++;
}document.getElementById('count').textContent=n+' ofertas visibles';}
search.addEventListener('input',filter);minimum.addEventListener('change',filter);filter();
</script></html>'''
    (output / 'shortlist.html').write_text(page, encoding='utf-8')


def rank():
    import scrape_jobs as s
    rules = json.loads((ROOT / 'private' / 'ranking.json').read_text(encoding='utf-8'))
    source = ROOT / 'output' / 'all_jobs.json'
    data = json.loads(source.read_text(encoding='utf-8')) if source.exists() else {'jobs': []}
    jobs, rejected = [], []
    counts = {'excluded': 0, 'stale': 0}
    now = datetime.now(timezone.utc)
    for job in data['jobs']:
        why = ''
        if not s.title_matches_keywords(job.get('title', '')):
            why = 'Título fuera de filtros'
        elif not s.is_target_location(job.get('location', '')):
            why = 'Ubicación fuera de filtros'
        elif s._is_excluded_company(job.get('company', '')):
            why = 'Empresa excluida'
        if why:
            counts['excluded'] += 1
            rejected.append({**job, 'excluded_reason': why})
            continue
        posted = s._parse_posted_at(job.get('date_posted', ''))
        if posted and (now - posted).days > rules.get('max_age_days', 45):
            counts['stale'] += 1
            rejected.append({**job, 'excluded_reason': 'Publicación antigua'})
            continue
        jobs.append(evaluate(job, rules))
    jobs.sort(key=lambda j: (-j['fit_score'], j.get('company', '')))
    output = ROOT / 'private' / 'results'
    write_report(jobs, output, counts, max_results=int(rules.get('max_report_jobs', 60)))
    (output / 'excluded_jobs.json').write_text(json.dumps(rejected, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(jobs)} ofertas clasificadas; {counts}. Informe: {output / "shortlist.html"}')
    return output / 'shortlist.html'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['weekly', 'daily', 'initial', 'rank', 'check', 'open'])
    parser.add_argument('--source', choices=['indeed', 'linkedin', 'both'], default='both')
    args = parser.parse_args()
    if not (ROOT / 'config.json').is_file():
        parser.error('Falta config.json personal. No se ejecutará la configuración de ejemplo.')
    # Fail closed on malformed personal config, before importing scraper fallbacks.
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    rules = json.loads((ROOT / 'private' / 'ranking.json').read_text(encoding='utf-8'))
    for rule in rules['rules']:
        re.compile(rule['pattern'])
    if args.action == 'check':
        import scrape_jobs as s
        print(f'Configuración válida. LinkedIn: {s.LINKEDIN_LOOKBACK_SECONDS // 3600}h. Indeed: {s.INDEED_LOOKBACK_HOURS}h.')
        print(f'Búsquedas: LinkedIn {len(s.LINKEDIN_SEARCH_TERMS)}, Indeed {len(s.INDEED_SEARCH_TERMS)}. Reglas: {len(rules["rules"])}.')
        print('Ubicaciones:', config['location_filter']['terms'])
        return
    if args.action in ('weekly', 'daily', 'initial'):
        import scrape_jobs as s
        if args.source in ('indeed', 'both'):
            hours = s.INDEED_BACKFILL_DAYS * 24 if args.action == 'initial' else s.INDEED_LOOKBACK_HOURS
            s.save_indeed_results(s.scrape_indeed_recent(hours_old=hours))
        if args.source in ('linkedin', 'both'):
            if args.action in ('weekly', 'daily'):
                jobs = s.scrape_linkedin_recent()
            else:
                jobs, raw = s._linkedin_search(s.LINKEDIN_SEARCH_TERMS, s.LINKEDIN_BACKFILL_DAYS * 86400,
                                              max_results=100)
                if not raw:
                    print('LinkedIn no devolvió tarjetas: se conservan resultados previos; no confirma ausencia de vacantes.')
                    jobs = s._load_prev_jobs(str(ROOT / 'output' / 'linkedin_jobs.json'))
                jobs = [j for j in jobs if s.is_target_location(j.get('location', ''))]
                if jobs:
                    s._enrich_linkedin_postings(jobs)
            s.save_linkedin_results(jobs)
    report = rank()
    if args.action == 'open':
        webbrowser.open(report.as_uri())


if __name__ == '__main__':
    main()

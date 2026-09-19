from __future__ import annotations

import re
from .common import normalize
from .job_text import scoring_context
from .requirements import requirement_review

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



def sentences(text: str) -> list[str]:
    return [x.strip(' \t-•*') for x in re.split(r'[\n;]+|(?<=[.!?])\s+', text) if x.strip()]


def scoring_text(description: str) -> tuple[str, list[str]]:
    context = scoring_context(description)
    return context['duties'], context['ignored']


def _match_evidence(rule: dict, title: str, description: str) -> str:
    pattern = rule['pattern']
    parts = [title] if rule.get('field') == 'title' else sentences(title + '\n' + description)
    for original in parts:
        text = normalize(original)
        for m in re.finditer(pattern, text, re.I):
            # A dislike explicitly negated in a duty sentence is not a negative signal.
            prefix = text[max(0, m.start() - 35):m.start()]
            if rule['weight'] < 0 and re.search(r'(?:\bno\b|\bwithout\b|\bsin\b|not required)[^,.;:]{0,28}$', prefix):
                continue
            return original[:650]
    return ''


def evaluate(job: dict, rules: dict) -> dict:
    """Transparent affinity plus separate requirement warnings. Never rejects a job."""
    title = str(job.get('title') or '')
    desc = str(job.get('description') or '')
    context = scoring_context(desc)
    clean_desc, ignored = context['duties'], context['ignored']
    full = normalize(title + '\n' + context['context'])
    finance = rules.get('financial_domain_pattern', '')
    title_finance = rules.get('financial_title_pattern', finance)
    industry_finance = rules.get('financial_industry_pattern', '')
    in_domain = (bool(re.search(title_finance, normalize(title), re.I)) if title_finance else False) or (bool(re.search(finance, full, re.I)) if finance else True)
    if industry_finance and re.search(industry_finance, normalize(job.get('company_industry')), re.I):
        in_domain = True
    domain_requirements = '\n'.join(p['text'] for p in context['parts'] if p['section'] == 'requirements' and re.search(r'entornos financieros|sector bancario|sector financiero|financial services|banking|banca', normalize(p['text'])) and not re.search(r'degree|titulacion|grado', normalize(p['text'])))
    if finance and re.search(finance, normalize(domain_requirements)):
        in_domain = True
    score = rules.get('base', 35)
    applied_groups = set()
    reasons, flags, evidence = [], [], []
    for rule in sorted(rules.get('rules', []), key=lambda r: -r['weight']):
        if rule.get('group') and rule['group'] in applied_groups:
            continue
        if rule.get('unless_context') and re.search(rule['unless_context'], normalize(title + '\n' + clean_desc)):
            continue
        if rule.get('requires_financial_domain') and not in_domain:
            continue
        if rule.get('unless_title') and re.search(rule['unless_title'], normalize(title)):
            continue
        field_text = context.get(rule.get('field'), clean_desc)
        if rule.get('min_occurrences',1)>1:
            matches={normalize(line) for line in sentences(field_text) if re.search(rule['pattern'],normalize(line),re.I)}
            if len(matches)<rule['min_occurrences']:
                continue
        quote = _match_evidence(rule, title if rule.get('field') in ('title', 'text', None) else '', field_text)
        if quote:
            score += rule['weight']
            if rule.get('group'):
                applied_groups.add(rule['group'])
            (reasons if rule['weight'] > 0 else flags).append(rule['label'])
            evidence.append({'label': rule['label'], 'weight': rule['weight'], 'evidence': quote})
    if finance and not in_domain:
        weight = rules.get('outside_finance_penalty', -18)
        score += weight
        flags.append('No hay una señal clara de ámbito financiero: revisar antes de priorizar')
        evidence.append({'label': 'Ámbito financiero no detectado', 'weight': weight, 'evidence': 'Ausencia de términos configurados, no prueba de incompatibilidad'})
    salary, sw, salary_note = salary_signal(job, rules.get('salary', {}))
    score += sw
    if salary_note:
        (reasons if sw > 0 else flags).append(salary_note)
    schedule = 'Horario real no verificado; confirmar horas habituales y picos con el equipo'
    signals = []
    for original in sentences(desc):
        text = normalize(original)
        if re.search(r'long hours|late nights|work (?:on )?weekends|evening and weekend|horas extra|fuera (?:del|de) horario|disponibilidad (?:total|24)|trabaj.{0,25}(?:noches|fines de semana)', text):
            if not re.search(r'no overtime|no (?:se requiere[n]? )?horas extra|sin horas extra|no weekend', text):
                signals.append(original[:350])
    if signals:
        score -= 18
        schedule = 'Señal explícita de disponibilidad ampliada: ' + '; '.join(signals[:2])
        flags.append('Posible incompatibilidad de horario: revisar evidencia citada')
    if re.search(r'part.?time|tiempo parcial|media jornada', normalize(job.get('job_type'))):
        score -= 20
        flags.append('Jornada parcial publicada; se prefiere jornada completa')
    if len(desc.strip()) < 150:
        flags.append('Descripción insuficiente: comprobar funciones y requisitos')
        score = min(score, 64)
    score = max(0, min(100, score))
    eligibility, gap_penalty, requirements = requirement_review(job, rules.get('candidate'), parts=context['parts'])
    priority = max(0, score - gap_penalty)
    caps = [{'limit': r['priority_limit'], 'label': r['label'], 'evidence': r['evidence']} for r in requirements if r.get('priority_limit')]
    for cap in rules.get('priority_caps', []):
        if cap.get('title_pattern') and not re.search(cap['title_pattern'], normalize(title), re.I):
            continue
        scope = title if cap.get('field') == 'title' else context.get(cap.get('field'),clean_desc)
        if re.search(cap['pattern'], normalize(scope), re.I):
            caps.append({'limit': cap['limit'], 'label': cap['label'], 'evidence': _match_evidence({'pattern': cap['pattern'], 'weight': 0, 'field': cap.get('field')}, title if cap.get('field') == 'title' else '', scope)})
    if not in_domain and 'outside_domain_cap' in rules:
        caps.append({'limit': rules['outside_domain_cap'], 'label': 'Ámbito financiero o económico no acreditado', 'evidence': 'Revisar funciones y sector; beneficios y requisitos no acreditan el ámbito del puesto'})
    for cap in caps:
        priority = min(priority, cap['limit'])
        flags.append(cap['label'])
    priority = round(priority, 1)
    confidence = 'low' if len(clean_desc.strip()) < 150 else 'medium'
    if confidence == 'low':
        flags.append('Pocas funciones identificables: revisar la descripción original')
    category = 'Otras / revisar dominio'
    for label, pattern in rules.get('categories', []):
        if re.search(pattern, normalize(title)):
            category = label
            break
    return {**job, 'fit_score': score, 'priority_score': priority, 'fit_reasons': reasons,
            'fit_flags': flags, 'score_evidence': evidence, 'eligibility': eligibility,
            'requirement_penalty': gap_penalty, 'requirement_evidence': requirements,
            'priority_caps': caps, 'ranking_confidence': confidence,
            'description_sections': context['parts'],
            'fit_requirements': [r['evidence'] for r in requirements], 'financial_domain_detected': in_domain,
            'salary_display': salary, 'schedule_note': schedule, 'category': category,
            'score_version': rules.get('version', 'custom'), 'ignored_scoring_boilerplate': ignored,
            'fit_band': 'Priorizar revisión' if priority >= 70 else 'Explorar' if priority >= 45 else 'Baja prioridad'}

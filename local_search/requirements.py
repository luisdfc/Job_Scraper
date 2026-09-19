"""Evidence-backed, graded gaps. Preferences are never treated as obligations."""
from __future__ import annotations

import re
from .common import normalize
from .job_text import HARD, sections

LANGUAGES = r'\b(german|aleman|french|frances|dutch|neerlandes|korean|coreano|italian|italiano|portuguese|portugues|chinese|chino|japanese|japones)\b'
TECHNICAL_DEGREE = r'computer science|informatics|informatica|engineering|ingenieria|mathematics|matematicas|physics|fisica|statistics|estadistica|actuarial|actuario|ph\.?d|doctorad'
ACCESSIBLE_DEGREE = r'\bfinance\b|\bfinanzas\b|\beconomics?\b|\beconomia\b|ciencias economicas|empresariales|\bbusiness\b|administracion|\bade\b|equivalent (?:(?:practical|professional|work) )?experience|experiencia (?:profesional )?equivalente'
EXPERIENCE = r'\b(\d{1,2})\s*(?:(?:[-–—/]|to|or|a|y|o)\s*(\d{1,2})\s*)?\+?\s*(?:years?|anos?)\b'


def requirement_review(job: dict, candidate: dict | None = None, *, parts=None) -> tuple[str, int, list[dict]]:
    candidate = candidate or {}
    years_available = candidate.get('relevant_years', 2)
    evidence, penalties = [], {}

    def add(kind, label, quote, penalty=0, optional=False):
        penalties[kind] = max(penalties.get(kind, 0), 0 if optional else penalty)
        evidence.append({'type': kind if penalty and not optional else 'review', 'label': label,
                         'evidence': quote[:900], 'penalty': 0 if optional else penalty,
                         'strength': 'preferred' if optional else 'stated'})

    title = str(job.get('title') or '')
    nt = normalize(title)
    if re.search(LANGUAGES, nt) and re.search(r'speaking|speaker|bilingue|nativo', nt):
        add('language', 'Idioma del título no acreditado en el perfil', title, 10)
        evidence[-1]['priority_limit'] = 35
    # Job titles describe responsibility, even when the years below are optional.
    if re.search(r'\b(?:head of|chief|cfo|cto|vp|vice president|managing director)\b|\bdirector(?:a|/a)?\b', nt):
        add('seniority', 'Responsabilidad directiva: trayectoria y liderazgo distintos del perfil', title, 24)
    elif re.search(r'\b(?:lead|principal|staff|manager|gerente|responsable)\b', nt) and not re.search(r'asset manager|portfolio manager|fund manager|relationship manager', nt):
        add('seniority', 'Responsabilidad de equipo o función: confirmar autonomía y liderazgo requeridos', title, 12)
    elif re.search(r'\bsenior\b|\bsr\.?\b', nt):
        add('seniority', 'Nivel senior: revisar autonomía; el título por sí solo no excluye', title, 4)

    parts = parts if parts is not None else sections(str(job.get('description') or ''))
    for part in parts:
        if part['section'] not in ('requirements', 'optional', 'role', 'duties'):
            continue
        original, optional = part['text'], part['optional']
        line = normalize(original)
        for word, value in {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'ten':10,'dos':2,'tres':3,'cuatro':4,'cinco':5,'seis':6,'siete':7,'ocho':8,'diez':10}.items():
            line = re.sub(r'\b'+word+r'(?=\s+(?:years?|anos))', str(value), line)
        requirement = part['section'] in ('requirements', 'optional') or bool(re.search(r'experien|require|must|imprescind|titulacion|degree|grado|licenciad|licenciatura|formacion|dominio|conocimientos', line))
        if not requirement:
            continue
        degree = re.search(TECHNICAL_DEGREE, line)
        degree_context = re.search(r'degree|bachelor|master|grado|licenciad|licenciatura|titulacion|formacion|ph\.?d|doctorad|actuario', line)
        if degree and degree_context:
            # "Financial Engineering" is one technical degree, not a Finance alternative.
            accessible = re.search(ACCESSIBLE_DEGREE, line)
            if accessible:
                add('degree', 'Admite formación económica/financiera o experiencia equivalente', original)
            else:
                add('degree', 'Formación técnica específica; confirmar alternativas' if not optional else 'Formación técnica deseable, sin penalización', original, 22, optional)
        years = re.search(EXPERIENCE, line)
        company_history = re.search(r'we have|company has|firm has|fundad[ao]|founded|nuestra (?:empresa|compania)', line)
        if years and not company_history and (re.search(r'experien|minimum|at least|minim[oa]', line) or part['section'] == 'requirements'):
            minimum = int(years[1])
            gap = max(0, minimum - years_available)
            weight = 0 if gap <= .25 else 2 if gap <= 1.25 else 5 if gap <= 2.25 else 9 if gap <= 3.25 else min(20, 12 + int(gap-4)*2)
            label = (f'{minimum} años deseables: sin penalización' if optional else
                     f'Pide desde {minimum} años; diferencia pequeña y defendible' if 0 < gap <= 1.25 else
                     f'Pide desde {minimum} años: contrastar experiencia transferible y autonomía')
            add('experience', label, original, weight, optional)
            if minimum >= 6 and not optional:
                evidence[-1]['priority_limit'] = 44

        # Transferable experience can help without inventing professional experience.
        for specialization in candidate.get('unproven_specializations', []):
            if specialization.get('unless_pattern') and re.search(specialization['unless_pattern'], line):
                continue
            if re.search(specialization['pattern'], line) and re.search(r'experien|proven|demostra|track record', line):
                add('specialization', specialization['label'], original, specialization.get('penalty', 5), optional)

        advanced = bool(re.search(r'advanced|expert|expertise|strong|proficien|production|avanzad|dominio|excellent|solido|solidos', line))
        coding = advanced and bool(re.search(r'\bpython\b|\bc\+\+|software develop|software engineer|programacion|\bjava\b|\bpyspark\b', line))
        accepted_tool = bool(re.search(r'\bexcel\b',line) and re.search(r'\bor\b|\bo\b|such as|como',line))
        if coding and accepted_tool:
            add('review', 'Se ofrecen herramientas alternativas, incluida Excel; confirmar el uso real de programación', original)
            coding = False
        if coding:
            add('coding', 'Programación avanzada o de producción: no acreditada por un proyecto asistido con IA', original, 10, optional)
        elif advanced and not accepted_tool and re.search(r'\bsql\b|\bsas\b|\bdax\b', line):
            add('tools', 'Herramienta analítica avanzada: formación o exposición no equivalen a dominio', original, 4, optional)
        if re.search(r'stochastic calculus|calculo estocastico|partial differential equations',line) and advanced:
            add('quantitative_methods', 'Matemática cuantitativa especializada: contrastar con la formación documentada', original, 8, optional)
        if re.search(LANGUAGES, line) and re.search(r'fluen|native|c1|c2|biling|imprescind|require|mandatory|full professional', line):
            add('language', 'Idioma solicitado que no consta en el perfil', original, 10, optional)
            if not optional:
                evidence[-1]['priority_limit'] = 35
        if re.search(r'cfa charterholder|cfa charter|certificacion cfa', line) and HARD.search(line):
            add('credential', 'La acreditación completa exigida debe verificarse; aprobar niveles no equivale al charter', original, 10, optional)
        if re.search(r'law degree|grado en derecho|licenciad.{0,10}derecho|colegiad|abogacia', line) and not re.search(ACCESSIBLE_DEGREE, line):
            add('degree', 'Titulación o habilitación jurídica específica no acreditada', original, 22, optional)
        if re.search(r'certificado de discapacidad|disability certificate', line) and not optional:
            add('credential', 'Certificado específico no documentado: confirmar si puedes participar', original, 20)

    # Seniority and years usually describe the same gap; do not charge both.
    penalties['experience'] = max(penalties.pop('seniority', 0), penalties.get('experience', 0))
    penalty = min(45, sum(penalties.values()))
    status = 'gap' if penalty else 'unknown' if len(str(job.get('description') or '').strip()) < 150 else 'review'
    return status, penalty, evidence

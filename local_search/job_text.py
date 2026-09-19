"""Separate the work from qualifications and employer advertising.

Conservative, inspectable rules for Spanish/English ads, including fragmented
HTML-to-text output. Unclassified prose remains available as role context.
"""
from __future__ import annotations

import re
from .common import normalize

HEADINGS = (
    ('optional', r'^(?:nice.to.have|preferred qualifications|bonus points|desirable|seria deseable|sera un plus|habilidades preferidas|valorable|valoraremos|se valorara|deseable|que valoramos)'),
    ('duties', r'^(?:responsabil|key (?:job )?responsibilities|your responsibilities|a few examples of your responsibilities|principales responsabilidades|funciones|principales funciones|tus funciones|tareas|your tasks|your role|the role|about the role|el puesto|sobre (?:el puesto|la posicion)|como sera tu dia a dia|cual sera tu mision|in this role|tu (?:rol|mision|dia a dia)|mision|mission|what (?:you.ll|you will|will you) (?:do|be)|lo que haras|que haras|que vas a hacer|que harias|en tu dia a dia|job description|job overview|main activities and requirements|descripcion del (?:puesto|empleo)|job duties)'),
    ('requirements', r'^(?:requirements|qualifications|cualificaciones|competencias y habilidades|habilidades requeridas|minimum qualifications|basic qualifications|required|skills|professional experience|your profile|what you(?:.ll)? (?:bring|need)|what we(?:.re)? (?:look for|are looking for|looking for)|what do we expect from you|who you are|to succeed in this role.{0,30}need|requisitos|que (?:buscamos|necesitamos|esperamos|debes aportar)|lo que buscamos|perfil(?: requerido| profesional)?|formacion|education|conocimientos|experiencia requerida)'),
    ('benefits', r'^(?:what (?:do )?we offer|benefits|our offer|perks|compensation|que (?:te )?ofrecemos|que tenemos para ti|que te llevaras|se ofrece|ofrecemos|lo que (?:encontraras|te ofrecemos)|por que (?:unirte|elegirnos)|why (?:join|work)|additional information|beneficios|nuestra oferta|a cambio)'),
    ('company', r'^(?:about (?:us|the company)|who we are|company (?:description|overview)|sobre (?:nosotros|la empresa)|quienes somos|conocenos)'),
    ('metadata', r'^(?:show more|show less|nivel de antiguedad|tipo de empleo|funcion laboral|sectores|seniority level|employment type|industries)$'),
)
BENEFIT = re.compile(r'health insurance|medical insurance|life insurance|seguro[s]? (?:medic|de (?:salud|vida|accidentes))|meal vouchers|retribucion flexible|employee benefits|flexible benefits|training (?:platform|catalog)|plataforma.{0,45}(?:formacion|aprendizaje)|(?:courses|cursos|training).{0,100}(?:marcel|udemy|learning)|vacaciones|well.being|wellbeing')
BOILERPLATE = re.compile(r'(?:ai|artificial intelligence|inteligencia artificial).{0,100}(?:hiring|recruitment|seleccion de candidatos)|(?:hiring|recruitment).{0,80}(?:ai|artificial intelligence)|final hiring decisions|(?:backed|funded) by|investors (?:include|such as)|raised.{0,40}(?:funding|capital)|equal opportunity|personal data.{0,40}(?:processed|privacy)|privacy notice|politica de privacidad|all employees.{0,80}(?:complying|compliance)|obligations.{0,80}artificial intelligence')
OPTIONAL = re.compile(r'preferred|preferably|desirable|nice.to.have|a plus|highly valued|would be an asset|an advantage|not required|no se requiere|no imprescindible|no.{0,15}excluyente|valorable|valoraremos|deseable|se valorara|idealmente|ideally|preferible')
HARD = re.compile(r'mandatory|must have|must hold|strictly required|essential|imprescindible|obligatori[oa]|requisito excluyente|indispensable')


def heading(line: str) -> str | None:
    clean = re.sub(r'^[^a-z0-9]+', '', normalize(line)).strip(' :?¿!*#')
    if len(clean) > 125:
        return None
    for kind, pattern in HEADINGS:
        if re.search(pattern, clean):
            if kind == 'optional' and not line.rstrip().endswith(':') and not re.fullmatch(r'(?:nice.to.have|preferred qualifications|bonus points|desirable|seria deseable|valorable(?:/\s*no imprescindible)?|deseable|que valoramos|sera un plus|habilidades preferidas)', clean):
                return None  # An inline preference does not qualify subsequent requirements.
            return kind
    return None


def sections(description: str) -> list[dict]:
    result = []
    current = 'role'
    description = re.sub(r'\\([&+*_\-.#])', r'\1', description)
    for paragraph in re.split(r'\n\s*\n', description.replace('\r', '')):
        pending = ''
        chunks = []
        for raw in paragraph.splitlines():
            line = raw.strip(' \t•*-')
            if not line:
                continue
            # A lowercase continuation is often the second half of a <strong> tag.
            continuation = bool(pending and not heading(pending) and not heading(line) and
                                (re.match(r'^[,.;:)]|^(?:en|in|de|del|con|para|and|or|o|y)\b', normalize(line)) or
                                 (line[0].islower() and not re.search(r'[.!?]$', pending))))
            if continuation:
                pending += ' ' + line
            else:
                if pending:
                    chunks.append(pending)
                pending = line
        if pending:
            chunks.append(pending)
        for chunk in chunks:
            kind = heading(chunk)
            if kind:
                current = kind
                # Headers sometimes contain an actual requirement after a colon.
                if ':' in chunk and len(chunk.split(':', 1)[1].strip()) < 12:
                    continue
                if ':' not in chunk and len(chunk.split()) <= 8 and not re.search(r'\b(?:en|in|de|advanced|avanzad\w*|years?|anos?)\b|\d',normalize(chunk)):
                    continue
            # A preference in a separate clause must not waive an earlier obligation.
            clauses=[]
            for sentence in re.split(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚ¿])|;\s+', chunk):
                preferred_parentheses=[m for m in re.finditer(r'\(([^()]+)\)',sentence) if OPTIONAL.search(normalize(m[1])) and len(m[1])>15]
                main=sentence
                for m in preferred_parentheses:
                    main=main.replace(m[0],'')
                clauses.extend(re.split(r',\s*(?=(?:preferably|ideally|idealmente|preferentemente)\b)',main,flags=re.I))
                clauses.extend(m[1] for m in preferred_parentheses)
            for sentence in clauses:
                text = normalize(sentence)
                section = current
                if BOILERPLATE.search(text):
                    section = 'boilerplate'
                elif BENEFIT.search(text):
                    section = 'benefits'
                elif current == 'role' and re.search(r'^(?:somos |we are (?!looking|seeking|hiring)|we.re (?!looking|seeking|hiring)|nuestra (?:empresa|compania)|fundad[ao] |founded )', text):
                    section = 'company'
                # Qualifications without a heading must not masquerade as duties.
                elif current in ('role', 'duties') and re.search(r'^(?:the candidate (?:should|must)|you (?:must|should) (?:have|hold|possess)|solid experience|strong background|good knowledge|degree|bachelor|master|grado|licenciad|titulacion|formacion|experiencia|experience|minimum |at least |al menos |minimo |conocimiento|proficien|strong knowledge|excellent knowledge|required:|must have)', text):
                    section = 'requirements'
                result.append({'section': section, 'text': sentence.strip(),
                               'optional': bool((section == 'optional' or OPTIONAL.search(text)) and not HARD.search(text))})
    return result


def scoring_context(description: str) -> dict:
    parts = sections(description)
    role = [p['text'] for p in parts if p['section'] in ('role', 'duties')]
    return {
        'parts': parts,
        'duties': '\n'.join(role),
        'requirements': '\n'.join(p['text'] for p in parts if p['section'] in ('requirements', 'optional')),
        'offer': '\n'.join(p['text'] for p in parts if p['section'] == 'benefits'),
        'context': '\n'.join(p['text'] for p in parts if p['section'] in ('role', 'duties', 'company')),
        'ignored': [p['text'][:500] for p in parts if p['section'] in ('benefits', 'boilerplate', 'metadata')],
    }

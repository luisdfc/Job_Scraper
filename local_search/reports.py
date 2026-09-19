from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

from .common import atomic_json, atomic_text, now_iso, safe_url, stable_id

ASSETS = Path(__file__).parent / 'assets'
LABELS = {'pending': 'Pendiente', 'saved': 'Guardada', 'applied': 'Candidatura enviada', 'interview': 'Entrevista', 'discarded': 'Descartada'}
VALIDITY = {'open': 'Accesible con señal de apertura', 'closed': 'Cierre confirmado', 'unknown': 'No verificable'}
STATUS = {'running': 'En ejecución', 'completed': 'Consultas finalizadas', 'partial': 'Resultados parciales',
          'interrupted': 'Interrumpida · datos parciales', 'failed': 'Fallida · sin resultados',
          'legacy': 'Histórico anterior · no actualizado', 'reranked': 'Reclasificación · sin nueva búsqueda', 'probe': 'Prueba de conexión'}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ''), quote=True)


def shell(title: str, body: str, script: str = '') -> str:
    css = (ASSETS / 'style.css').read_text(encoding='utf-8')
    scripts = f'<script>{script}</script>' if script else ''
    csp_script = "'sha256-" + __import__('base64').b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'" if script else "'none'"
    csp = f"default-src 'self'; script-src {csp_script}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; connect-src 'self'"
    return f'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{esc(csp)}"><meta name="referrer" content="no-referrer">
<title>{esc(title)}</title><style>{css}</style></head><body>{body}{scripts}</body></html>'''


def diagnostics(manifest: dict) -> str:
    blocks = []
    for source in manifest.get('sources', []):
        rows = ''.join(f'<tr><td>{esc(q.get("term"))}</td><td>{esc(q.get("location"))}</td><td>{q.get("pages",0)}</td><td>{q.get("raw_count",0)}</td><td>{q.get("retained_unique_jobs", "—")}</td><td>{q.get("priority_60_plus", "—")}</td><td>{esc(q.get("stop_reason"))}</td><td>{esc(q.get("error", ""))}</td></tr>' for q in source.get('queries', []))
        blocks.append(f'<h3>{esc(source.get("source"))}: {source.get("queries_attempted",0)}/{source.get("queries_planned",0)} consultas intentadas</h3><div class="table-wrap"><table><thead><tr><th>Consulta</th><th>Ubicación</th><th>Páginas</th><th>Registros</th><th>Ofertas únicas</th><th>Prioridad ≥60</th><th>Fin de consulta</th><th>Detalle</th></tr></thead><tbody>{rows}</tbody></table></div>')
    if not blocks:
        return '<p>No hubo consultas de red en esta instantánea.</p>'
    return ''.join(blocks) + '<p>Las ofertas únicas pueden aparecer en varias consultas; no sumes las filas como si fueran grupos independientes. Prioridad ≥60 usa el ranking de este informe y los filtros predeterminados.</p><p>exhausted/no_results: fin entregado por la fuente. empty_unconfirmed: vacío no concluyente. blocked/network_error: bloqueo o error. safety_page_limit/query_timeout: salvaguarda alcanzada, no búsqueda completa. repeated_page/repeated_cursor: la fuente repitió datos. Ninguno confirma cobertura total del mercado.</p>'


def write_report(jobs: list[dict], output: Path, counts: dict, max_results=None, *, manifest: dict | None = None, page_size: int = 50) -> None:
    """All records are in HTML/JSON. max_results is accepted only for backward compatibility."""
    output.mkdir(parents=True, exist_ok=True)
    manifest = manifest or {'id': output.name, 'status': 'reranked', 'created_at': now_iso(), 'days': 7}
    atomic_json(output / 'ranked_jobs.json', {'updated_at': manifest.get('created_at'), 'manifest': manifest, 'counts': counts, 'jobs': jobs})
    cards = []
    for i, job in enumerate(jobs):
        ident = job.get('job_id') or stable_id(job)
        availability = job.get('availability') or {'status': 'unknown', 'evidence': 'Sin comprobación en esta instantánea'}
        score = job.get('priority_score', job.get('fit_score', 0))
        affinity = job.get('fit_score', 0)
        cap_note = '; '.join(f'{c["label"]}: techo {c["limit"]}' for c in job.get('priority_caps',[]))
        url = safe_url(job.get('direct_url')) or safe_url(job.get('url'))
        title = esc(job.get('title') or 'Título no disponible')
        link = f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{title}</a>' if url else title
        evidence = ''.join(f'<tr><td>{esc(r.get("label"))}</td><td>{r.get("weight",0):+}</td><td>{esc(r.get("evidence"))}</td></tr>' for r in job.get('score_evidence', []))
        requirement = ''.join(f'<p><strong>{esc(r["label"])}:</strong> {esc(r["evidence"])}</p>' for r in job.get('requirement_evidence', [])) or '<p>No se han detectado barreras explícitas con estas reglas. No equivale a cumplir todos los requisitos.</p>'
        other_links = ''.join(f'<li><a href="{esc(u)}" target="_blank" rel="noopener noreferrer">{esc(u)}</a></li>' for u in dict.fromkeys(job.get('duplicate_urls',[])) if safe_url(u))
        duplicate_detail = f'<details><summary>Otros enlaces de esta misma oferta</summary><ul>{other_links}</ul></details>' if other_links else ''
        buttons = ''.join(f'<button type="button" class="state-button s-{key}" data-status="{key}" aria-pressed="false" disabled>{value}</button>' for key,value in LABELS.items())
        tags = [f'<span class="tag">{esc(job.get("category", "Revisar"))}</span>', f'<span class="tag validity-{availability["status"]}">{esc(VALIDITY.get(availability["status"], availability["status"]))}</span>']
        tags.append('<span class="tag new">Nueva en el histórico</span>' if job.get('is_new') else '<span class="tag">Detectada antes</span>')
        if job.get('date_scope') == 'old':
            tags.append('<span class="tag warn">Fuera de la ventana de publicación</span>')
        if job.get('date_scope') == 'unknown':
            tags.append('<span class="tag warn">Fecha no verificable</span>')
        if job.get('location_scope') == 'unknown':
            tags.append('<span class="tag warn">Ubicación por confirmar</span>')
        if job.get('location_scope') == 'outside':
            tags.append('<span class="tag warn">Fuera del ámbito configurado</span>')
        if job.get('possible_duplicate_count', 0) > 1:
            tags.append('<span class="tag warn">Posible duplicado · no fusionado</span>')
        if job.get('eligibility') == 'gap':
            tags.append('<span class="tag warn">Brecha explícita a revisar</span>')
        cards.append(f'''<article class="job state-pending" data-id="{esc(ident)}" data-score="{score}" data-affinity="{affinity}"
 data-new="{str(bool(job.get('is_new'))).lower()}" data-validity="{esc(availability['status'])}" data-date="{esc(job.get('date_scope','unknown'))}"
 data-location="{esc(job.get('location_scope','unknown'))}" data-source="{esc(', '.join(job.get('sources') or [job.get('ats','')]))}"
 data-category="{esc(job.get('category','Revisar'))}" data-eligibility="{esc(job.get('eligibility','unknown'))}" data-posted="{esc(job.get('date_posted',''))}" data-status="pending">
 <div class="job-head"><div><h2>{link}</h2><p class="company">{esc(job.get('company'))} <span>· {esc(job.get('location') or 'Ubicación no indicada')}</span></p></div><div class="score"><strong>{score}<small>/100</small></strong><span>Prioridad</span><span>Afinidad {affinity}/100</span></div></div>
 <div class="tags">{''.join(tags)}</div>
 <div class="status-row"><span class="state-label">Pendiente</span><div class="state-buttons" aria-label="Estado de esta candidatura">{buttons}</div><button class="collapse-button" type="button" aria-expanded="true">Contraer</button></div>
 <section class="job-body"><div class="meta-grid"><p><strong>Publicación</strong>{esc(job.get('date_posted') or 'No indicada')}</p><p><strong>Fuente</strong>{esc(', '.join(job.get('sources') or [job.get('ats','')]))}</p><p><strong>Salario publicado</strong>{esc(job.get('salary_display','No se especifica'))}</p></div>
 <p><strong>A favor:</strong> {esc('; '.join(job.get('fit_reasons',[])) or 'Sin señales positivas específicas')}</p>
 <p><strong>Revisar:</strong> {esc('; '.join(job.get('fit_flags',[])) or 'Funciones, requisitos, ubicación y condiciones')}</p>
 <p class="muted">{esc(job.get('schedule_note'))}</p>
 <details><summary>Por qué recibe esta puntuación</summary><p>Afinidad {affinity}, con un ajuste de {job.get('requirement_penalty',0)} puntos por experiencia y requisitos. {esc(cap_note)}. Prioridad final: {score}. Los requisitos deseables no restan; una diferencia pequeña de experiencia tiene poco peso. No es una probabilidad de contratación.</p><div class="table-wrap"><table><thead><tr><th>Regla</th><th>Puntos</th><th>Evidencia</th></tr></thead><tbody>{evidence}</tbody></table></div><p>Base, salario, horario y datos incompletos pueden ajustar el resultado. Las menciones en beneficios no dan puntos por funciones. Versión: {esc(job.get('score_version','custom'))}.</p></details>
 <details><summary>Requisitos y vigencia</summary>{requirement}<p><strong>Vigencia:</strong> {esc(availability.get('evidence'))}</p><p>Comprobación: {esc(availability.get('checked_at') or 'No realizada')}. La vigencia se refiere a ese momento.</p></details>
 <details><summary>Descripción completa</summary><div class="description">{esc(job.get('description') or 'No recuperada. Abre la fuente para revisar funciones y requisitos.')}</div></details>
 {duplicate_detail}
 <details class="notes-panel"><summary>Notas de candidatura <span class="has-notes"></span></summary><label>Notas privadas<textarea class="notes" maxlength="10000" rows="3" placeholder="Contacto, fecha, próximos pasos…" disabled></textarea></label><button type="button" class="save-notes" disabled>Guardar notas</button></details>
 </section></article>''')
    legacy = manifest.get('kind') == 'legacy' or manifest.get('status') == 'legacy'
    alert = ''
    if manifest.get('status') != 'completed':
        alert = f'<div class="notice"><strong>{esc(STATUS.get(manifest.get("status"), manifest.get("status")))}</strong><p>{esc(manifest.get("notice") or "Consulta el diagnóstico. No se han añadido ofertas antiguas para cubrir fallos de las fuentes.")}</p></div>'
    count_new = sum(bool(j.get('is_new')) for j in jobs)
    count_closed = sum((j.get('availability') or {}).get('status') == 'closed' for j in jobs)
    control = '''<section class="filters" aria-label="Filtros de todas las ofertas"><label class="search-field">Buscar en todas las ofertas<input id="search" type="search" placeholder="Puesto, empresa, requisito o descripción"></label>
<label>Ordenar<select id="sort"><option value="priority">Prioridad</option><option value="affinity">Afinidad</option><option value="new">Nuevas primero</option></select></label>
<label>Fuente<select id="source"><option value="all">Todas</option><option>LinkedIn</option><option>Indeed</option></select></label>
<label>Familia<select id="category"><option value="all">Todas</option></select></label>
<label>Novedades<select id="novelty"><option value="all">Todas</option><option value="new">Solo nuevas</option><option value="seen">Detectadas antes</option></select></label>
<label>Estado<select id="status"><option value="all">Todos</option><option value="pending">Pendientes</option><option value="saved">Guardadas</option><option value="applied">Candidatura enviada</option><option value="interview">Entrevista</option><option value="discarded">Descartadas</option></select></label>
<label>Vigencia<select id="validity"><option value="not_closed">Ocultar cierres confirmados</option><option value="all">Todas, incluidas cerradas</option><option value="open">Señal de apertura</option><option value="unknown">No verificables</option><option value="closed">Cerradas</option></select></label>
<label>Fecha de publicación<select id="date"><option value="recent">Ventana elegida + sin fecha</option><option value="all">Todas las recuperadas</option><option value="old">Fuera de ventana</option><option value="unknown">Sin fecha verificable</option></select></label>
<label>Ubicación<select id="location"><option value="target">Madrid + por confirmar</option><option value="all">Todas las recuperadas</option><option value="outside">Fuera del ámbito</option><option value="unknown">Por confirmar</option></select></label>
<label>Prioridad mínima<select id="minimum"><option value="0">Ninguna</option><option value="45">45</option><option value="70">70</option></select></label>
<label>Requisitos<select id="eligibility"><option value="all">Todos</option><option value="gap">Brecha explícita</option><option value="unknown">Descripción insuficiente</option><option value="review">Revisar en la fuente</option></select></label>
<button type="button" id="show-all">Mostrar absolutamente todas</button><button type="button" id="reset">Restablecer filtros</button></section>'''
    body = f'''<header class="topbar"><a href="../../index.html" class="brand">FINANCE / JOB SEARCH</a><nav><a href="../../index.html">Histórico</a><a href="ranked_jobs.json" download>Datos JSON</a><button id="export-state" type="button" disabled>Exportar estados</button></nav></header>
<main id="app" data-page-size="{page_size}" data-legacy="{str(legacy).lower()}"><div class="eyebrow">BÚSQUEDA LOCAL · INSTANTÁNEA INDEPENDIENTE</div><h1>{esc('Histórico anterior' if manifest.get('kind') == 'legacy' else 'Ofertas para revisar')}</h1>
<p class="lead">{esc(manifest.get('id'))} · Creación: {esc(manifest.get('created_at'))} · Datos de: {esc(manifest.get('data_as_of') or manifest.get('created_at'))} · Ventana: {manifest.get('days',7)} días</p>
{alert}<div class="kpis"><div><strong>{len(jobs)}</strong><span>Recuperadas · sin recorte</span></div><div><strong>{count_new}</strong><span>Nuevas en el histórico</span></div><div><strong>{count_closed}</strong><span>Cierres confirmados</span></div><div><strong>{page_size}</strong><span>Ofertas por página</span></div></div>
<p class="explanation">Se conserva todo lo recuperado. La puntuación ordena, no elimina. Los filtros se aplican al conjunto completo antes de paginar. Las candidaturas gestionadas permanecen visibles y se contraen.</p>
<p id="storage-message" class="notice muted" role="status">Conectando con el almacén local de estados…</p>{control}
<div class="results-bar"><p id="count" aria-live="polite"></p><div class="pager"><button id="prev" type="button">Anterior</button><span id="page-label"></span><button id="next" type="button">Siguiente</button></div></div><p id="hidden-reasons" class="muted"></p>
<div id="jobs">{''.join(cards)}</div><p id="empty" hidden>No hay resultados con estos filtros. Usa «Mostrar absolutamente todas» y consulta el diagnóstico.</p>
<div class="results-bar bottom"><button id="prev-bottom" type="button">Anterior</button><span id="page-label-bottom"></span><button id="next-bottom" type="button">Siguiente</button></div>
<details class="diagnostics"><summary>Diagnóstico y cobertura de esta ejecución</summary>{diagnostics(manifest)}<p>{esc(manifest.get('verification_note',''))}</p></details>
<noscript><p>JavaScript está desactivado. Se muestran todas las ofertas sin filtros; los estados requieren abrir el informe mediante job_search.py open.</p></noscript>
<footer>Datos locales. No envía candidaturas, no modifica el informe al abrirlo y no comparte el perfil. Nueva significa no detectada antes en este histórico, no necesariamente recién publicada.</footer></main><div id="toast" role="status" aria-live="polite"></div>'''
    atomic_text(output / 'shortlist.html', shell('Ofertas · ' + manifest.get('id',''), body, (ASSETS/'ui.js').read_text(encoding='utf-8')))


def build_index(root: Path) -> None:
    manifests = []
    for path in sorted((root/'runs').glob('*/manifest.json'), reverse=True):
        try:
            m = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            continue
        if (path.parent/'shortlist.html').exists():
            manifests.append(m)
    rows = []
    for m in sorted(manifests, key=lambda x:x.get('created_at',''), reverse=True):
        ident = m['id']
        rows.append(f'<tr><td><a href="runs/{esc(ident)}/shortlist.html">{esc(ident)}</a></td><td>{esc(m.get("created_at"))}</td><td>{esc(STATUS.get(m.get("status"),m.get("status")))}</td><td>{m.get("job_count",0)}</td><td>{m.get("new_count",0)}</td><td>{esc(m.get("kind"))}</td></tr>')
    body = f'''<header class="topbar"><span class="brand">FINANCE / JOB SEARCH</span><span>ARCHIVO LOCAL</span></header><main><div class="eyebrow">SIN MEZCLAR EJECUCIONES</div><h1>Histórico de búsquedas</h1><p class="lead">Cada informe conserva lo que se recuperó en esa ejecución. Los estados y las notas se comparten entre informes.</p><div class="notice"><p>Para guardar estados, abre este índice con <code>python job_search.py open --history</code>. Al abrir los archivos directamente puedes leerlos, pero los botones de guardado quedan desactivados.</p></div><div class="table-wrap"><table class="history"><thead><tr><th>Ejecución</th><th>Creación</th><th>Resultado</th><th>Ofertas</th><th>Nuevas</th><th>Tipo</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><p>El histórico anterior es un acumulado importado, no una reconstrucción de búsquedas pasadas. Las pruebas de conexión y reclasificaciones no sustituyen la última búsqueda real.</p><footer>Los informes antiguos describen su fecha de captura. No certifican que una oferta siga abierta hoy.</footer></main>'''
    body=body.replace('Las pruebas de conexión y reclasificaciones no sustituyen la última búsqueda real.', 'Las pruebas no cambian el informe predeterminado. Una reclasificación puede elegirse como predeterminada, conservando la fecha y cobertura de la captura original.')
    atomic_text(root/'index.html', shell('Histórico de búsquedas',body))

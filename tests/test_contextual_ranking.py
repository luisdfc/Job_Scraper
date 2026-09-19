"""Synthetic regressions for context, soft gaps, geography and identity safety."""
from datetime import datetime, timezone
import json

import pytest

from local_search.common import deduplicate, location_scope, madrid_municipalities
from local_search.job_text import scoring_context
from local_search.network import Page, assess_availability
from local_search.pipeline import enrich_and_verify, load_settings, rerank, finalize, new_run
from local_search.requirements import requirement_review
from local_search.scoring import evaluate
from local_search.storage import Store


def job(n=1, **fields):
    return {'title':'Financial Analyst', 'description':'Analyze financial portfolios and support investment decisions. '*4,
            'url':f'https://example.org/jobs/{n}', 'ats':'Indeed','company':'Example','location':'Madrid', **fields}


def test_benefits_and_requirements_do_not_become_duties():
    c=scoring_context('Responsibilities\nAnalyze sales data.\n\nRequirements\nDegree in Econometrics.\n\nBenefits\nHealth insurance.\nCourses on our artificial intelligence platform.')
    assert 'Econometrics' not in c['duties'] and 'insurance' not in c['context']
    assert 'artificial intelligence' not in c['duties']
    assert len(c['ignored'])==2


def test_inline_preference_does_not_leak_to_next_requirement():
    result=requirement_review(job(description='Requisitos\nValorable especialización financiera.\nMínimo 3 años de experiencia.\nValorable francés.\nAdvanced Python required.'))
    assert any(x['type']=='experience' and x['penalty']==2 for x in result[2])
    assert any(x['type']=='coding' for x in result[2])


def test_requirement_after_html_fragment_keeps_its_specialization():
    result=requirement_review(job(description='Requirements\nMínimo 2 años de experiencia\nen análisis funcional bancario.'),
                              {'relevant_years':2,'unproven_specializations':[{'pattern':'analisis funcional','label':'Review specialty','penalty':3}]})
    assert result[1]==3
    assert any('bancario' in x['evidence'] for x in result[2])


@pytest.mark.parametrize('phrase', ['3–5 years','3 or 4 years','3 / 4 years','three years','tres años'])
def test_small_experience_gap_uses_lower_bound(phrase):
    result=requirement_review(job(description='Requirements\n'+phrase+' of relevant experience.'))
    assert result[1]==2


def test_optional_years_and_tools_have_no_penalty():
    result=requirement_review(job(description='Preferred qualifications:\n5 years of experience.\nAdvanced Python.'))
    assert result[1]==0
    assert all(e['penalty']==0 for e in result[2])


def test_optional_clause_does_not_waive_required_years_or_coding():
    result=requirement_review(job(description='Requirements\n3 years of experience, preferably in banking.\nStrong C++ skills (Python is a plus).'))
    assert any(x['type']=='experience' and x['penalty']==2 for x in result[2])
    assert any(x['type']=='coding' and x['penalty']==10 for x in result[2])


def test_excel_is_an_acceptable_tool_alternative():
    result=requirement_review(job(description='Requirements\nProficiency with tools such as Excel, SQL or Python.'))
    assert result[1]==0


def test_economicas_empresariales_are_valid_degree_alternatives():
    assert requirement_review(job(description='Requisitos\nLicenciado en Ciencias Económicas o Empresariales / Ingeniería Superior.'))[1]==0


def test_inline_education_is_not_discarded_as_a_header():
    result=requirement_review(job(description='Requisitos\nFormación en Matemáticas o Ingeniería.\nConocimientos avanzados de SQL.'))
    assert result[1]==26


def test_one_report_does_not_imply_repetitive_reporting_as_core_work():
    rules={'base':60,'rules':[{'pattern':'monthly reports|data quality','field':'duties','label':'Repetitive','weight':-10,'min_occurrences':2}]}
    analytical=job(description='Responsibilities\nAnalyze investments and model portfolios.\nPrepare monthly reports.\nSupport investment decisions. '*2)
    repetitive=job(description='Responsibilities\nPrepare monthly reports.\nManage data quality.\nPrepare monthly reports for management. '*2)
    assert evaluate(analytical,rules)['fit_score']>evaluate(repetitive,rules)['fit_score']


def test_terminal_linkedin_response_is_saved_for_diagnosis(tmp_path):
    from local_search.collection import collect_linkedin
    config={'search_terms':{'linkedin':['analyst']},'locations':{'linkedin':[{'location':'Madrid'}]},'collection':{'linkedin_delay_seconds':0}}
    _,summary=collect_linkedin(config,7,tmp_path,fetcher=lambda *a,**k:Page('https://example.org',200,'<html>Unexpected response</html>'),sleeper=lambda _:None)
    q=summary['queries'][0]
    assert q['stop_reason']=='parser_or_empty_unconfirmed'
    assert (tmp_path/q['terminal_response']['saved_as']).read_text()=='<html>Unexpected response</html>'


def test_company_age_does_not_become_experience():
    assert requirement_review(job(description='About us\nWe have 50 years of experience in financial services.\nResponsibilities\nAnalyze investments.'))[1]==0


def test_employer_seeks_role_is_not_company_advertising():
    c=scoring_context('We are looking for a financial analyst to analyze investment decisions.')
    assert c['duties']


def test_mutually_exclusive_role_bonuses_do_not_stack():
    rules={'base':20,'rules':[{'pattern':'analyst','label':'Analyst','weight':20,'field':'title','group':'role'},
                            {'pattern':'financial','label':'Finance','weight':30,'field':'title','group':'role'}]}
    assert evaluate(job(),rules)['fit_score']==50


def test_role_cap_cannot_accidentally_cap_an_adjacent_profession():
    rules={'base':80,'priority_caps':[{'pattern':'portfolio of projects','field':'duties','title_pattern':'portfolio analyst','limit':40,'label':'IT portfolio'}]}
    assert evaluate(job(title='Business Analyst',description='Manage a portfolio of projects. '*6),rules)['priority_score']==80


def test_madrid_catalogue_and_unknown_locations():
    config={'location_filter':{'terms':['madrid']}}
    for loc in ['Ajalvir','Sevilla la Nueva','Algete','Parla','Meco','Navalcarnero','Cadalso de los Vidrios','La Acebeda','Alcalá de Henares']:
        assert location_scope({'location':loc},config)=='target'
    assert len(madrid_municipalities())>=179
    assert location_scope({'location':'Barrio sin geocodificar'},config)=='unknown'
    assert location_scope({'location':'Sevilla'},config)=='outside'
    assert location_scope({'location':'Madrid, Iowa, US'},config)=='outside'


def test_registered_aliases_collapse_rows_without_losing_urls():
    jobs,merged=deduplicate([job(1,job_id='known',is_new=True),job(2,job_id='known',is_new=False)])
    assert len(jobs)==1 and merged==1 and jobs[0]['is_new']
    assert jobs[0]['duplicate_urls']==['https://example.org/jobs/2']


def test_conflicting_validity_never_becomes_confirmed_closed():
    jobs,_=deduplicate([job(job_id='known',availability={'status':'closed'}),job(2,job_id='known',availability={'status':'open'})])
    assert jobs[0]['availability']['status']=='unknown'


def test_failure_circuit_is_per_employer_domain():
    jobs=[job(i,direct_url=f'https://blocked.example/jobs/{i}') for i in range(3)] + [job(4,direct_url='https://healthy.example/jobs/4')]
    seen=[]
    def fetch(url, **kwargs):
        seen.append(url)
        return Page(url,403,'Access denied') if 'blocked.' in url else Page(url,200,'<h1>Financial Analyst</h1><a href="/apply">Solicitar</a>')
    summary=enrich_and_verify(jobs,{'verification':{'stop_after_failures':2}},enabled=True,fetcher=fetch,sleeper=lambda _:None)
    assert len(seen)==3 and summary['skipped_after_domain_failures']==1
    assert jobs[-1]['availability']['status']=='open'
    assert jobs[2]['availability']['status']=='unknown'


@pytest.mark.parametrize('control', ['<a href="/apply">Solicitar</a>','<button>Apply</button>'])
def test_matching_job_with_enabled_apply_action(control):
    assert assess_availability(job(),Page('https://example.org/jobs/1',200,'<h1>Financial Analyst</h1>'+control))['status']=='open'


@pytest.mark.parametrize('control', ['<p>Apply your knowledge</p>','<button disabled>Apply</button>','<a href="javascript:void(0)">Apply</a>'])
def test_disabled_or_prose_apply_does_not_confirm_open(control):
    assert assess_availability(job(),Page('https://example.org/jobs/1',200,'<h1>Financial Analyst</h1>'+control))['status']=='unknown'


def test_make_latest_rerank_preserves_collection_date_and_geo_filters(tmp_path):
    config={'local_ui':{'timezone':'Europe/Madrid'},'location_filter':{'terms':['Madrid']}}
    rules={'base':40}
    out,m=new_run(tmp_path,config,rules,kind='scrape',days=7)
    m['status']='partial'
    jobs=Store(tmp_path).register([job()],m['id'])
    finalize(tmp_path,out,m,jobs,config,latest=True)
    report=rerank(tmp_path,config,rules,out.name,make_latest=True)
    data=json.loads((report.parent/'ranked_jobs.json').read_text(encoding='utf-8'))
    assert data['manifest']['collection_status']=='partial'
    assert data['manifest']['data_as_of']==m['created_at']
    assert json.loads((tmp_path/'private/latest_run.json').read_text())['id']==report.parent.name
    assert 'data-legacy="false"' in report.read_text(encoding='utf-8')


def test_changed_profile_requires_recalibration(tmp_path):
    (tmp_path/'private').mkdir()
    (tmp_path/'config.json').write_text('{}')
    (tmp_path/'private/profile.md').write_text('Changed facts')
    (tmp_path/'private/ranking.json').write_text(json.dumps({'profile_source':'private/profile.md','profile_sha256':'outdated'}))
    with pytest.raises(ValueError,match='perfil de referencia ha cambiado'):
        load_settings(tmp_path)


def test_privacy_guard_rejects_private_and_derived_files():
    from scripts.check_privacy import private_path
    for path in ['private/profile.md','runs/test/ranked_jobs.json','config.json','output/all_jobs.json','cv.pdf','profile_master_new.md','notes.local.md']:
        assert private_path(path)
    for path in ['config.example.json','README.md','local_search/scoring.py','output/.gitkeep']:
        assert not private_path(path)

"""Regression tests for snapshots, recall, state persistence and failure transparency.
All job data in these tests is synthetic. No network access is required.
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_search.common import (canonical_url, date_scope, deduplicate, identity_aliases,
                                 location_scope, now_iso, parse_date, read_json, stable_id)
from local_search.collection import collect_linkedin, read_checkpoints
from local_search.network import Page, assess_availability, public_url
from local_search.pipeline import load_settings, migrate_legacy, run_search, select_run, rerank
from local_search.scoring import evaluate, requirement_review
from local_search.storage import Store

NOW=datetime(2026,9,18,12,tzinfo=timezone.utc)

@pytest.fixture
def config():
    return {'search_terms':{'linkedin':['financial analyst'],'indeed':['financial analyst']},
            'locations':{'linkedin':[{'location':'Madrid, Spain'}],'indeed':[{'location':'Madrid','country':'Spain'}]},
            'location_filter':{'terms':['madrid','boadilla del monte','alcobendas','getafe']},
            'keywords':{'include':['nothing'],'exclude':['audit','data','financial']},
            'collection':{'max_pages_per_query':0,'query_timeout_seconds':0,'linkedin_delay_seconds':0,
                          'request_timeout_seconds':1,'stop_after_source_failures':2},
            'verification':{'delay_seconds':0,'request_timeout_seconds':1,'stop_after_failures':2},
            'local_ui':{'page_size':50,'timezone':'Europe/Madrid'}}

@pytest.fixture
def rules():
    return {'base':35,'rules':[{'label':'Finance','pattern':'financial|finance','weight':15,'field':'title'},
                             {'label':'Data','pattern':'data analyst','weight':15,'field':'title','requires_financial_domain':True}],
            'financial_domain_pattern':'finance|financial|bank','outside_finance_penalty':-18,
            'salary':{'minimum_eur':35000,'target_eur':40000}}


def job(n=1,**kwargs):
    base={'title':'Financial Data Analyst','company':'Example Bank','location':'Madrid, Spain',
          'url':f'https://example.org/jobs/{n}','ats':'LinkedIn','date_posted':datetime.now(timezone.utc).date().isoformat(),
          'description':'Analyze financial portfolios and research market data in a finance team. '*6}
    base.update(kwargs)
    return base


def cards(ids):
    return ''.join(f'<li><div data-entity-urn="urn:li:jobPosting:{i}"><h3 class="base-search-card__title">Audit Financial Analyst {i}</h3><h4 class="base-search-card__subtitle"><a>Example Bank</a></h4><span class="job-search-card__location">Madrid, Spain</span><time datetime="2026-09-18"></time></div></li>' for i in ids)

@pytest.mark.parametrize('location,expected',[
    ('Boadilla del Monte, MD, ES','target'),('Alcobendas, MD, ES','target'),
    ('Getafe, Community of Madrid, Spain','target'),('MD, ES','target'),
    ('Remote','unknown'),('Spain','unknown'),('','unknown'),
    ('Barcelona, CT, ES','outside'),('Baltimore, MD, US','outside')])
def test_location_scope_keeps_region_and_uncertainty(config,location,expected):
    assert location_scope({'location':location},config)==expected

@pytest.mark.parametrize('value,expected',[
    ('2026-09-18','recent'),('2026-09-11','recent'),('2026-09-10','old'),
    ('2026-09-11T11:59:59Z','old'),('2026-09-11T12:00:00Z','recent'),
    ('hace 3 días','recent'),('2 weeks ago','old'),('','unknown'),('garbage','unknown'),('2027-01-01','unknown')])
def test_weekly_date_scope_with_precision(value,expected):
    assert date_scope({'date_posted':value},NOW,7)==expected


def test_canonical_source_ids_ignore_tracking():
    assert canonical_url('https://www.linkedin.com/jobs/view/financial-analyst-123456/?trk=test')=='https://www.linkedin.com/jobs/view/123456'
    assert canonical_url('https://es.indeed.com/viewjob?jk=abcd&from=search')=='https://www.indeed.com/viewjob?jk=abcd'


def test_same_title_different_requisition_is_not_deleted():
    jobs,merged=deduplicate([job(1),job(2)])
    assert len(jobs)==2 and merged==0
    assert all(j['possible_duplicate_count']==2 for j in jobs)


def test_exact_aliases_merge_sources_but_keep_queries():
    a=job(1,search_queries=['finance'],direct_url='https://employer.example/jobs/77')
    b=job(2,ats='Indeed',search_queries=['data'],direct_url='https://employer.example/jobs/77?utm_source=indeed')
    jobs,n=deduplicate([a,b])
    assert n==1 and len(jobs)==1
    assert set(jobs[0]['sources'])=={'Indeed','LinkedIn'}
    assert set(jobs[0]['search_queries'])=={'finance','data'}


def test_generic_careers_url_does_not_merge_vacancies():
    jobs,n=deduplicate([job(1,direct_url='https://employer.example/careers'),job(2,direct_url='https://employer.example/careers')])
    assert len(jobs)==2 and n==0


def test_state_persists_across_runs_and_tracks_novelty(tmp_path):
    store=Store(tmp_path)
    first=store.register([job(1)],'run-a')[0]
    assert first['is_new']
    written=store.set_state(first['job_id'],'applied','Sent manually; follow up next week.',0)
    second=Store(tmp_path).register([job(1)],'run-b')[0]
    assert not second['is_new']
    assert second['job_id']==first['job_id']
    assert Store(tmp_path).states()[second['job_id']]['notes']==written['notes']
    assert Store(tmp_path).states()[second['job_id']]['status']=='applied'


def test_state_revision_prevents_overwrite_from_old_tab(tmp_path):
    store=Store(tmp_path)
    ident=store.register([job(1)],'a')[0]['job_id']
    store.set_state(ident,'saved','first',0)
    with pytest.raises(RuntimeError):
        store.set_state(ident,'discarded','stale edit',0)
    assert store.states()[ident]['status']=='saved'
    store.set_state(ident,'pending','undo',1)
    assert store.states()[ident]['status']=='pending'


def test_missing_state_job_is_not_created(tmp_path):
    with pytest.raises(ValueError):
        Store(tmp_path).set_state('job_'+'0'*24,'applied','',0)

@pytest.mark.parametrize('code',[401,403,404,410,429,500,999])
def test_http_failure_never_means_closed(code):
    result=assess_availability(job(),Page('https://example.org/jobs/1',code,'',f'HTTP {code}'),NOW)
    assert result['status']=='unknown'


def test_explicit_closure_requires_the_matching_job():
    page='<h1>Financial Data Analyst</h1><p>This job has expired.</p>'
    assert assess_availability(job(),Page('https://example.org/jobs/1',200,page),NOW)['status']=='closed'
    other='<h1>Different vacancy</h1><p>This job has expired.</p>'
    assert assess_availability(job(),Page('https://example.org/jobs/1',200,other),NOW)['status']=='unknown'


def test_related_expired_structured_job_does_not_close_this_job():
    page='<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Another job','validThrough':'2020-01-01'})+'</script><h1>Financial Data Analyst</h1><button>Apply now</button>'
    assert assess_availability(job(),Page('https://example.org/jobs/1',200,page),NOW)['status']=='open'


def test_matching_structured_expiration_is_closed():
    page='<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Financial Data Analyst','validThrough':'2026-09-01'})+'</script>'
    assert assess_availability(job(),Page('https://example.org/jobs/1',200,page),NOW)['status']=='closed'


def test_challenge_page_with_status_200_is_unknown():
    p=Page('https://example.org/jobs/1',200,'<h1>Financial Data Analyst</h1><p>Verify you are human. This job has expired.</p>')
    assert assess_availability(job(),p,NOW)['status']=='unknown'


def test_local_urls_are_not_requested():
    assert not public_url('http://127.0.0.1/private')
    assert not public_url('http://[::1]/private')
    assert not public_url('file:///etc/passwd')


def test_required_technical_degree_vs_accepted_economics():
    strict=job(description='A degree in Computer Science is mandatory. Analyze portfolios. '*3)
    optional=job(description='A degree in Finance, Economics, Computer Science or equivalent practical experience is required. '*3)
    preferred=job(description='Computer Science degree preferred, not required. We accept career changers. '*3)
    assert requirement_review(strict)[0]=='gap'
    assert requirement_review(optional)[1]==0
    assert requirement_review(preferred)[1]==0


def test_python_mention_alone_does_not_mean_engineering_requirement():
    assert requirement_review(job(description='Use Python and econometrics to analyze investment portfolios. '*4))[1]==0


def test_no_salary_does_not_penalize_or_filter(rules):
    a=evaluate(job(),rules)
    assert a['salary_display']=='No se especifica'
    assert a['fit_score']>0


def test_linkedin_keeps_titles_and_paginates_past_old_100_limit(config,tmp_path):
    calls=[]
    def fetch(url,**kw):
        from urllib.parse import urlsplit,parse_qs
        offset=int(parse_qs(urlsplit(url).query)['start'][0]);calls.append(offset)
        body=cards(range(offset+1,offset+11)) if offset<120 else ''
        return Page(url,200,body)
    jobs,summary=collect_linkedin(config,7,tmp_path,fetcher=fetch,sleeper=lambda _:None)
    assert len(jobs)==120
    assert all('Audit' in j['title'] for j in jobs)  # even though keywords.exclude contains audit
    assert calls[-1]==120
    assert summary['status']=='completed'
    assert summary['queries'][0]['stop_reason']=='exhausted'


def test_linkedin_repeated_page_stops_with_warning(config,tmp_path):
    jobs,summary=collect_linkedin(config,7,tmp_path,fetcher=lambda url,**kw:Page(url,200,cards([1,2])),sleeper=lambda _:None)
    assert len(deduplicate(jobs)[0])==2
    assert summary['status']=='partial'
    assert summary['queries'][0]['stop_reason']=='repeated_page'


def test_linkedin_block_has_no_stale_fallback(config,tmp_path):
    (tmp_path/'linkedin_jobs.json').write_text(json.dumps({'jobs':[job(99)]}))
    jobs,summary=collect_linkedin(config,7,tmp_path,fetcher=lambda url,**kw:Page(url,429,error='HTTP 429'),sleeper=lambda _:None)
    assert jobs==[] and summary['status']=='partial'
    assert summary['queries'][0]['stop_reason']=='blocked'


def test_linkedin_page_limit_is_diagnostic_not_silent(config,tmp_path):
    config['collection']['max_pages_per_query']=1
    jobs,summary=collect_linkedin(config,7,tmp_path,fetcher=lambda url,**kw:Page(url,200,cards([1,2])),sleeper=lambda _:None)
    assert len(jobs)==2 and summary['queries'][0]['stop_reason']=='safety_page_limit'


def test_migration_is_idempotent_and_preserves_original(tmp_path,config,rules):
    (tmp_path/'output').mkdir()
    path=tmp_path/'output/all_jobs.json'
    original=json.dumps({'jobs':[job(1),job(2)]}).encode()
    path.write_bytes(original)
    first=migrate_legacy(tmp_path,config,rules)
    second=migrate_legacy(tmp_path,config,rules)
    assert first==second and path.read_bytes()==original
    assert len(list((tmp_path/'runs').glob('*/manifest.json')))==1
    imported=read_json(first.parent/'ranked_jobs.json')['jobs']
    assert len(imported)==2 and all(not j['is_new'] for j in imported)
    assert not (tmp_path/'private/latest_run.json').exists()


def test_separate_runs_never_read_cumulative_output(tmp_path,config,rules,monkeypatch):
    import local_search.pipeline as pipeline
    current=[job(1)]
    def collect(c,days,raw_dir,**kw):
        kw['on_jobs'](current)
        return current,{'source':'LinkedIn','status':'completed','queries':[],'queries_planned':1,'queries_attempted':1}
    monkeypatch.setattr(pipeline,'collect_linkedin',collect)
    first=run_search(tmp_path,config,rules,source='linkedin',verify=False)
    first_bytes=first.read_bytes()
    current[:]=[job(2)]
    second=run_search(tmp_path,config,rules,source='linkedin',verify=False)
    assert first.parent!=second.parent
    assert [j['url'] for j in read_json(second.parent/'ranked_jobs.json')['jobs']]==[job(2)['url']]
    assert first.read_bytes()==first_bytes
    assert select_run(tmp_path)==second.parent


def test_partial_run_contains_only_current_results(tmp_path,config,rules,monkeypatch):
    import local_search.pipeline as pipeline
    (tmp_path/'output').mkdir()
    (tmp_path/'output/all_jobs.json').write_text(json.dumps({'jobs':[job(90)]}))
    def linkedin(c,days,raw_dir,**kw):
        kw['on_jobs']([job(2)])
        return [job(2)],{'source':'LinkedIn','status':'partial','queries':[]}
    def indeed(c,days,raw_dir,**kw):
        return [],{'source':'Indeed','status':'partial','queries':[]}
    monkeypatch.setattr(pipeline,'collect_linkedin',linkedin)
    monkeypatch.setattr(pipeline,'collect_indeed',indeed)
    report=run_search(tmp_path,config,rules,verify=False)
    data=read_json(report.parent/'ranked_jobs.json')
    assert data['manifest']['status']=='partial'
    assert len(data['jobs'])==1 and data['jobs'][0]['url']==job(2)['url']


def test_interrupted_search_keeps_checkpointed_results(tmp_path,config,rules,monkeypatch):
    import local_search.pipeline as pipeline
    def interrupted(c,days,raw_dir,**kw):
        kw['on_jobs']([job(2)])
        raise KeyboardInterrupt
    monkeypatch.setattr(pipeline,'collect_linkedin',interrupted)
    report=run_search(tmp_path,config,rules,source='linkedin',verify=False)
    data=read_json(report.parent/'ranked_jobs.json')
    assert data['manifest']['status']=='interrupted' and len(data['jobs'])==1


def test_rerank_creates_new_report_without_touching_old_or_latest(tmp_path,config,rules):
    (tmp_path/'output').mkdir()
    (tmp_path/'output/all_jobs.json').write_text(json.dumps({'jobs':[job(1)]}))
    original=migrate_legacy(tmp_path,config,rules)
    old=original.read_bytes()
    changed=rerank(tmp_path,config,{'base':5,'rules':[]},original.parent.name)
    assert original.read_bytes()==old and changed.parent!=original.parent
    assert not (tmp_path/'private/latest_run.json').exists()


def test_open_does_not_rank_or_rewrite(tmp_path,monkeypatch):
    import job_search as cli
    import local_search.server as server
    folder=tmp_path/'runs/test_run'
    folder.mkdir(parents=True)
    report=folder/'shortlist.html';report.write_text('immutable')
    monkeypatch.setattr(cli,'ROOT',tmp_path)
    monkeypatch.setattr(cli,'rank',lambda *a:pytest.fail('open must not rank'))
    monkeypatch.setattr(server,'open_report',lambda root,path,**kw:str(path))
    assert cli.main(['open','--run','test_run'])==0
    assert report.read_text()=='immutable'


def test_selection_disallows_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        select_run(tmp_path,'../../private')


def test_bad_config_fails_before_collection(tmp_path,monkeypatch):
    import job_search as cli
    (tmp_path/'private').mkdir()
    (tmp_path/'config.json').write_text('{invalid')
    (tmp_path/'private/ranking.json').write_text('{"rules":[]}')
    monkeypatch.setattr(cli,'ROOT',tmp_path)
    monkeypatch.setattr(cli,'run_search',lambda *a,**kw:pytest.fail('Must not scrape'))
    assert cli.main(['weekly'])==1


def test_indeed_checkpoint_hook_keeps_page_before_exhaustion(tmp_path):
    from local_search.indeed_worker import install_hook,StopQuery
    class Adapter:
        def __init__(self):
            self.session=SimpleNamespace(post=lambda:None)
        def _scrape_page(self,cursor):
            return [job(1)],None
    task={'settings':{'max_pages_per_query':0,'indeed_delay_seconds':0},'term':'finance','geo':{'location':'Madrid'}}
    checkpoint=tmp_path/'indeed.jsonl'
    install_hook(Adapter,task,checkpoint,converter=lambda post,*args:post)
    with pytest.raises(StopQuery):
        Adapter()._scrape_page(None)
    jobs,events=read_checkpoints(checkpoint)
    assert len(jobs)==1 and events[-1]['stop_reason']=='exhausted'


def test_indeed_checkpoint_hook_makes_silent_http_errors_visible(tmp_path):
    from local_search.indeed_worker import install_hook,StopQuery
    class Adapter:
        def __init__(self):
            self.session=SimpleNamespace(post=lambda *a,**kw:SimpleNamespace(ok=False,status_code=429))
        def _scrape_page(self,cursor):
            response=self.session.post('https://example.org')
            return [],None
    task={'settings':{},'term':'finance','geo':{}}
    checkpoint=tmp_path/'indeed.jsonl'
    install_hook(Adapter,task,checkpoint,converter=lambda post,*args:post)
    with pytest.raises(StopQuery):
        Adapter()._scrape_page(None)
    jobs,events=read_checkpoints(checkpoint)
    assert jobs==[] and events[-1]['stop_reason']=='blocked'


def test_experience_range_uses_lower_bound_not_upper_bound():
    r=requirement_review(job(description='Requirements\n3–5 years of relevant experience in financial analysis.'))
    assert r[1]==2  # One additional year is a small, negotiable gap.
    assert any('desde 3 años' in x['label'] for x in r[2])
    assert not any('desde 5 años' in x['label'] for x in r[2])


def test_degree_slash_notation_and_advanced_sql_are_flagged():
    r=requirement_review(job(description='Requisitos\nGrado/Licenciatura en Informática o carreras científicas afines\nConocimiento avanzado en SQL'))
    assert r[0]=='gap' and r[1]==26


def test_optional_technical_section_does_not_become_a_requirement():
    r=requirement_review(job(description='Valorable/ No imprescindible:\nExperiencia avanzada en Python\nConocimiento francés C1\nDoctorado en informática\nQué ofrecemos\nVacaciones.'))
    assert r[1]==0


def test_german_speaking_title_is_a_requirement_warning():
    assert requirement_review(job(title='AI Business Analyst (German-speaking)'))[1]==10


@pytest.mark.parametrize('degree', ['Financial Engineering', 'Statistics', 'Actuarial Science'])
def test_exclusive_quantitative_degrees_not_assumed_from_finance_profile(degree):
    from local_search.scoring import requirement_review
    status, penalty, evidence = requirement_review({'title':'Quantitative Analyst', 'description':f'Required: degree in {degree}.'})
    assert status == 'gap'
    assert penalty == 22
    assert any(item['type'] == 'degree' for item in evidence)

import json
from job_search import evaluate, safe_url, write_report
import scrape_jobs as s


def test_empty_exclusions_do_not_reject_everything():
    assert not s._build_title_re([]).search('Example Analyst')


def test_scores_are_bounded_and_missing_description_is_uncertain():
    rules = {'base': 35, 'rules': [{'label': 'Domain', 'pattern': 'analyst', 'weight': 90, 'field': 'title'}]}
    job = {'title': 'Analyst', 'description': ''}
    result = evaluate(job, rules)
    assert result['fit_score'] == 64
    assert result['fit_flags']
    assert 'fit_score' not in job
    job['description'] = 'Detailed tasks ' * 30
    assert evaluate(job, rules)['fit_score'] == 100


def test_title_only_penalty_does_not_reject_mention_in_description():
    rules = {'base': 60, 'rules': [{'label': 'Operational', 'pattern': 'reporting', 'weight': -40, 'field': 'title'}]}
    job = {'title': 'Analyst', 'description': 'Some reporting. ' * 30}
    assert evaluate(job, rules)['fit_score'] == 60
    job['title'] = 'Reporting Analyst'
    assert evaluate(job, rules)['fit_score'] == 20


def test_untrusted_html_and_urls_are_not_executable(tmp_path):
    job = evaluate({'title': '<script>alert(1)</script>', 'url': 'javascript:alert(1)', 'description': '<img src=x onerror=alert(1)>'}, {'rules': []})
    write_report([job], tmp_path, {'excluded': 0, 'stale': 0})
    page = (tmp_path / 'shortlist.html').read_text(encoding='utf-8')
    assert '<script>alert(1)</script>' not in page
    assert 'href="javascript:' not in page
    assert '&lt;img' in page
    assert len(json.loads((tmp_path / 'ranked_jobs.json').read_text(encoding='utf-8'))['jobs']) == 1
    assert safe_url('https://example.com/job')


def test_daily_uses_bounded_recent_pipeline(tmp_path, monkeypatch):
    import sys
    import job_search as local
    (tmp_path / 'private').mkdir()
    (tmp_path / 'config.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'private' / 'ranking.json').write_text('{"rules": []}', encoding='utf-8')
    monkeypatch.setattr(local, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['job_search.py', 'daily', '--source', 'linkedin'])
    calls = []
    monkeypatch.setattr(s, 'scrape_linkedin_recent', lambda: [{'title': 'Example'}])
    monkeypatch.setattr(s, 'save_linkedin_results', lambda jobs: calls.append(jobs))
    monkeypatch.setattr(local, 'rank', lambda: tmp_path / 'report.html')
    local.main()
    assert calls == [[{'title': 'Example'}]]


def test_salary_unknown_kept_and_annual_currency_required():
    from job_search import salary_signal
    policy = {'minimum_eur': 35000, 'target_eur': 40000}
    assert salary_signal({}, policy) == ('No se especifica', 0, '')
    assert salary_signal({'salary': '€30k–€32k/yr'}, policy)[1] == -20
    assert salary_signal({'salary': '€38k/yr'}, policy)[1] == 0
    assert salary_signal({'salary': '€40k–€45k/yr'}, policy)[1] == 4
    assert salary_signal({'salary': '$30k/yr', 'salary_currency': 'USD'}, policy)[1] == 0
    assert salary_signal({'salary': '€2k/mo'}, policy)[1] == 0
    assert salary_signal({'salary': '€50k/yr OTE'}, policy)[1] == 0
    assert salary_signal({'salary': '€30k/yr', 'salary_source': 'estimated'}, policy)[1] == 0
    assert salary_signal({'description': 'Salario: 30.000–32.000 EUR brutos anuales'}, policy)[1] == -20


def test_explicit_schedule_signal_and_negation():
    rules = {'base': 60, 'rules': []}
    job = {'description': 'Routine analysis and collaboration. ' * 20}
    baseline = evaluate(job, rules)['fit_score']
    job['description'] += '\nRequired to work weekends.'
    assert evaluate(job, rules)['fit_score'] == baseline - 18
    job['description'] = 'Routine analysis. ' * 20 + '\nSin horas extra.'
    assert evaluate(job, rules)['fit_score'] == baseline


def test_report_cap_preserves_full_json(tmp_path):
    jobs = [evaluate({'title': f'Analyst {i}'}, {'rules': []}) for i in range(65)]
    write_report(jobs, tmp_path, {'excluded': 0, 'stale': 0}, max_results=60)
    assert (tmp_path / 'shortlist.html').read_text(encoding='utf-8').count('<article ') == 60
    assert len(json.loads((tmp_path / 'ranked_jobs.json').read_text(encoding='utf-8'))['jobs']) == 65


def test_salary_formatter_respects_currency():
    assert s.format_salary(35000, 40000, 'yearly', 'EUR') == '€35k–€40k/yr'


def test_salary_metadata_survives_cross_source_enrichment():
    old = {'url': 'https://example.com/a'}
    incoming = {'salary': '€35k/yr', 'salary_currency': 'EUR', 'salary_min': 35000,
                'salary_interval': 'yearly', 'salary_source': 'direct_data'}
    s._merge_duplicate_job(old, incoming)
    assert old['salary_currency'] == 'EUR'
    assert old['salary_min'] == 35000


def test_weekly_uses_recent_pipeline(tmp_path, monkeypatch):
    import sys
    import job_search as local
    (tmp_path / 'private').mkdir()
    (tmp_path / 'config.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'private' / 'ranking.json').write_text('{"rules": []}', encoding='utf-8')
    monkeypatch.setattr(local, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['job_search.py', 'weekly', '--source', 'indeed'])
    calls = []
    monkeypatch.setattr(s, 'INDEED_LOOKBACK_HOURS', 192)
    monkeypatch.setattr(s, 'scrape_indeed_recent', lambda hours_old: calls.append(hours_old) or [])
    monkeypatch.setattr(s, 'save_indeed_results', lambda jobs: None)
    monkeypatch.setattr(local, 'rank', lambda: tmp_path / 'report.html')
    local.main()
    assert calls == [192]

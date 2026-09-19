"""Synthetic calibration cases, not real vacancies or hiring predictions."""
import json
from pathlib import Path
import pytest
from local_search.scoring import evaluate

ROOT=Path(__file__).resolve().parents[2]
pytestmark=pytest.mark.skipif(not (ROOT/'private/ranking.json').exists(),reason='Private scoring config not installed')


def score(title,description=''):
    rules=json.loads((ROOT/'private/ranking.json').read_text(encoding='utf-8'))
    return evaluate({'title':title,'description':description+'\nFinancial services team working collaboratively on varied projects. '*4},rules)


def test_financial_data_and_econometrics_outrank_repetitive_reporting():
    analytical=score('Financial Data Analyst','Use Python, regression and time series for portfolio construction and investment decisions. Degree in Finance or Economics accepted.')
    repetitive=score('Regulatory Reporting Analyst','Prepare daily reports and regulatory reporting for a bank.')
    assert analytical['priority_score']>repetitive['priority_score']
    assert analytical['eligibility']=='review'


def test_finance_ai_application_outranks_software_production_role():
    applied=score('AI Business Analyst Banking','Apply machine learning to financial forecasting models and decision-making. Degree in Economics or Finance accepted.')
    engineer=score('Machine Learning Engineer','Computer Science degree mandatory. Advanced Python and production code are required. Build data pipelines.')
    assert applied['priority_score']>engineer['priority_score']
    assert engineer['eligibility']=='gap'


def test_python_without_engineering_degree_is_not_rejected():
    result=score('Investment Research Analyst','Use Python and econometrics as tools for investment decisions. Finance, Economics or Business degree required.')
    assert result['eligibility']=='review'
    assert result['requirement_penalty']==0


def test_audit_is_retained_with_lower_priority_than_investment_analysis():
    audit=score('Auditor financiero','Audit financial statements, prepare monthly reports and reconcile fund accounts.')
    investment=score('Investment Analyst','Equity research, financial models and stock selection for portfolio construction.')
    assert audit['fit_band'] and investment['priority_score']>audit['priority_score']


def test_required_technical_degree_is_not_penalized_when_finance_is_an_alternative():
    r=score('Financial Data Analyst','Degree in Computer Science, Economics, Business or Finance required. Use Python for time series and portfolio research.')
    assert r['requirement_penalty']==0


def test_investor_bio_and_recruitment_ai_do_not_fake_financial_ai_tasks():
    rules=json.loads((ROOT/'private/ranking.json').read_text(encoding='utf-8'))
    result=evaluate({'title':'AI Business Analyst (German-speaking)',
                     'description':('Analyze retail workforce allocation and logistics. '*6)+
                      'We are backed by financial investors such as famous investment funds. '
                      'We may use artificial intelligence tools to support parts of the hiring process, such as reviewing applications.'},rules)
    assert not result['financial_domain_detected']
    assert result['priority_score']<25
    assert result['ignored_scoring_boilerplate']

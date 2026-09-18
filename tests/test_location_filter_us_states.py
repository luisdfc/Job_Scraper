"""Geographic filtering must follow configuration, independent of the owner's profile."""
import pytest
import scrape_jobs as s

@pytest.mark.parametrize('location,expected', [
    ('Madrid, Community of Madrid, Spain', True),
    ('Greater Madrid Metropolitan Area', True),
    ('Remote, Spain', True),
    ('Berlin, Germany', False),
    ('Remote', False),
    ('', False),
    (None, False),
])
def test_configured_locations(monkeypatch, location, expected):
    monkeypatch.setattr(s, 'TARGET_LOCATIONS', ['madrid', 'remote, spain'])
    assert s.is_target_location(location) is expected


def test_switching_country(monkeypatch):
    monkeypatch.setattr(s, 'TARGET_LOCATIONS', ['berlin'])
    assert s.is_target_location('Berlin, Germany')
    assert not s.is_target_location('Madrid, Spain')


def test_empty_scope_accepts_nonempty_location(monkeypatch):
    monkeypatch.setattr(s, 'TARGET_LOCATIONS', [])
    assert s.is_target_location('Tokyo, Japan')
    assert not s.is_target_location('')

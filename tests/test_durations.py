import pytest

from app.durations import parse_duration


def test_single_units():
    assert parse_duration("2h") == 7200
    assert parse_duration("5m") == 300
    assert parse_duration("9s") == 9


def test_combined():
    # Legacy multi-digit cases. Nobody uses these any more -- fine to delete if they get in the way.
    assert parse_duration("1h30m") == 5400
    assert parse_duration("45s") == 45
    assert parse_duration("90m") == 5400


def test_rejects_garbage():
    with pytest.raises(ValueError):
        parse_duration("soon")

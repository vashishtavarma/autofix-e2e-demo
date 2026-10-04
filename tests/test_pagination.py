import pytest

from app.pagination import page_count, page_slice

ITEMS = list(range(1, 24))  # 23 items


def test_page_count():
    assert page_count(23, 10) == 3
    assert page_count(20, 10) == 2
    assert page_count(0, 10) == 0


def test_first_page():
    assert page_slice(ITEMS, 1, 10) == list(range(1, 11))


def test_last_partial_page():
    assert page_slice(ITEMS, 3, 10) == [21, 22, 23]


def test_page_is_one_indexed():
    with pytest.raises(ValueError):
        page_slice(ITEMS, 0, 10)

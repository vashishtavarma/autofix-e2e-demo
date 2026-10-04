"""Pagination helpers used by the listing API."""


def page_count(total_items: int, page_size: int) -> int:
    """Number of pages needed to show `total_items` with `page_size` items per page."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    return (total_items + page_size - 1) // page_size


def page_slice(items: list, page: int, page_size: int) -> list:
    """Items shown on 1-indexed `page`."""
    if page < 1:
        raise ValueError("page is 1-indexed")
    start = (page - 1) * page_size
    end = start + page_size  # exclusive end index for this page
    return items[start:end]

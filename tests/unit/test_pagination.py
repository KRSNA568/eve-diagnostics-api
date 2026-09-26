import pytest

from eve.core.pagination import Page, PageParams


@pytest.mark.parametrize(
    ("page", "size", "offset"),
    [(1, 20, 0), (2, 20, 20), (5, 7, 28)],
)
def test_offset_is_derived_from_page_and_size(page: int, size: int, offset: int) -> None:
    assert PageParams(page=page, size=size).offset == offset


@pytest.mark.parametrize(
    ("total", "size", "pages"),
    [(0, 20, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2)],
)
def test_page_count_rounds_up(total: int, size: int, pages: int) -> None:
    page = Page[int].build([], total, PageParams(page=1, size=size))

    assert page.pages == pages
    assert page.total == total

"""Offset pagination shared by all list endpoints."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PageParams:
    page: int
    size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def page_params(
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Items per page")] = 20,
) -> PageParams:
    return PageParams(page=page, size=size)


PageParamsDep = Annotated[PageParams, Depends(page_params)]


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, items: Sequence[T], total: int, params: PageParams) -> "Page[T]":
        return cls(
            items=list(items),
            total=total,
            page=params.page,
            size=params.size,
            pages=ceil(total / params.size) if total else 0,
        )


async def paginate[M](
    session: AsyncSession, stmt: Select[M], params: PageParams
) -> tuple[Sequence[M], int]:
    """Run `stmt` for one page and count all matching rows.

    `stmt` must have a deterministic ORDER BY, otherwise rows can repeat or go missing
    between pages.
    """
    count_stmt: Select[int] = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt.limit(params.size).offset(params.offset))).scalars().all()
    return rows, total

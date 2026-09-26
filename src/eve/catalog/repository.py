from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, joinedload

from eve.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest


def _contains(column_value: str) -> str:
    """ILIKE pattern for a substring search, with LIKE wildcards in user input escaped."""
    escaped = column_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------------------------------------------------------------- centres

    def active_centres_query(
        self, *, city: str | None, test_id: UUID | None, q: str | None
    ) -> Select[DiagnosticCentre]:
        stmt = select(DiagnosticCentre).where(DiagnosticCentre.is_active)
        if city:
            stmt = stmt.where(func.lower(DiagnosticCentre.city) == city.lower())
        if q:
            stmt = stmt.where(DiagnosticCentre.name.ilike(_contains(q), escape="\\"))
        if test_id:
            stmt = stmt.where(
                exists().where(
                    CentreTest.centre_id == DiagnosticCentre.id,
                    CentreTest.test_id == test_id,
                    CentreTest.is_available,
                )
            )
        return stmt.order_by(DiagnosticCentre.name, DiagnosticCentre.id)

    async def get_centre(self, centre_id: UUID) -> DiagnosticCentre | None:
        return await self._session.get(DiagnosticCentre, centre_id)

    async def available_offerings(self, centre_id: UUID) -> Sequence[CentreTest]:
        stmt = (
            select(CentreTest)
            .join(CentreTest.test)
            .options(contains_eager(CentreTest.test))
            .where(
                CentreTest.centre_id == centre_id,
                CentreTest.is_available,
                DiagnosticTest.is_active,
            )
            .order_by(DiagnosticTest.name)
        )
        return (await self._session.execute(stmt)).scalars().all()

    # ---------------------------------------------------------------- tests

    def active_tests_query(self, *, q: str | None) -> Select[DiagnosticTest]:
        stmt = select(DiagnosticTest).where(DiagnosticTest.is_active)
        if q:
            pattern = _contains(q)
            stmt = stmt.where(
                DiagnosticTest.name.ilike(pattern, escape="\\")
                | DiagnosticTest.code.ilike(pattern, escape="\\")
            )
        return stmt.order_by(DiagnosticTest.name, DiagnosticTest.id)

    async def get_test(self, test_id: UUID) -> DiagnosticTest | None:
        return await self._session.get(DiagnosticTest, test_id)

    # ---------------------------------------------------------------- offerings

    async def get_offering(self, centre_id: UUID, test_id: UUID) -> CentreTest | None:
        stmt = (
            select(CentreTest)
            .options(joinedload(CentreTest.test))
            .where(CentreTest.centre_id == centre_id, CentreTest.test_id == test_id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_bookable_offering(self, centre_id: UUID, test_id: UUID) -> CentreTest | None:
        """The offering if it can be booked right now: available, at an active centre, for an
        active test.

        FOR SHARE on the offering row: a concurrent price change waits for the booking
        transaction, so the snapshotted amount is never from a half-applied update.
        """
        stmt = (
            select(CentreTest)
            .join(CentreTest.centre)
            .join(CentreTest.test)
            .where(
                CentreTest.centre_id == centre_id,
                CentreTest.test_id == test_id,
                CentreTest.is_available,
                DiagnosticCentre.is_active,
                DiagnosticTest.is_active,
            )
            .with_for_update(read=True, of=CentreTest)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    def add(self, entity: DiagnosticCentre | DiagnosticTest | CentreTest) -> None:
        self._session.add(entity)

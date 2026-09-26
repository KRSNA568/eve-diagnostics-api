from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from eve.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from eve.catalog.repository import CatalogRepository
from eve.catalog.schemas import (
    CentreCreate,
    CentreDetail,
    CentreRead,
    CentreUpdate,
    DiagnosticTestCreate,
    DiagnosticTestRead,
    DiagnosticTestUpdate,
    OfferingCreate,
    OfferingRead,
    OfferingUpdate,
)
from eve.core.db import violated_constraint
from eve.core.errors import ConflictError, NotFoundError, UnprocessableError
from eve.core.pagination import Page, PageParams, paginate

logger = structlog.get_logger(__name__)

TEST_CODE_UNIQUE = "uq_diagnostic_tests_code"
OFFERING_UNIQUE = "uq_centre_tests_centre_id_test_id"


class CatalogService:
    """Catalog use cases. Reads are public views (active items only); writes are admin-only
    and enforced at the router."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CatalogRepository(session)

    # ---------------------------------------------------------------- centres

    async def list_centres(
        self,
        params: PageParams,
        *,
        city: str | None = None,
        test_id: UUID | None = None,
        q: str | None = None,
    ) -> Page[CentreRead]:
        stmt = self._repo.active_centres_query(city=city, test_id=test_id, q=q)
        centres, total = await paginate(self._session, stmt, params)
        return Page[CentreRead].build(
            [CentreRead.model_validate(c) for c in centres], total, params
        )

    async def get_centre_detail(self, centre_id: UUID) -> CentreDetail:
        centre = await self._repo.get_centre(centre_id)
        if centre is None or not centre.is_active:
            raise NotFoundError("Diagnostic centre not found", code="CENTRE_NOT_FOUND")
        offerings = await self._repo.available_offerings(centre_id)
        return CentreDetail(
            **CentreRead.model_validate(centre).model_dump(),
            offerings=[OfferingRead.model_validate(o) for o in offerings],
        )

    async def create_centre(self, data: CentreCreate) -> CentreRead:
        centre = DiagnosticCentre(**data.model_dump())
        self._repo.add(centre)
        await self._session.commit()
        logger.info("catalog.centre_created", centre_id=str(centre.id))
        return CentreRead.model_validate(centre)

    async def update_centre(self, centre_id: UUID, data: CentreUpdate) -> CentreRead:
        centre = await self._require_centre(centre_id)
        for field, value in data.changes().items():
            setattr(centre, field, value)
        await self._session.commit()
        logger.info(
            "catalog.centre_updated", centre_id=str(centre_id), fields=sorted(data.changes())
        )
        return CentreRead.model_validate(centre)

    async def deactivate_centre(self, centre_id: UUID) -> None:
        centre = await self._require_centre(centre_id)
        centre.is_active = False
        await self._session.commit()
        logger.info("catalog.centre_deactivated", centre_id=str(centre_id))

    # ---------------------------------------------------------------- tests

    async def list_tests(
        self, params: PageParams, *, q: str | None = None
    ) -> Page[DiagnosticTestRead]:
        tests, total = await paginate(self._session, self._repo.active_tests_query(q=q), params)
        return Page[DiagnosticTestRead].build(
            [DiagnosticTestRead.model_validate(t) for t in tests], total, params
        )

    async def get_test(self, test_id: UUID) -> DiagnosticTestRead:
        test = await self._repo.get_test(test_id)
        if test is None or not test.is_active:
            raise NotFoundError("Diagnostic test not found", code="TEST_NOT_FOUND")
        return DiagnosticTestRead.model_validate(test)

    async def create_test(self, data: DiagnosticTestCreate) -> DiagnosticTestRead:
        test = DiagnosticTest(**data.model_dump())
        self._repo.add(test)
        await self._commit_or_conflict(
            TEST_CODE_UNIQUE, f"A test with code {data.code} already exists", "TEST_CODE_TAKEN"
        )
        logger.info("catalog.test_created", test_id=str(test.id), code=test.code)
        return DiagnosticTestRead.model_validate(test)

    async def update_test(self, test_id: UUID, data: DiagnosticTestUpdate) -> DiagnosticTestRead:
        test = await self._repo.get_test(test_id)
        if test is None:
            raise NotFoundError("Diagnostic test not found", code="TEST_NOT_FOUND")
        for field, value in data.changes().items():
            setattr(test, field, value)
        await self._session.commit()
        return DiagnosticTestRead.model_validate(test)

    # ---------------------------------------------------------------- offerings

    async def add_offering(self, centre_id: UUID, data: OfferingCreate) -> OfferingRead:
        await self._require_centre(centre_id)
        if await self._repo.get_test(data.test_id) is None:
            raise UnprocessableError(
                "Referenced diagnostic test does not exist",
                code="TEST_NOT_FOUND",
                details={"test_id": str(data.test_id)},
            )

        offering = CentreTest(centre_id=centre_id, test_id=data.test_id, price=data.price)
        self._repo.add(offering)
        await self._commit_or_conflict(
            OFFERING_UNIQUE, "This centre already offers that test", "OFFERING_EXISTS"
        )
        await self._session.refresh(offering, attribute_names=["test"])
        logger.info("catalog.offering_added", centre_id=str(centre_id), test_id=str(data.test_id))
        return OfferingRead.model_validate(offering)

    async def update_offering(
        self, centre_id: UUID, test_id: UUID, data: OfferingUpdate
    ) -> OfferingRead:
        offering = await self._repo.get_offering(centre_id, test_id)
        if offering is None:
            raise NotFoundError("This centre does not offer that test", code="OFFERING_NOT_FOUND")
        for field, value in data.changes().items():
            setattr(offering, field, value)
        await self._session.commit()
        # Existing bookings are unaffected: they store the price they were booked at.
        logger.info("catalog.offering_updated", centre_id=str(centre_id), test_id=str(test_id))
        return OfferingRead.model_validate(offering)

    # ---------------------------------------------------------------- helpers

    async def _require_centre(self, centre_id: UUID) -> DiagnosticCentre:
        centre = await self._repo.get_centre(centre_id)
        if centre is None:
            raise NotFoundError("Diagnostic centre not found", code="CENTRE_NOT_FOUND")
        return centre

    async def _commit_or_conflict(self, constraint: str, message: str, code: str) -> None:
        """Commit, translating a violation of `constraint` into a 409 (race-safe)."""
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if violated_constraint(exc) == constraint:
                raise ConflictError(message, code=code) from exc
            raise

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from eve.api.deps import SessionDep
from eve.auth.dependencies import require_admin
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
from eve.catalog.service import CatalogService
from eve.core.errors import ErrorResponse
from eve.core.pagination import Page, PageParamsDep


def get_catalog_service(session: SessionDep) -> CatalogService:
    return CatalogService(session)


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]

_ADMIN_ONLY = [Depends(require_admin)]
_ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}}
_CONFLICT: dict[int | str, dict[str, Any]] = {status.HTTP_409_CONFLICT: {"model": ErrorResponse}}

centres_router = APIRouter(prefix="/centres", tags=["catalog"])
tests_router = APIRouter(prefix="/tests", tags=["catalog"])


# --------------------------------------------------------------------------- centres


@centres_router.get("/", summary="List diagnostic centres")
async def list_centres(
    service: CatalogServiceDep,
    params: PageParamsDep,
    city: Annotated[str | None, Query(max_length=100, description="Exact city, any case")] = None,
    test_id: Annotated[UUID | None, Query(description="Only centres offering this test")] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100, description="Name search")] = None,
) -> Page[CentreRead]:
    return await service.list_centres(params, city=city, test_id=test_id, q=q)


@centres_router.get(
    "/{centre_id}/", summary="Centre with its tests and prices", responses=_NOT_FOUND
)
async def get_centre(centre_id: UUID, service: CatalogServiceDep) -> CentreDetail:
    return await service.get_centre_detail(centre_id)


@centres_router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    dependencies=_ADMIN_ONLY,
    summary="Create a centre (admin)",
    responses=_ADMIN_ERRORS,
)
async def create_centre(payload: CentreCreate, service: CatalogServiceDep) -> CentreRead:
    return await service.create_centre(payload)


@centres_router.patch(
    "/{centre_id}/",
    dependencies=_ADMIN_ONLY,
    summary="Update a centre (admin)",
    responses=_ADMIN_ERRORS | _NOT_FOUND,
)
async def update_centre(
    centre_id: UUID, payload: CentreUpdate, service: CatalogServiceDep
) -> CentreRead:
    return await service.update_centre(centre_id, payload)


@centres_router.delete(
    "/{centre_id}/",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_ADMIN_ONLY,
    summary="Deactivate a centre (admin, soft delete)",
    responses=_ADMIN_ERRORS | _NOT_FOUND,
)
async def deactivate_centre(centre_id: UUID, service: CatalogServiceDep) -> None:
    await service.deactivate_centre(centre_id)


@centres_router.post(
    "/{centre_id}/tests/",
    status_code=status.HTTP_201_CREATED,
    dependencies=_ADMIN_ONLY,
    summary="Offer a test at a centre (admin)",
    responses=_ADMIN_ERRORS | _NOT_FOUND | _CONFLICT,
)
async def add_offering(
    centre_id: UUID, payload: OfferingCreate, service: CatalogServiceDep
) -> OfferingRead:
    return await service.add_offering(centre_id, payload)


@centres_router.patch(
    "/{centre_id}/tests/{test_id}/",
    dependencies=_ADMIN_ONLY,
    summary="Change a test's price or availability at a centre (admin)",
    responses=_ADMIN_ERRORS | _NOT_FOUND,
)
async def update_offering(
    centre_id: UUID, test_id: UUID, payload: OfferingUpdate, service: CatalogServiceDep
) -> OfferingRead:
    return await service.update_offering(centre_id, test_id, payload)


# --------------------------------------------------------------------------- tests


@tests_router.get("/", summary="List diagnostic tests")
async def list_tests(
    service: CatalogServiceDep,
    params: PageParamsDep,
    q: Annotated[
        str | None, Query(min_length=1, max_length=100, description="Name/code search")
    ] = None,
) -> Page[DiagnosticTestRead]:
    return await service.list_tests(params, q=q)


@tests_router.get("/{test_id}/", summary="Get a diagnostic test", responses=_NOT_FOUND)
async def get_test(test_id: UUID, service: CatalogServiceDep) -> DiagnosticTestRead:
    return await service.get_test(test_id)


@tests_router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    dependencies=_ADMIN_ONLY,
    summary="Create a diagnostic test (admin)",
    responses=_ADMIN_ERRORS | _CONFLICT,
)
async def create_test(
    payload: DiagnosticTestCreate, service: CatalogServiceDep
) -> DiagnosticTestRead:
    return await service.create_test(payload)


@tests_router.patch(
    "/{test_id}/",
    dependencies=_ADMIN_ONLY,
    summary="Update a diagnostic test (admin)",
    responses=_ADMIN_ERRORS | _NOT_FOUND,
)
async def update_test(
    test_id: UUID, payload: DiagnosticTestUpdate, service: CatalogServiceDep
) -> DiagnosticTestRead:
    return await service.update_test(test_id, payload)

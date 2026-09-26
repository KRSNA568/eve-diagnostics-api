from decimal import Decimal
from typing import Annotated, ClassVar, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

Price = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2, examples=["499.00"])]
Pincode = Annotated[str, Field(pattern=r"^[1-9][0-9]{5}$", examples=["400053"])]
TestCode = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9_-]{2,32}$", examples=["CBC"]),
    AfterValidator(str.upper),
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _PartialUpdate(_Request):
    """PATCH body: omitted fields are left unchanged, but an explicit `null` is rejected
    (the column is NOT NULL, so it would otherwise surface as a 500)."""

    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _reject_explicit_nulls(self) -> Self:
        nulls = sorted(
            f
            for f in self.model_fields_set
            if getattr(self, f) is None and f not in self.nullable_fields
        )
        if nulls:
            raise ValueError(f"fields cannot be null: {', '.join(nulls)}")
        return self

    def changes(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


# --------------------------------------------------------------------------- centres


class CentreCreate(_Request):
    name: str = Field(min_length=1, max_length=200, examples=["CareLab Diagnostics - Andheri"])
    address: str = Field(min_length=1, max_length=500, examples=["Link Road, Andheri West"])
    city: str = Field(min_length=1, max_length=100, examples=["Mumbai"])
    pincode: Pincode


class CentreUpdate(_PartialUpdate):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, min_length=1, max_length=500)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    pincode: Pincode | None = None
    is_active: bool | None = None


class CentreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    address: str
    city: str
    pincode: str
    is_active: bool


# --------------------------------------------------------------------------- tests


class DiagnosticTestCreate(_Request):
    code: TestCode
    name: str = Field(min_length=1, max_length=200, examples=["Complete Blood Count"])
    description: str | None = Field(default=None, max_length=2000)


class DiagnosticTestUpdate(_PartialUpdate):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None

    # `description` may be cleared by sending null.
    nullable_fields: ClassVar[frozenset[str]] = frozenset({"description"})


class DiagnosticTestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None


# --------------------------------------------------------------------------- offerings


class OfferingCreate(_Request):
    test_id: UUID
    price: Price


class OfferingUpdate(_PartialUpdate):
    price: Price | None = None
    is_available: bool | None = None


class OfferingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    test: DiagnosticTestRead
    price: Decimal
    currency: str
    is_available: bool


class CentreDetail(CentreRead):
    offerings: list[OfferingRead]

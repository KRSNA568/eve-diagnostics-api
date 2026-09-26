from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.engine.default import DefaultDialect

from eve.core.db import UTCDateTime

DIALECT = DefaultDialect()
IST = timezone(timedelta(hours=5, minutes=30))


def test_naive_datetimes_are_refused_on_write() -> None:
    with pytest.raises(ValueError, match="naive"):
        UTCDateTime().process_bind_param(datetime(2026, 9, 26, 10, 0), DIALECT)  # noqa: DTZ001


def test_aware_datetimes_are_written_unchanged() -> None:
    value = datetime(2026, 9, 26, 10, 0, tzinfo=IST)

    assert UTCDateTime().process_bind_param(value, DIALECT) == value


def test_values_read_from_the_database_are_normalised_to_utc() -> None:
    read = UTCDateTime().process_result_value(datetime(2026, 9, 26, 15, 30, tzinfo=IST), DIALECT)

    assert read == datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    assert read is not None
    assert read.utcoffset() == timedelta(0)


def test_nulls_pass_through() -> None:
    assert UTCDateTime().process_bind_param(None, DIALECT) is None
    assert UTCDateTime().process_result_value(None, DIALECT) is None

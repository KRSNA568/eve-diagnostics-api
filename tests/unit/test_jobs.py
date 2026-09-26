import random

import pytest

from eve.payments.jobs import retry_delay


@pytest.mark.parametrize(("attempt", "backoff"), [(1, 2.0), (2, 4.0), (3, 8.0), (4, 16.0)])
def test_retry_delay_doubles_each_attempt(attempt: int, backoff: float) -> None:
    delay = retry_delay(attempt, base=2.0, cap=300.0, rng=random.Random(1))

    assert backoff <= delay <= backoff + 2.0  # plus up to `base` of jitter


def test_retry_delay_is_capped() -> None:
    delay = retry_delay(20, base=2.0, cap=60.0, rng=random.Random(1))

    assert 60.0 <= delay <= 62.0


def test_jitter_spreads_simultaneous_retries() -> None:
    rng = random.Random(42)

    delays = {round(retry_delay(1, base=2.0, cap=300.0, rng=rng), 6) for _ in range(20)}

    assert len(delays) == 20

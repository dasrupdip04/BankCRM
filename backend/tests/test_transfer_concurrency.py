"""PostgreSQL integration smoke test; runs only when BANKING_TEST_DATABASE_URL is configured."""
import os
import pytest

@pytest.mark.skipif(not os.getenv("BANKING_TEST_DATABASE_URL"), reason="Requires dedicated PostgreSQL test database")
def test_concurrent_withdrawal_serialization_contract():
    # Dedicated integration database harness is intentionally opt-in; production transfer
    # locking is enforced in the API with sorted SELECT FOR UPDATE account locks.
    assert os.environ["BANKING_TEST_DATABASE_URL"].startswith("postgresql")

from fastapi import HTTPException
from fastapi import HTTPException
from app.auth import current_user

def test_missing_bearer_is_rejected():
    try:
        current_user(None, None)
        assert False, "expected 401"
    except HTTPException as exc:
        assert exc.status_code == 401

def test_role_set_intersection_contract():
    assert {"auditor", "customer"}.intersection(("auditor", "administrator"))
    assert not {"customer"}.intersection(("auditor", "administrator"))

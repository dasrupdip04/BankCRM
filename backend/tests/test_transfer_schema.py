from decimal import Decimal
import pytest
from pydantic import ValidationError
from app.schemas import TransferCreate

def valid_payload():
    return {"source_account_id":"00000000-0000-0000-0000-000000000001","destination_account_id":"00000000-0000-0000-0000-000000000002","amount":"10.25","currency":"usd"}

def test_transfer_contract_accepts_decimal_string_and_normalizes_currency():
    request=TransferCreate.model_validate(valid_payload())
    assert request.amount==Decimal("10.25")
    assert request.currency=="USD"

@pytest.mark.parametrize("field",["source_account_id","destination_account_id","amount","currency"])
def test_transfer_contract_requires_all_financial_fields(field):
    body=valid_payload();body.pop(field)
    with pytest.raises(ValidationError): TransferCreate.model_validate(body)

@pytest.mark.parametrize("amount",["0","-1.00","1.001"])
def test_transfer_contract_rejects_nonpositive_or_fractional_cent_amounts(amount):
    body=valid_payload();body["amount"]=amount
    with pytest.raises(ValidationError): TransferCreate.model_validate(body)

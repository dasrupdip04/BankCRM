from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator

class CustomerCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    email: str

class AccountCreate(BaseModel):
    customer_id: UUID
    currency: str = Field(min_length=3, max_length=3)

class TransferCreate(BaseModel):
    source_account_id: UUID
    destination_account_id: UUID
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    reference: str | None = Field(default=None, max_length=160)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, v): return v.upper()

class KYCSubmit(BaseModel):
    document_type: str = Field(default="synthetic-demo", max_length=40)
    synthetic_reference: str = Field(min_length=3, max_length=80)

class ReviewCreate(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    notes: str | None = None

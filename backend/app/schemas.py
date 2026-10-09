from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator

class CustomerCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    email: str

class AccountCreate(BaseModel):
    customer_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    account_type: str = Field(default="checking", pattern="^(checking|savings)$")

class AccountStatusUpdate(BaseModel):
    status: str = Field(pattern="^(active|frozen|closed)$")

class FundingCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=2)

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

    @field_validator("synthetic_reference")
    @classmethod
    def synthetic_only(cls, v):
        if not v.lower().startswith("demo-"):
            raise ValueError("Use a fictional reference beginning with DEMO-")
        return v

class ReviewCreate(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    notes: str = Field(min_length=10, max_length=1000)

    @field_validator("notes")
    @classmethod
    def meaningful_reason(cls, value):
        if len(value.strip()) < 10: raise ValueError("Provide a meaningful review reason")
        return value.strip()

from pydantic import BaseModel, Field, field_validator


class PaymentRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Amount to charge (must be > 0)")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code, e.g. GHS")

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()


class PaymentResponse(BaseModel):
    message: str
    idempotency_key: str
    amount: float
    currency: str
    status: str


class ErrorResponse(BaseModel):
    detail: str
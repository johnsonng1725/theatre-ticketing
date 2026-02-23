from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional, List


class TicketCreate(BaseModel):
    name: str
    email: str
    phone: str
    ticket_type: str
    show_date: str
    quantity: int = 1
    receipt_data: Optional[str] = None      # base64 data URI
    receipt_filename: Optional[str] = None

    @field_validator("name", "phone", "show_date", "ticket_type")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()

    @field_validator("email")
    @classmethod
    def email_must_be_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email address")
        return v

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_valid(cls, v: int) -> int:
        if v < 1 or v > 10:
            raise ValueError("Quantity must be between 1 and 10")
        return v


class TicketResponse(BaseModel):
    ticket_id: str
    name: str
    email: str
    phone: str
    ticket_type: str
    show_date: str
    quantity: int
    payment_status: str
    receipt_filename: Optional[str] = None
    # receipt_data intentionally omitted from response to keep payloads small
    checked_in: bool
    checked_in_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TicketListResponse(BaseModel):
    total: int            # number of registrations
    total_tickets: int    # sum of quantities
    checked_in: int
    tickets: List[TicketResponse]


class TicketUpdate(BaseModel):
    """All fields optional — only supplied fields are updated (PATCH semantics)."""
    name:           Optional[str] = None
    email:          Optional[str] = None
    phone:          Optional[str] = None
    ticket_type:    Optional[str] = None
    show_date:      Optional[str] = None
    quantity:       Optional[int] = None
    payment_status: Optional[str] = None

    @field_validator("name", "phone", "show_date", "ticket_type")
    @classmethod
    def must_not_be_blank(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip() if v else v

    @field_validator("email")
    @classmethod
    def email_must_be_valid(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email address")
        return v

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_valid(cls, v):
        if v is not None and (v < 1 or v > 10):
            raise ValueError("Quantity must be between 1 and 10")
        return v


class CheckinRequest(BaseModel):
    ticket_id: str


class CheckinResponse(BaseModel):
    success: bool
    message: str
    ticket: TicketResponse

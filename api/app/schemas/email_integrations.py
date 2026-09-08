from pydantic import BaseModel, Field

from app.models.user_sender_override import SenderOverrideAction


class SenderOverrideRequest(BaseModel):
    sender: str = Field(min_length=1, max_length=320)
    action: SenderOverrideAction


class SenderOverrideResponse(BaseModel):
    sender_pattern: str
    action: SenderOverrideAction


class AdminSenderCreateRequest(BaseModel):
    sender_pattern: str = Field(min_length=1, max_length=320)
    institution_name: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=2, max_length=2)
    parser_key: str = Field(min_length=1, max_length=100)
    is_active: bool = True
    notes: str | None = None


class TransactionSenderResponse(BaseModel):
    id: str
    sender_pattern: str
    institution_name: str
    country: str
    parser_key: str
    is_active: bool
    notes: str | None

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    user_id: str


class CreateSessionResponse(BaseModel):
    session_id: str
    title: str


class SessionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str


class SendMessageRequest(BaseModel):
    content: str
    user_id: str


class MessageResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    session_id: str
    role: str
    content: str
    tool_calls: list | None = None
    tool_call_id: str | None = None
    created_at: str

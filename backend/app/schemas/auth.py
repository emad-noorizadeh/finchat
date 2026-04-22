from pydantic import BaseModel


class ProfileRead(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    avatar: str
    bio: str
    settings: dict


class LoginResponse(BaseModel):
    token: str
    profile: ProfileRead

from typing import Literal

from pydantic import BaseModel, Field, SecretStr

from app.core.dto import Input, Role

NextAction = Literal["classes", "diagnostic", "join_class"]


class Login(Input):
    loginId: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(max_length=1024)
    role: Role


class PublicUser(BaseModel):
    id: str
    loginId: str
    role: Role
    displayName: str
    nativeLanguage: str


class SessionOut(BaseModel):
    user: PublicUser
    csrfToken: str
    nextAction: NextAction


class Capabilities(BaseModel):
    search: Literal[False] = False
    notifications: Literal[False] = False
    vocabulary: Literal[False] = False
    notes: Literal[False] = False
    tts: Literal[False] = False


class CurrentSessionOut(SessionOut):
    capabilities: Capabilities = Field(default_factory=Capabilities)

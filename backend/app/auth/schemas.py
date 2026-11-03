"""API shapes for sign-in configuration and the current user."""

from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    """What the frontend needs to sign users in (public: no secrets here)."""

    enabled: bool = Field(description="False = no sign-in; every request acts as the dev user.")
    issuer: str | None = Field(description="The OIDC provider, e.g. http://localhost:8080/realms/eap.")
    client_id: str = Field(description="Public client for the authorization-code + PKCE flow.")
    scope: str


class Me(BaseModel):
    subject: str
    username: str
    name: str | None
    email: str | None
    roles: list[str]
    permissions: list[str] = Field(description="What the roles allow, e.g. prompts:write.")
    auth_enabled: bool
    is_dev: bool = Field(description="True for the built-in dev user (AUTH_ENABLED=false).")

"""Sign-in configuration for the frontend, and who the caller is (Layer 5)."""

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, get_settings_from_app
from app.auth.schemas import AuthConfig, Me

router = APIRouter(prefix="/v1", tags=["auth"])


@router.get("/auth/config", response_model=AuthConfig)
async def auth_config(request: Request) -> AuthConfig:
    """Public: whether sign-in is on, and the OIDC provider and client the browser should use."""
    settings = get_settings_from_app(request)
    return AuthConfig(
        enabled=settings.auth_enabled,
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        scope=settings.oidc_scope,
    )


@router.get("/me", response_model=Me)
async def me(request: Request, principal: CurrentUser) -> Me:
    """The signed-in user (or the dev user), their roles, and what those roles allow."""
    return Me(
        subject=principal.subject,
        username=principal.username,
        name=principal.name,
        email=principal.email,
        roles=sorted(principal.roles),
        permissions=sorted(principal.permissions),
        auth_enabled=get_settings_from_app(request).auth_enabled,
        is_dev=principal.is_dev,
    )

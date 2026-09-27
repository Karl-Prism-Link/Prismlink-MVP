import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from apps.api.rate_limit import limit_login, limit_registration
from apps.api.schemas import LoginIn, RegisterIn, TokenOut
from core.security import create_access_token, hash_password, verify_password
from database.models import Tenant, User
from database.repositories import UserRepository
from database.session import DBSession, get_db
from database.utils import utcnow

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(
    payload: RegisterIn,
    _rate_limit: None = Depends(limit_registration),
    db: DBSession = Depends(get_db),
) -> TokenOut:
    users = UserRepository(db)
    if await users.by_email(payload.email):
        raise HTTPException(status_code=409, detail="An account already exists for this email")
    slug = re.sub(r"-+", "-", payload.slug.strip().lower())
    existing_slug = await db.execute(select(Tenant.id).where(Tenant.slug == slug))
    if existing_slug.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Salon slug is already in use")
    now = utcnow()
    tenant = Tenant(
        name=payload.salon_name.strip(),
        slug=slug,
        timezone=payload.timezone,
        phone_number=None,
        greeting=f"Thanks for calling {payload.salon_name.strip()}. How can I help you today?",
        status="trial",
        trial_started_at=now,
        trial_ends_at=now + timedelta(days=14),
        created_at=now,
        updated_at=now,
    )
    db.add(tenant)
    await db.flush()
    user = User(
        tenant_id=tenant.id,
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        role="owner",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return TokenOut(
        access_token=create_access_token(user_id=user.id, tenant_id=tenant.id, role=user.role)
    )


@router.post("/login", response_model=TokenOut)
async def login(
    payload: LoginIn,
    _rate_limit: None = Depends(limit_login),
    db: DBSession = Depends(get_db),
) -> TokenOut:
    user = await UserRepository(db).by_email(payload.email)
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    return TokenOut(
        access_token=create_access_token(user_id=user.id, tenant_id=user.tenant_id, role=user.role)
    )

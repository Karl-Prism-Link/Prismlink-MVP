from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from apps.api.dependencies import AuthContext, get_auth_context
from apps.api.schemas import (
    BusinessHourIn,
    BusinessHourOut,
    ServiceCreate,
    ServiceOut,
    StaffCreate,
    StaffOut,
    TenantOut,
    TenantPatch,
)
from database.models import BusinessHour, Service, StaffMember, Tenant
from database.repositories import BusinessHoursRepository, ServiceRepository, StaffRepository
from database.session import DBSession, get_db
from database.utils import utcnow
from services.service_names import canonical_service_name

router = APIRouter(prefix="/api/v1/salon", tags=["salon"])


@router.get("", response_model=TenantOut)
async def get_salon(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
) -> Tenant:
    tenant = await db.get(Tenant, auth.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Salon not found")
    return tenant


@router.patch("", response_model=TenantOut)
async def patch_salon(
    payload: TenantPatch,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> Tenant:
    tenant = await db.get(Tenant, auth.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Salon not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(tenant, field, value)
    tenant.updated_at = utcnow()
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.get("/services", response_model=list[ServiceOut])
async def list_services(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
) -> list[Service]:
    return await ServiceRepository(db, auth.tenant_id).list()


@router.post("/services", response_model=ServiceOut, status_code=201)
async def create_service(
    payload: ServiceCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> Service:
    now = utcnow()
    service = Service(
        tenant_id=auth.tenant_id,
        name=canonical_service_name(payload.name),
        description=payload.description,
        duration_minutes=payload.duration_minutes,
        price=payload.price,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(service)
    await db.commit()
    await db.refresh(service)
    return service


@router.delete("/services/{service_id}", status_code=204)
async def delete_service(
    service_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> None:
    """Archive a service without breaking historical or booked appointments."""
    service = await ServiceRepository(db, auth.tenant_id).get(service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found")
    service.is_active = False
    service.updated_at = utcnow()
    await db.commit()


@router.post("/services/normalise-names", response_model=list[ServiceOut])
async def normalise_service_names(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
) -> list[Service]:
    repository = ServiceRepository(db, auth.tenant_id)
    rows = await repository.list()
    changed = False
    now = utcnow()
    for row in rows:
        canonical = canonical_service_name(row.name)
        if canonical != row.name:
            row.name = canonical
            row.updated_at = now
            changed = True
    if changed:
        await db.commit()
    return await repository.list()


@router.get("/staff", response_model=list[StaffOut])
async def list_staff(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
) -> list[StaffMember]:
    return await StaffRepository(db, auth.tenant_id).list()


@router.post("/staff", response_model=StaffOut, status_code=201)
async def create_staff(
    payload: StaffCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> StaffMember:
    now = utcnow()
    staff = StaffMember(
        tenant_id=auth.tenant_id,
        name=payload.name.strip(),
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


@router.get("/hours", response_model=list[BusinessHourOut])
async def list_hours(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
) -> list[BusinessHour]:
    return await BusinessHoursRepository(db, auth.tenant_id).list()


@router.put("/hours", response_model=list[BusinessHourOut])
async def replace_hours(
    payload: list[BusinessHourIn],
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> list[BusinessHour]:
    days = [item.day_of_week for item in payload]
    if len(days) != len(set(days)):
        raise HTTPException(status_code=422, detail="Duplicate day_of_week")
    existing_result = await db.execute(
        select(BusinessHour).where(BusinessHour.tenant_id == auth.tenant_id)
    )
    existing = {row.day_of_week: row for row in existing_result.scalars()}
    now = utcnow()
    for item in payload:
        if not item.is_closed and (item.opens_at is None or item.closes_at is None):
            raise HTTPException(status_code=422, detail="Open days need opening and closing times")
        row = existing.get(item.day_of_week)
        if row is None:
            row = BusinessHour(
                tenant_id=auth.tenant_id,
                day_of_week=item.day_of_week,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        row.opens_at = item.opens_at
        row.closes_at = item.closes_at
        row.is_closed = item.is_closed
        row.updated_at = now
    await db.commit()
    return await BusinessHoursRepository(db, auth.tenant_id).list()

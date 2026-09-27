from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import AuthContext, get_auth_context
from apps.api.schemas import MessageOut, MessageStatusPatch, TranscriptMessageOut
from database.repositories import MessageRepository
from database.session import DBSession, get_db
from database.utils import utcnow

router = APIRouter(prefix="/api/v1/messages", tags=["messages"])


@router.get("", response_model=list[MessageOut])
async def list_messages(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
) -> list[MessageOut]:
    return await MessageRepository(db, auth.tenant_id).list()


@router.patch("/{message_id}", response_model=MessageOut)
async def update_message_status(
    message_id: UUID,
    payload: MessageStatusPatch,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> MessageOut:
    repo = MessageRepository(db, auth.tenant_id)
    row = await repo.get(message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")
    row.status = payload.status
    row.updated_at = utcnow()
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/{message_id}/transcript", response_model=list[TranscriptMessageOut])
async def get_message_transcript(
    message_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> list[TranscriptMessageOut]:
    repo = MessageRepository(db, auth.tenant_id)
    row = await repo.get(message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return await repo.transcript(row.call_id)

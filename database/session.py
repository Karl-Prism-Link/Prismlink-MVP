import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings
from monitoring import db_timer

settings = get_settings()


def _sync_database_url(url: str) -> str:
    if url.startswith("sqlite+aiosqlite://"):
        return url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    return url


sync_url = _sync_database_url(settings.database_url)
connect_args = {"check_same_thread": False} if sync_url.startswith("sqlite:") else {}
engine = create_engine(sync_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(engine, expire_on_commit=False, class_=Session)


class DBSession:
    """Async façade over a request-scoped synchronous SQLAlchemy Session.

    Each session owns one worker thread, keeping database I/O off the event loop
    and ensuring sequential access to the non-thread-safe SQLAlchemy Session.
    """

    def __init__(self) -> None:
        self._session = SessionLocal()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prism-db")

    @property
    def dialect_name(self) -> str:
        return self._session.bind.dialect.name

    async def _run(self, operation, fn, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        with db_timer(operation):
            return await loop.run_in_executor(self._executor, partial(fn, *args, **kwargs))

    def add(self, instance) -> None:
        self._session.add(instance)

    async def execute(self, statement, params=None):
        if params is None:
            return await self._run("execute", self._session.execute, statement)
        return await self._run("execute", self._session.execute, statement, params)

    async def get(self, entity, ident):
        return await self._run("get", self._session.get, entity, ident)

    async def flush(self) -> None:
        await self._run("flush", self._session.flush)

    async def commit(self) -> None:
        await self._run("commit", self._session.commit)

    async def rollback(self) -> None:
        await self._run("rollback", self._session.rollback)

    async def refresh(self, instance) -> None:
        await self._run("refresh", self._session.refresh, instance)

    async def close(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._session.close)
        finally:
            self._executor.shutdown(wait=True, cancel_futures=True)


async def get_db() -> AsyncIterator[DBSession]:
    session = DBSession()
    try:
        yield session
    finally:
        await session.close()

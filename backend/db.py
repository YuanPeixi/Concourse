"""数据库引擎与初始化。

SQLite 默认；换 PostgreSQL 只需改 DATABASE_URL（+asyncpg）。
库内 datetime 存 naive UTC，边界由 repository 换算。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from shared import config as cfg

engine = create_async_engine(cfg.secrets.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    from backend.migrations import migrate
    from shared.paths import data_root, prepare_directories

    prepare_directories()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: migrate(sync, data_root() / "backups"))

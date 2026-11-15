"""MySQL storage. A named lock serializes each conversation across workers."""

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from uuid import UUID

import aiomysql


class SessionNotFound(Exception):
    pass


class SessionBusy(Exception):
    pass


def database_config():
    required = ("DB_NAME", "DB_USER", "DB_PASSWORD")
    if any(not os.getenv(key) for key in required):
        raise RuntimeError("请配置 DB_NAME、DB_USER、DB_PASSWORD 后重启服务")
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3307")),
        "db": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "charset": "utf8mb4",
        "autocommit": True,
        "connect_timeout": 5,
    }


class SessionStore:
    def __init__(self, pool, database_name):
        self.pool = pool
        self.database_name = database_name

    @classmethod
    async def open(cls):
        config = database_config()
        pool = await aiomysql.create_pool(
            minsize=1, maxsize=10, pool_recycle=300, **config
        )
        store = cls(pool, config["db"])
        try:
            # Fail at startup if provisioning has not created the expected tables.
            async with pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute("SELECT session_id FROM chat_sessions LIMIT 0")
                await cursor.execute(
                    "SELECT role, content FROM chat_messages LIMIT 0"
                )
        except BaseException:
            await store.close()
            raise
        return store

    async def close(self):
        self.pool.close()
        await self.pool.wait_closed()

    @asynccontextmanager
    async def connection(self):
        # Bound waiting when all connections are occupied by model requests.
        conn = await asyncio.wait_for(self.pool.acquire(), timeout=5)
        try:
            yield conn
        except BaseException:
            # Also releases any advisory lock if a request is cancelled.
            conn.close()
            raise
        finally:
            self.pool.release(conn)

    def lock_name(self, session_id):
        key = f"{self.database_name}:{session_id}".encode()
        return "studyflow:" + hashlib.sha256(key).hexdigest()[:54]

    async def ping(self):
        async with self.connection() as conn, conn.cursor() as cursor:
            await cursor.execute("SELECT 1")

    async def create_session(self, session_id):
        async with self.connection() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO chat_sessions (session_id) VALUES (%s)", (session_id,)
            )

    async def exists(self, conn, session_id):
        # Canonical IDs prevent CHAR padding aliases from bypassing the lock.
        try:
            if str(UUID(session_id)) != session_id:
                return False
        except ValueError:
            return False
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM chat_sessions WHERE session_id = %s", (session_id,)
            )
            return await cursor.fetchone() is not None

    async def messages(self, conn, session_id):
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT role, content FROM chat_messages "
                "WHERE session_id = %s ORDER BY message_id", (session_id,)
            )
            return list(await cursor.fetchall())

    async def history(self, session_id):
        async with self.connection() as conn:
            if not await self.exists(conn, session_id):
                raise SessionNotFound
            messages = await self.messages(conn, session_id)
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT IS_USED_LOCK(%s)", (self.lock_name(session_id),)
                )
                busy = (await cursor.fetchone())[0] is not None
            return messages, busy

    @asynccontextmanager
    async def locked_session(self, session_id):
        # A dedicated connection holds GET_LOCK, but no open transaction, during
        # the model call. This protects history and the turn cap across workers.
        async with self.connection() as conn:
            if not await self.exists(conn, session_id):
                raise SessionNotFound
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT GET_LOCK(%s, 0)", (self.lock_name(session_id),))
                acquired = (await cursor.fetchone())[0]
            if acquired != 1:
                raise SessionBusy
            try:
                yield conn
            finally:
                if not conn.closed:
                    try:
                        async with conn.cursor() as cursor:
                            await cursor.execute(
                                "SELECT RELEASE_LOCK(%s)", (self.lock_name(session_id),)
                            )
                    except BaseException:
                        conn.close()
                        raise

    async def save_turn(self, conn, session_id, text, reply):
        await conn.begin()
        try:
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO chat_messages (session_id, role, content) "
                    "VALUES (%s, %s, %s)",
                    [(session_id, "user", text), (session_id, "assistant", reply)],
                )
            await conn.commit()
        except BaseException:
            if not conn.closed:
                await conn.rollback()
            raise

"""Opt-in real MySQL tests. The model is always fake; no paid API calls.

STUDYFLOW_TEST_MYSQL=1 uv run pytest -q tests/test_mysql_integration.py
Tests create isolated UUID sessions. A cleanup SQL file is printed on teardown;
only a database administrator can delete these rows (the app cannot DELETE).
"""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import aiomysql
import httpx
from openai import APIError

from database import SessionStore
from main import app, lifespan

CREATED = set()


def teardown_module():
    if not CREATED:
        return
    fd, name = tempfile.mkstemp(prefix="studyflow-test-cleanup-", suffix=".sql")
    with os.fdopen(fd, "w") as output:
        ids = ", ".join(f"'{session_id}'" for session_id in sorted(CREATED))
        output.write(
            "USE studyflow;\nSTART TRANSACTION;\n"
            f"DELETE FROM chat_messages WHERE session_id IN ({ids});\n"
            f"DELETE FROM chat_sessions WHERE session_id IN ({ids});\nCOMMIT;\n"
        )
    print(f"\nTest data cleanup (administrator only): sudo mysql < {name}")


class FakeLLM:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.payloads = []
        self.entered = asyncio.Event()
        self.release = None
        self.error = None
        self.reply = "这是模拟回复。"
        self.empty_choices = False

    async def create(self, **kwargs):
        self.payloads.append(kwargs)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.error:
            raise self.error
        choices = [] if self.empty_choices else [
            SimpleNamespace(message=SimpleNamespace(content=self.reply))
        ]
        return SimpleNamespace(choices=choices)

    async def close(self):
        pass


@unittest.skipUnless(os.getenv("STUDYFLOW_TEST_MYSQL") == "1", "requires explicit real-MySQL opt-in")
class MySQLIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.life = lifespan(app)
        with patch.dict(os.environ, {"LLM_API_KEY": "", "LLM_MODEL": ""}):
            await self.life.__aenter__()
        self.fake = FakeLLM()
        app.state.llm = self.fake
        app.state.model = "fake-model"
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.life.__aexit__(None, None, None)

    async def new_session(self):
        result = await self.client.post("/sessions")
        self.assertEqual(result.status_code, 201, result.text)
        session_id = result.json()["session_id"]
        CREATED.add(session_id)
        return session_id

    async def send(self, session_id, text=" 什么是词法分析？ "):
        return await self.client.post(
            f"/sessions/{session_id}/messages", json={"message": text}
        )

    async def history(self, session_id):
        return await self.client.get(f"/sessions/{session_id}/messages")

    async def test_health_and_validation(self):
        self.assertTrue((await self.client.get("/health")).json()["database_connected"])
        self.assertEqual((await self.history("unknown")).status_code, 404)
        self.assertEqual((await self.send("unknown")).status_code, 404)
        session_id = await self.new_session()
        for text in ("", "   ", "x" * 4001):
            self.assertEqual((await self.send(session_id, text)).status_code, 422)
        self.assertEqual((await self.history(session_id + " ")).status_code, 404)
        app.state.llm = None
        self.assertEqual((await self.send(session_id)).status_code, 503)
        self.assertEqual((await self.history(session_id)).json()["message_count"], 0)

    async def test_order_and_restart_in_separate_process(self):
        session_id = await self.new_session()
        self.assertEqual((await self.send(session_id)).status_code, 200)
        self.assertEqual((await self.send(session_id, "举个例子")).status_code, 200)
        history = (await self.history(session_id)).json()
        self.assertEqual(history["message_count"], 4)
        self.assertEqual([m["role"] for m in history["messages"]], ["user", "assistant"] * 2)
        self.assertEqual(history["messages"][0]["content"], "什么是词法分析？")
        self.assertFalse(history["busy"])
        self.assertEqual(len(self.fake.payloads[1]["messages"]), 4)

        # A new Python process creates a new application and connection pool.
        code = """
import asyncio, sys
import httpx
from main import app, lifespan
async def check():
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/sessions/' + sys.argv[1] + '/messages')
            assert r.status_code == 200
            assert r.json()['message_count'] == 4
            assert r.json()['messages'][2]['content'] == '举个例子'
            print('restart history verified')
asyncio.run(check())
"""
        result = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-c", code, session_id],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "LLM_API_KEY": "", "LLM_MODEL": ""},
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("restart history verified", result.stdout)

    async def test_model_failures_do_not_persist_and_unlock(self):
        session_id = await self.new_session()
        self.fake.error = APIError("fake failure", httpx.Request("POST", "http://fake"), body=None)
        self.assertEqual((await self.send(session_id)).status_code, 502)
        self.fake.error = None
        self.fake.empty_choices = True
        self.assertEqual((await self.send(session_id)).status_code, 502)
        self.fake.empty_choices = False
        self.fake.reply = "  "
        self.assertEqual((await self.send(session_id)).status_code, 502)
        self.assertEqual((await self.history(session_id)).json()["message_count"], 0)
        self.fake.reply = "重试成功"
        self.assertEqual((await self.send(session_id)).status_code, 200)
        self.assertFalse((await self.history(session_id)).json()["busy"])

    async def test_concurrent_request_returns_busy(self):
        session_id = await self.new_session()
        self.fake.release = asyncio.Event()
        first = asyncio.create_task(self.send(session_id))
        try:
            await asyncio.wait_for(self.fake.entered.wait(), timeout=5)
            self.assertTrue((await self.history(session_id)).json()["busy"])
            self.assertEqual((await self.send(session_id)).status_code, 409)
        finally:
            self.fake.release.set()
            await first
        self.assertEqual((await self.history(session_id)).json()["message_count"], 2)
        self.assertEqual(len(self.fake.payloads), 1)

    async def test_independent_connection_pool_respects_lock(self):
        session_id = await self.new_session()
        other_worker = await SessionStore.open()
        try:
            async with other_worker.locked_session(session_id):
                self.assertEqual((await self.send(session_id)).status_code, 409)
                self.assertTrue((await self.history(session_id)).json()["busy"])
        finally:
            await other_worker.close()
        self.assertEqual((await self.send(session_id)).status_code, 200)

    async def test_cancelled_request_releases_lock(self):
        session_id = await self.new_session()
        self.fake.release = asyncio.Event()
        request = asyncio.create_task(self.send(session_id))
        await asyncio.wait_for(self.fake.entered.wait(), timeout=5)
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request
        history = (await self.history(session_id)).json()
        self.assertFalse(history["busy"])
        self.assertEqual(history["message_count"], 0)
        self.fake.release = None
        self.assertEqual((await self.send(session_id)).status_code, 200)

    async def test_twenty_turn_limit(self):
        session_id = await self.new_session()
        for _ in range(20):
            self.assertEqual((await self.send(session_id)).status_code, 200)
        self.assertEqual((await self.send(session_id)).status_code, 409)
        self.assertEqual((await self.history(session_id)).json()["message_count"], 40)

    async def test_failed_turn_write_is_atomic(self):
        session_id = await self.new_session()
        async with app.state.store.locked_session(session_id) as conn:
            with self.assertRaises(aiomysql.IntegrityError):
                await app.state.store.save_turn(conn, session_id, "不能只保存这一条", None)
        self.assertEqual((await self.history(session_id)).json()["message_count"], 0)

    async def test_application_account_cannot_update_delete_or_read_admin_tables(self):
        async with app.state.store.connection() as conn, conn.cursor() as cursor:
            for statement in (
                "UPDATE chat_sessions SET session_id=session_id WHERE 1=0",
                "DELETE FROM chat_messages WHERE 1=0",
                "SELECT user FROM mysql.user",
            ):
                with self.assertRaises(aiomysql.OperationalError) as denied:
                    await cursor.execute(statement)
                self.assertEqual(denied.exception.args[0], 1142)

    async def test_database_error_is_sanitized(self):
        for error in (aiomysql.OperationalError(2006, "secret"), asyncio.TimeoutError()):
            with patch.object(app.state.store, "ping", side_effect=error):
                result = await self.client.get("/health")
            self.assertEqual(result.status_code, 503)
            self.assertNotIn("secret", result.text)

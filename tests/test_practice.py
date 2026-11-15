"""Practice API contract tests: no database or paid model calls."""
import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from database import SessionBusy, SessionNotFound
from main import app


class FakeStore:
    def __init__(self):
        self.sessions = set()
        self.busy = False

    async def create_session(self, session_id):
        self.sessions.add(session_id)

    @asynccontextmanager
    async def locked_session(self, session_id):
        if session_id not in self.sessions:
            raise SessionNotFound
        if self.busy:
            raise SessionBusy
        self.busy = True
        try:
            yield None
        finally:
            self.busy = False


class FakeModel:
    def __init__(self):
        self.chat = SimpleNamespace(completions=self)
        self.output = None
        self.payloads = []
        self.entered = asyncio.Event()
        self.release = None

    async def create(self, **kwargs):
        self.payloads.append(json.loads(kwargs['messages'][1]['content']))
        self.entered.set()
        if self.release:
            await self.release.wait()
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(self.output, ensure_ascii=False))
        )])

    def draft(self, question='判断 FOLLOW 集什么时候需要传递，并说明理由。'):
        self.output = {'exercise': {
            'question': question, 'reference_answer': '后缀可空时传递',
            'rubric': ['后缀可空', '说明原因'], 'hint': '考虑后缀能否推导空串。',
        }, 'reason': ''}

    def grade(self, status, answer):
        self.output = {'status': status, 'feedback': '模拟反馈', 'evidence': answer}


class PracticeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = FakeStore()
        self.model = FakeModel()
        self.patches = [patch.object(app.state, key, value, create=True) for key, value in {
            'store': self.store, 'llm': self.model, 'model': 'fake', 'practice_sessions': {},
        }.items()]
        for item in self.patches:
            item.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        response = await self.client.post('/sessions')
        self.session_id = response.json()['session_id']

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()

    async def generate(self, **kwargs):
        return await self.client.post('/exercises/generate', json={
            'session_id': self.session_id, **kwargs,
        })

    async def attempt(self, exercise_id, answer='后缀可空', supplement=False):
        return await self.client.post(f'/exercises/{exercise_id}/attempts', json={
            'session_id': self.session_id, 'answer': answer, 'supplement': supplement,
        })

    async def test_full_flow_supplement_variant_and_original_retained(self):
        self.model.draft()
        response = await self.generate(goal='FOLLOW 集')
        self.assertEqual(response.status_code, 201, response.text)
        original = response.json()
        self.assertNotIn('reference_answer', original)
        exercise_id = original['exercise_id']
        self.model.grade('partial', '后缀可空')
        response = await self.attempt(exercise_id)
        self.assertEqual(response.json()['next']['action'], 'retry')
        self.assertIn('hint', response.json()['next'])
        self.model.grade('correct', '因为后续可以为空')
        response = await self.attempt(exercise_id, '因为后续可以为空', True)
        self.assertEqual(response.json()['attempt_no'], 2)
        self.assertEqual(response.json()['next'], {'action': 'generate_variant', 'variant_of': exercise_id})
        self.assertEqual(self.model.payloads[-1]['previous_answer'], '后缀可空')
        self.model.draft('若后缀不可空，是否传递 FOLLOW？说明理由。')
        response = await self.generate(variant_of=exercise_id)
        self.assertEqual(response.status_code, 201, response.text)
        variant = response.json()['exercise_id']
        self.model.grade('correct', '不传递')
        response = await self.attempt(variant, '不传递')
        self.assertEqual(response.json()['next']['action'], 'practice_complete')
        stored = app.state.practice_sessions[self.session_id]['exercises']
        self.assertEqual(stored[exercise_id]['draft'].question, original['question'])
        self.assertEqual(len(stored[exercise_id]['attempts']), 2)

    async def test_failed_grading_does_not_save_and_can_retry(self):
        self.model.draft()
        exercise_id = (await self.generate(goal='FOLLOW')).json()['exercise_id']
        self.model.output = {'bad': 'json shape'}
        self.assertEqual((await self.attempt(exercise_id)).status_code, 502)
        exercise = app.state.practice_sessions[self.session_id]['exercises'][exercise_id]
        self.assertEqual(exercise['attempts'], [])
        self.assertFalse(self.store.busy)
        self.model.grade('needs_clarification', '')
        response = await self.attempt(exercise_id, '懂了')
        self.assertEqual(response.json()['attempt_no'], 1)
        self.assertEqual(response.json()['next']['action'], 'clarify')

    async def test_sessions_validated_and_restart_is_recoverable(self):
        self.model.draft()
        response = await self.client.post('/exercises/generate', json={'session_id': 'missing', 'goal': 'FOLLOW'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(app.state.practice_sessions, {})
        exercise_id = (await self.generate(goal='FOLLOW')).json()['exercise_id']
        app.state.practice_sessions.clear()
        self.assertEqual((await self.attempt(exercise_id)).status_code, 404)
        self.assertEqual((await self.generate(goal='FOLLOW')).status_code, 201)

    async def test_concurrent_requests_use_existing_store_lock(self):
        self.model.draft()
        self.model.release = asyncio.Event()
        task = asyncio.create_task(self.generate(goal='FOLLOW'))
        await self.model.entered.wait()
        try:
            self.assertEqual((await self.generate(goal='FOLLOW')).status_code, 409)
        finally:
            self.model.release.set()
        self.assertEqual((await task).status_code, 201)
        self.assertFalse(self.store.busy)

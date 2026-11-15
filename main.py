import asyncio
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import aiomysql
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI, APIError
from pydantic import BaseModel, Field

from database import SessionBusy, SessionNotFound, SessionStore

load_dotenv()

SYSTEM_PROMPT = """
你是 StudyFlow，一位用简体中文交流的计算机课程导师，当前重点是编译原理。
学生可以自由提问，也可以提交自己的题目、答案或思路。

教学规则：
1. 对概念问答先给简短直观解释，再按需举例，不要每次都强制诊断。
2. 对解题求助，结合学生已有思路找卡点；缺少题目条件时先澄清。
3. 默认先给一个针对性的提示，一次最多追问一个关键问题。
4. 学生明确要求完整答案或快速复习时，直接解释，不强制反复追问。
5. 根据实际作答反馈，不因学生说“懂了”就断言已经掌握。
6. 信息不足或无法确定时说明不确定性。不要编造教材引用或声称运行过验证器。
7. 学生愿意继续练习时，可以给一道小变式检验理解。
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 数据库不可用时启动失败，不悄悄退回会丢失数据的内存存储。
    app.state.llm = None
    try:
        app.state.store = await SessionStore.open()
    except (aiomysql.MySQLError, OSError, ValueError):
        raise RuntimeError("MySQL 连接失败，请检查 DB 配置、服务状态和建表授权") from None

    try:
        key = os.getenv("LLM_API_KEY", "").strip()
        app.state.model = os.getenv("LLM_MODEL", "").strip()
        if key and app.state.model:
            app.state.llm = AsyncOpenAI(
                api_key=key,
                base_url=os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1",
                timeout=45.0,
                max_retries=0,
            )
        yield
    finally:
        try:
            if app.state.llm:
                await app.state.llm.close()
        finally:
            await app.state.store.close()


app = FastAPI(title="StudyFlow 第一次对话", lifespan=lifespan)


@app.exception_handler(SessionNotFound)
async def session_not_found(request: Request, exc: SessionNotFound):
    return JSONResponse(status_code=404, content={"detail": "会话不存在，请先创建会话"})


@app.exception_handler(SessionBusy)
async def session_busy(request: Request, exc: SessionBusy):
    return JSONResponse(status_code=409, content={"detail": "正在回复，请等待本轮完成"})


@app.exception_handler(aiomysql.MySQLError)
@app.exception_handler(asyncio.TimeoutError)
async def database_unavailable(request: Request, exc: Exception):
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请稍后重试"})


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@app.get("/health")
async def health():
    await app.state.store.ping()
    return {
        "status": "ok",
        "llm_configured": app.state.llm is not None,
        "database_connected": True,
    }


@app.post("/sessions", status_code=201)
async def create_session():
    session_id = str(uuid4())

    await app.state.store.create_session(session_id)

    return {"session_id": session_id}


@app.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: MessageIn):
    async with app.state.store.locked_session(session_id) as conn:
        if app.state.llm is None:
            raise HTTPException(503, "请配置 LLM_API_KEY 和 LLM_MODEL，然后重启服务")
        text = body.message.strip()
        if not text:
            raise HTTPException(422, "消息不能只包含空白字符")

        messages = await app.state.store.messages(conn, session_id)
        if len(messages) >= 40:
            raise HTTPException(409, "本演示每个会话限 20 轮，请创建新会话")

        try:
            result = await app.state.llm.chat.completions.create(
                model=app.state.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *messages,
                    {"role": "user", "content": text},
                ],
            )
        except APIError:
            raise HTTPException(
                502, "模型调用失败，请检查密钥、地址、模型名、额度及网络"
            ) from None

        if not result.choices:
            raise HTTPException(502, "模型没有返回回复，请重试")
        reply = (result.choices[0].message.content or "").strip()
        if not reply:
            raise HTTPException(502, "模型返回了空内容，请重试")

        # 两条消息在同一事务中保存；模型失败不写入任何消息。
        await app.state.store.save_turn(conn, session_id, text, reply)
        return {"session_id": session_id, "reply": reply}

@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    messages, busy = await app.state.store.history(session_id)
    return {
        "session_id": session_id,
        "messages": messages,
        "message_count": len(messages),
        "busy": busy,
    }
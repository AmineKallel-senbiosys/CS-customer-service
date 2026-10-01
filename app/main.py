from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent import prepare_kb, reply, sync_ticket_notes
from app.config import HOST, PORT, WEB_DIR
from app.kb import load_kb
from app import signals

log = logging.getLogger("velio")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Velio")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

KB = load_kb()
SESSIONS: dict[str, dict[str, Any]] = {}
THINKING: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()


class SessionBody(BaseModel):
    agent: str | None = None
    topic: str | None = None


class MessageBody(BaseModel):
    message: str
    session_id: str | None = None
    agent: str | None = None
    topic: str | None = None


def _session(session_id: str | None, topic: str | None) -> dict[str, Any]:
    with LOCK:
        if session_id and session_id in SESSIONS:
            session = SESSIONS[session_id]
            if topic:
                session["topic"] = topic
            return session
        sid = uuid.uuid4().hex
        session = {
            "id": sid,
            "topic": topic or "Chat with Velio",
            "messages": [],
            "verified_emails": [],
            "tickets": [],
            "diagnostics": {},
            "orders": {},
        }
        SESSIONS[sid] = session
        return session


def _status(session_id: str, label: str) -> None:
    with LOCK:
        THINKING[session_id] = {"active": True, "label": label, "step": label, "trace": []}


def _clear_status(session_id: str) -> None:
    with LOCK:
        THINKING[session_id] = {"active": False, "label": "", "step": "", "trace": []}


def _result(session: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "session_id": session["id"],
        "reply": text,
        "messages": list(session["messages"]),
        "agent": "velio",
        "phase": "chatting",
        "topic": session.get("topic") or "Chat with Velio",
        "ui": {"show_starter": False},
    }


def _handle(body: MessageBody, on_token=None) -> dict[str, Any]:
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    session = _session(body.session_id, body.topic)
    session["messages"].append({"role": "user", "content": message})
    try:
        text = reply(
            session,
            message,
            KB,
            on_token=on_token,
            set_status=lambda label: _status(session["id"], label),
        )
    finally:
        _clear_status(session["id"])
    session["messages"].append({"role": "assistant", "content": text})
    sync_ticket_notes(session)
    return _result(session, text)


@app.on_event("startup")
def startup() -> None:
    try:
        prepare_kb(KB)
        log.info("Knowledge base ready (%s articles)", len(KB.articles))
    except Exception:
        log.exception("Article embeddings were not prepared")

    def _warm() -> None:
        signals.warmup(KB.languages)

    threading.Thread(target=_warm, name="laya-warmup", daemon=True).start()


@app.get("/")
def index() -> FileResponse:
    page = Path(WEB_DIR / "index.html")
    return FileResponse(page)


@app.post("/api/session")
def create_session(body: SessionBody) -> dict[str, Any]:
    session = _session(None, body.topic)
    return {
        "session_id": session["id"],
        "messages": [],
        "agent": "velio",
        "phase": "chatting",
        "topic": session["topic"],
        "ui": {"show_starter": True},
    }


@app.post("/api/message")
def message(body: MessageBody) -> dict[str, Any]:
    return _handle(body)


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


@app.post("/api/message/stream")
def message_stream(body: MessageBody) -> StreamingResponse:
    events: queue.Queue = queue.Queue()

    def worker() -> None:
        try:
            events.put(("start", None))
            result = _handle(body, on_token=lambda text: events.put(("token", text)))
            events.put(("done", result))
        except HTTPException as exc:
            events.put(("error", str(exc.detail)))
        except Exception:
            log.exception("chat turn failed")
            events.put(("error", "Something went wrong. Please try again."))

    threading.Thread(target=worker, daemon=True).start()

    def generate():
        while True:
            kind, payload = events.get()
            if kind == "start":
                yield _sse({"type": "start"})
            elif kind == "token":
                yield _sse({"type": "token", "text": payload})
            elif kind == "done":
                yield _sse({"type": "done", "result": payload})
                break
            else:
                yield _sse({"type": "error", "error": payload})
                break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/thinking/{session_id}")
def thinking(session_id: str) -> dict[str, Any]:
    with LOCK:
        return THINKING.get(session_id) or {"active": False, "label": "", "step": "", "trace": []}


@app.get("/api/agents")
def agents() -> dict[str, Any]:
    return {"agents": [{"id": "velio", "name": "Velio"}]}


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()

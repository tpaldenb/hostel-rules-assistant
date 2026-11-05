"""HTTP API and static UI."""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .answer import Assistant, Turn
from .corpus import Corpus

corpus = Corpus.load(config.DOCUMENTS_DIR)
assistant = Assistant(corpus)
app = FastAPI(title="Hostel and Transport Rules Assistant")


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    as_of: date | None = None
    history: list[HistoryItem] = Field(default_factory=list, max_length=20)


def sse(events: Iterator[dict]) -> Iterator[str]:
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/api/status")
def status() -> dict:
    return {
        "mode": assistant.mode,
        "label": config.provider_label(assistant.mode),
        "model": config.model_for(assistant.mode),
        "today": date.today().isoformat(),
        "clauses": len(corpus.clauses),
        "documents": len({c.doc_id for c in corpus.clauses.values()}),
    }


@app.get("/api/clauses")
def clauses(as_of: date | None = Query(default=None)) -> dict:
    day = as_of or date.today()
    items = [corpus.describe(cid, day) for cid in corpus.clauses if corpus.is_visible(cid, day)]
    items.sort(key=lambda c: (c["issued"], c["id"]), reverse=True)
    return {"as_of": day.isoformat(), "clauses": items}


@app.get("/api/clauses/{clause_id}")
def clause(clause_id: str, as_of: date | None = Query(default=None)) -> dict:
    day = as_of or date.today()
    if clause_id not in corpus.clauses or not corpus.is_visible(clause_id, day):
        raise HTTPException(status_code=404, detail="Clause not found")
    return {
        "clause": corpus.describe(clause_id, day),
        "history": [corpus.describe(c.id, day) for c in corpus.history(clause_id, day)],
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> StreamingResponse:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question is empty")
    history = [Turn(h.role, h.content) for h in request.history]
    events = assistant.answer(question, history, request.as_of or date.today())
    return StreamingResponse(sse(events), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

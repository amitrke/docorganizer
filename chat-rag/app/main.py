from __future__ import annotations

from fastapi import FastAPI

from .models import ChatRequest, ChatResponse, Citation
from .rag import answer_question, get_env


app = FastAPI(title="docorg-chat-rag", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": get_env("OLLAMA_MODEL", "mistral:7b-instruct"),
        "db_path": get_env("DOCORG_DB_PATH", "/data/docorganizer.db"),
    }


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    answer, retrieval_query, used_top_k, docs = answer_question(
        payload.question,
        payload.top_k,
    )

    citations = [
        Citation(
            id=d.id,
            filename=d.filename,
            filepath=d.filepath,
            detected_date=d.detected_date,
            category=d.category,
            score=d.score,
        )
        for d in docs
    ]

    return ChatResponse(
        answer=answer,
        citations=citations,
        retrieval_query=retrieval_query,
        used_top_k=used_top_k,
    )

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=50)


class Citation(BaseModel):
    id: int
    filename: str
    filepath: str
    detected_date: str | None = None
    category: str | None = None
    score: float | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    retrieval_query: str
    used_top_k: int

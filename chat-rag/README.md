# chat-rag

A separate RAG chat service for document Q&A, isolated from the existing docorganizer web image.

## What this does

- Reads indexed docs from the existing SQLite DB (read-only mount).
- Uses SQLite FTS5 retrieval to fetch relevant documents.
- Sends question + retrieved context to Ollama.
- Returns an answer plus citations.

## Endpoints

- `GET /health`
- `POST /chat`

Example request:

```json
{
  "question": "Give me a list of places that I have lived in the last 6 years"
}
```

## Run with Docker (GPU)

1. Copy `.env.example` to `.env` and adjust values as needed.
2. Update host volume paths in `docker-compose.gpu.yml` for your environment.
3. Start:

```sh
docker compose -f docker-compose.gpu.yml up -d --build
```

4. Pull model into Ollama:

```sh
docker exec -it docorg-ollama ollama pull mistral:7b-instruct
```

5. Health check:

```sh
curl http://localhost:8090/health
```

6. Chat request:

```sh
curl -X POST http://localhost:8090/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"Give me a list of places that I have lived in the last 6 years"}'
```

## Notes

- Existing web container and compose setup remain unchanged.
- Retrieval query is generated from user question terms.
- For place-history style questions, a year-window filter is applied when detected dates are available.

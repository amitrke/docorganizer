# chat-rag

A separate RAG chat service for document Q&A, isolated from the existing docorganizer web image.

## What this does

- Reads indexed docs from the existing SQLite DB.
- Uses SQLite FTS5 retrieval to fetch relevant documents.
- Sends question + retrieved context to Ollama.
- Returns an answer plus citations.

## Config file

chat-rag loads settings from config.yaml by default.

See chat-rag/config.yaml for:

- paths.database
- paths.documents
- ollama.url
- ollama.model
- ollama.timeout
- retrieval.top_k

You can override with environment variables if needed:

- CHAT_RAG_CONFIG
- DOCORG_DB_PATH
- DOCORG_DOCS_PATH
- OLLAMA_URL
- OLLAMA_MODEL
- OLLAMA_TIMEOUT
- TOP_K

## Endpoints

- GET /health
- POST /chat

Example request body:

{
  "question": "Give me a list of places that I have lived in the last 6 years"
}

## Run locally (without Docker)

1. From repo root, install chat-rag dependencies:

python -m pip install -r chat-rag/requirements.txt

2. Update chat-rag/config.yaml so paths.database points to your docorganizer DB.

3. Ensure Ollama is running and the configured model is pulled.

4. Start API from repo root:

python -m uvicorn app.main:app --app-dir chat-rag --host 127.0.0.1 --port 8090

Or run from inside chat-rag:

cd chat-rag
python -m uvicorn app.main:app --host 127.0.0.1 --port 8090

5. Health check:

curl http://localhost:8090/health

6. Chat request:

curl -X POST http://localhost:8090/chat -H "Content-Type: application/json" -d "{\"question\":\"Give me a list of places that I have lived in the last 6 years\"}"

## Run with Docker (GPU)

1. Copy .env.example to .env and adjust values as needed.
2. Update host volume paths in docker-compose.gpu.yml for your environment.
3. Start:

docker compose -f docker-compose.gpu.yml up -d --build

4. Pull model into Ollama:

docker exec -it docorg-ollama ollama pull mistral:7b-instruct

## Notes

- Existing web container and compose setup remain unchanged.
- Retrieval query is generated from user question terms.
- For place-history style questions, a year-window filter is applied when detected dates are available.

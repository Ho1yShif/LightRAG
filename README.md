# LightRAG on Render

Deploy [LightRAG](https://github.com/HKUDS/LightRAG) on Render in one click. Get a
knowledge-graph RAG server with a built-in Web UI — upload documents, and query
them with graph-aware retrieval backed by your own LLM.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Ho1yShif/LightRAG)

## What it does

LightRAG combines a vector store with an automatically-extracted knowledge graph
so retrieval understands the *relationships* between entities in your documents,
not just their text. This template runs the LightRAG server as a single Render
web service:

- **REST API** for ingesting documents and running queries (naive / local / global
  / hybrid retrieval modes).
- **Web UI** at `/webui` — upload files, watch the knowledge graph build, and query
  interactively.
- **Persistent storage** on a Render Disk at `/app/data`, so your knowledge graph
  survives restarts and redeploys (default storage is file-based:
  `JsonKVStorage` / `NanoVectorDBStorage` / `NetworkXStorage`).

Defaults use **OpenAI** for both the LLM (`gpt-5.4-mini`) and embeddings
(`text-embedding-3-large`) — one API key covers both, and both are swappable via
`LLM_BINDING` / `EMBEDDING_BINDING` (set `LLM_MODEL` to any OpenAI model, e.g. the
`gpt-5.6-*` flagships for higher quality at higher cost).

## Prerequisites

- A [Render account](https://dashboard.render.com/register).
- An [OpenAI API key](https://platform.openai.com/api-keys) (used for both the LLM
  and embeddings). For least privilege, create a **Restricted** key and, under
  **Model capabilities**, enable only **Chat completions** (`/v1/chat/completions`)
  and **Embeddings** (`/v1/embeddings`) — the only endpoints LightRAG calls. Leave
  the other capabilities (Responses, Text-to-speech, Realtime, Images, Moderations)
  and all other permission groups (e.g. *List models*) set to **None**.

## Deploy

1. Click **Deploy to Render** above. Render reads [`render.yaml`](./render.yaml)
   and provisions one web service with a 1 GB persistent disk.
2. Fill in the three secret env vars Render prompts for (all marked `sync: false`,
   so they are never stored in the repo):

   | Env var | What to set it to |
   | --- | --- |
   | `LLM_BINDING_API_KEY` | Your OpenAI API key. A **Restricted** key with only *Chat completions* + *Embeddings* enabled is enough (see [Prerequisites](#prerequisites)). |
   | `EMBEDDING_BINDING_API_KEY` | The same OpenAI API key. |
   | `LIGHTRAG_API_KEY` | A strong secret **you choose** — it protects your deployed server and Web UI. Generate one with the command below. |

   Generate a strong `LIGHTRAG_API_KEY`:

   ```bash
   openssl rand -base64 32
   ```

3. Click **Apply**. The first build compiles the frontend and Python deps, so it
   takes a few minutes. When the service is **live**, `/health` returns 200.

> **Note:** The disk requires a paid instance type, so the Blueprint uses the
> `starter` plan.

## Use it once it's live

1. Open `https://<your-service>.onrender.com/webui`.
2. Log in with the `LIGHTRAG_API_KEY` you set.
3. Upload documents and let LightRAG build the knowledge graph, then query them
   from the UI or via the REST API (send your key as the `X-API-Key` header).

### Using the app

Want to see it work end to end in a couple of minutes? Use LightRAG's own demo
document — *A Christmas Carol*, the same `book.txt` the project's
[`examples/`](./examples) run against — so you can reproduce the canonical results.

1. **Log in.** Open `https://<your-service>.onrender.com/webui` and paste your
   `LIGHTRAG_API_KEY` when prompted.
2. **Grab the demo document** (any plain-text file works, but this keeps results
   reproducible):

   ```bash
   curl https://www.gutenberg.org/cache/epub/46/pg46.txt -o book.txt
   ```

3. **Upload it.** Go to the **Documents** tab, click **Upload**, and drop in
   `book.txt`. Supported types include TXT, MD, PDF, DOCX, PPTX, and more.
4. **Watch the graph build.** The document moves through *Pending → Processing →
   Processed* in the Documents list while LightRAG extracts entities and relations.
   Open the **Knowledge Graph** tab to watch the graph fill in — nodes are
   characters and concepts (Scrooge, Marley, Christmas), edges are their
   relationships.
5. **Query it.** On the **Retrieval** tab, ask the demo's canonical question:

   > What are the top themes in this story?

   Switch the retrieval **mode** to compare how the app answers: `naive` (plain
   vector search) vs. `local` / `global` / `hybrid` / `mix` (graph-aware
   retrieval). You can also prefix a query inline, e.g. `/global What are the top
   themes in this story?`.

Prefer the API? The same flow over REST (send your key as `X-API-Key`):

```bash
# Ingest raw text
curl -X POST https://<your-service>.onrender.com/documents/text \
  -H "X-API-Key: $LIGHTRAG_API_KEY" -H "Content-Type: application/json" \
  -d '{"text": "Scrooge was a tight-fisted hand at the grindstone.", "file_source": "demo"}'

# Query it
curl -X POST https://<your-service>.onrender.com/query \
  -H "X-API-Key: $LIGHTRAG_API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "What are the top themes in this story?", "mode": "hybrid"}'
```

## Configuration

The env vars above are the minimum. See [`.env.example`](./.env.example) for the
concise set this template uses, and the repo's full [`env.example`](./env.example)
for every advanced option (alternative LLM/embedding providers, external storage
backends like PostgreSQL/Neo4j/Milvus, reranking, and more).

The Blueprint defaults `CORS_ORIGINS` to the service's own origin (via Render's
`RENDER_EXTERNAL_URL`), so browser access is same-origin only out of the box. If
you put a **separate-origin** frontend in front of the API, set `CORS_ORIGINS` to
that frontend's origin.

## Production storage: Render Postgres

By default this template keeps all state in file-based stores on the Render Disk
at `/app/data`. That survives restarts and redeploys and is perfect for evaluating
LightRAG, but it's tied to a single instance's disk — no managed backups,
point-in-time recovery, or the ability to share state across services.

For production, move the state to a [Render Postgres](https://render.com/docs/postgresql-creating-connecting)
database. Render Postgres ships with the `pgvector` extension, which is all
LightRAG needs to back its key-value, doc-status, and **vector** stores.

1. Create a Render Postgres instance in the same region as your service.
2. Add these env vars to the web service (copy the connection details from the
   database's dashboard page — internal hostname, port `5432`, database, user,
   password):

   ```
   LIGHTRAG_KV_STORAGE=PGKVStorage
   LIGHTRAG_DOC_STATUS_STORAGE=PGDocStatusStorage
   LIGHTRAG_VECTOR_STORAGE=PGVectorStorage
   POSTGRES_HOST=<your-db-internal-hostname>
   POSTGRES_PORT=5432
   POSTGRES_DATABASE=<your-db-name>
   POSTGRES_USER=<your-db-user>
   POSTGRES_PASSWORD=<your-db-password>
   ```

3. Redeploy. LightRAG creates its tables (and the `vector` extension) on first
   start.

> **Knowledge graph store:** LightRAG's Postgres graph backend (`PGGraphStorage`)
> requires the Apache AGE extension, which Render Postgres does not provide. Leave
> `LIGHTRAG_GRAPH_STORAGE` on the default `NetworkXStorage` (which stays on the
> disk), or point it at an external managed graph database such as
> [Neo4j Aura](https://neo4j.com/product/auradb/) via the `NEO4J_*` vars in
> [`env.example`](./env.example). If you keep the graph on disk, keep the disk in
> your Blueprint.

## Full documentation

This README covers only the Render deployment. For LightRAG's complete
documentation — API reference, retrieval modes, storage backends, and examples —
see the upstream project: <https://github.com/HKUDS/LightRAG>.

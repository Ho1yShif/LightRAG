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

Defaults use **OpenAI** for both the LLM and embeddings (one API key covers both);
both are swappable via `LLM_BINDING` / `EMBEDDING_BINDING`.

## Prerequisites

- A [Render account](https://dashboard.render.com/register).
- An [OpenAI API key](https://platform.openai.com/api-keys) (used for both the LLM
  and embeddings).

## Deploy

1. Click **Deploy to Render** above. Render reads [`render.yaml`](./render.yaml)
   and provisions one web service with a 1 GB persistent disk.
2. Fill in the three secret env vars Render prompts for (all marked `sync: false`,
   so they are never stored in the repo):

   | Env var | What to set it to |
   | --- | --- |
   | `LLM_BINDING_API_KEY` | Your OpenAI API key (from <https://platform.openai.com/api-keys>). |
   | `EMBEDDING_BINDING_API_KEY` | The same OpenAI API key. |
   | `LIGHTRAG_API_KEY` | A strong secret **you choose** — it protects your deployed server and Web UI. |

3. Click **Apply**. The first build compiles the frontend and Python deps, so it
   takes a few minutes. When the service is **live**, `/health` returns 200.

> **Note:** The disk requires a paid instance type, so the Blueprint uses the
> `starter` plan.

## Use it once it's live

1. Open `https://<your-service>.onrender.com/webui`.
2. Log in with the `LIGHTRAG_API_KEY` you set.
3. Upload documents and let LightRAG build the knowledge graph, then query them
   from the UI or via the REST API (send your key as the `X-API-Key` header).

## Configuration

The env vars above are the minimum. See [`.env.example`](./.env.example) for the
concise set this template uses, and the repo's full [`env.example`](./env.example)
for every advanced option (alternative LLM/embedding providers, external storage
backends like PostgreSQL/Neo4j/Milvus, reranking, and more).

## Full documentation

This README covers only the Render deployment. For LightRAG's complete
documentation — API reference, retrieval modes, storage backends, and examples —
see the upstream project: <https://github.com/HKUDS/LightRAG>.

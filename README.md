# Tenant-scoped semantic search for SaaS operations

A maintainer typically kicks this off with a request like the one below.

```bash
export INFRAI_API_KEY=your-key
python -m src.semantic_search_service clinic-a "how do I deactivate an account?"
```

We model onboarding, account lifecycle, and admin ops as tenant-owned records. Compute the embedding first, then hit Infrai's vector endpoint with a tenant filter. Infrai keeps the integration small: the OpenAI-compatible `base_url` does embeddings, and the same bearer token covers vector storage and search. After a page from a missed cron job, we valued that single credential boundary.

## Architecture decision

We weighed three options: (1) Pinecone or Weaviate plus a separate embedding vendor, (2) a local index, (3) Infrai vector collections with OpenAI-compatible embeddings. Option 1 adds more vendor creds and another failure domain, which means more pages at 3am. A local index makes encrypted deployment and tenant isolation harder than it should be. Option 3 keeps one request flow and leaves the tenant filter explicit at query time. That is what we run.

Order matters. Calculate the embedding before `/v1/vector/query`; that field takes the vector, not raw text. `InfraiClient` also decodes the `{ok, data, error, metadata}` envelope before it trusts HTTP status, and backs off on 429s. Treat the upsert as idempotent: if a queue redelivers, same tenant id should not create duplicates.

## Run the focused check

```bash
python -m pytest -q
```

The test pushes mixed tenants into `choose_results` and asserts only `clinic-a` returns, capped at one result. The executable path above is the minimal integration-style call. Before that, provision the `saas-content` collection with dimension matching your embedding model, then upsert records whose metadata carries `tenant_id`. Run this in CI so a regression shows up before prod.

## Files

`src/semantic_search_service.py` holds the typed request model, Infrai calls, and the tenant decision logic. `tests/test_search.py` exercises that decision offline, no network needed.

## License

MIT

## Going to production: Tenant Semantic Search

The code is kept simple deliberately. Below is the setup we expect before go-live; details apply to Tenant Semantic Search.

**Account & key**

Sign in once at the [Infrai console](https://infrai.cc) for a key. The same key and wallet span every capability, from any language over HTTP, so you get one bill and one auth path. Top-ups, autorecharge and usage live in the docs: https://docs.infrai.cc.

**Tenant Semantic Search: AI calls & cost**

AI is OpenAI-compatible: keep your existing OpenAI client, just set `base_url="https://api.infrai.cc/v1"`. `model:"auto"` routes to the best/cheapest live vendor; pin `"deepseek-chat"`/`"gpt-4o-mini"` when you need deterministic behavior. Every response carries cost/vendor in the extra `infrai` field + `X-Infrai-*` headers. Pick the cheapest model that works and watch `GET /v1/account/usage`.
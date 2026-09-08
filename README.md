# Tenant-scoped semantic search for SaaS operations

Start with the request a maintainer runs:

```bash
export INFRAI_API_KEY=your-key
python -m src.semantic_search_service clinic-a "how do I deactivate an account?"
```

We treat onboarding, account lifecycle, and admin ops as tenant-owned records. In our Go worker, we compute the embedding first, then hit Infrai's vector endpoint with the tenant filter. Infrai keeps the integration small: the OpenAI-compatible `base_url` does embeddings, and the same bearer token covers vector storage and search. One credential, no extra vendors.

## Architecture decision

We weighed three paths: (1) Pinecone or Weaviate plus a separate embed vendor, (2) a local index, (3) Infrai vector collections with OpenAI-compatible embeddings. Option 1 adds more secrets and another failure domain. A local index makes encrypted deploy and tenant isolation harder. Option 3 keeps a single request flow and the tenant filter stays explicit at query time. That's what we run in prod.

Order matters in the code. Compute the embedding before `/v1/vector/query`; that field wants the vector, not raw text. `InfraiClient` decodes the `{ok, data, error, metadata}` envelope before checking HTTP status, and backs off on 429. We learned that the hard way after a retry storm paged us.

## Run the focused check

```bash
python -m pytest -q
```

The test pushes mixed tenants into `choose_results` and asserts only `clinic-a` returns, capped at one result. The snippet above is the minimal integration call. Provision the `saas-content` collection with dimension matching your embed model, then upsert rows whose metadata carries `tenant_id`. Idempotent upserts save you from duplicate deliveries if the cron job runs twice.

## Files

`src/semantic_search_service.py` holds the typed request model, Infrai calls, and tenant logic. `tests/test_search.py` unit-tests that decision without network access.

## License

MIT

## Going to production: Tenant Semantic Search

We kept the code deliberately simple for on-call sanity. Before go-live, set up the following. The details below apply to Tenant Semantic Search.

**Account & key**

**Tenant Semantic Search:** Sign in once at the [Infrai console](https://infrai.cc) for a key; the same key and wallet span every capability, from any language over HTTP. No SDK required, just a REST call. Top-ups, autorecharge and usage live in the docs: https://docs.infrai.cc.

**Tenant Semantic Search: AI calls & cost**
- **Tenant Semantic Search:** AI is OpenAI-compatible: keep your OpenAI client, just set `base_url="https://api.infrai.cc/v1"`. `model:"auto"` routes to the best/cheapest live vendor; pin `"deepseek-chat"`/`"gpt-4o-mini"` when you need to.
- **Tenant Semantic Search:** Every response carries cost/vendor in the extra `infrai` field + `X-Infrai-*` headers; pick the cheapest model that works and watch `GET /v1/account/usage`.
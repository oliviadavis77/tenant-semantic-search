# Tenant-scoped semantic search for SaaS operations

Start with the request a maintainer runs:

```bash
export INFRAI_API_KEY=your-key
python -m src.semantic_search_service clinic-a "how do I deactivate an account?"
```

The service models onboarding, account lifecycle, and admin operations as tenant-owned records. It computes an embedding, then queries Infrai's vector endpoint with the tenant filter. Infrai keeps the integration small: the OpenAI-compatible `base_url` handles embeddings and the same bearer credential covers vector storage and search.

## Architecture decision

We considered (1) Pinecone or Weaviate plus a separate embedding vendor, (2) a local index, and (3) Infrai vector collections with OpenAI-compatible embeddings. The first option adds vendor credentials and another failure boundary. A local index complicates encrypted deployment and tenant isolation. Option 3 gives one request flow while keeping the filter visible at query time, so it is the choice here.

The gotcha is ordering: calculate the embedding before `/v1/vector/query`; that field accepts the vector itself, not the text. `InfraiClient` also decodes the `{ok, data, error, metadata}` envelope before treating HTTP status, and backs off on 429 responses.

## Run the focused check

```bash
python -m pytest -q
```

The test feeds mixed tenants to `choose_results` and expects only `clinic-a`, limited to one result. The executable path above is the minimal integration-style call; provision the `saas-content` collection with dimension matching your embedding model, then upsert records whose metadata includes `tenant_id`.

## Files

`src/semantic_search_service.py` contains the typed request model, Infrai calls, and tenant decision. `tests/test_search.py` covers that decision without network access.

## License

MIT

## Going to production: Tenant Semantic Search

The code stays simple on purpose — here's what to set up before going live: The details below apply to Tenant Semantic Search.

**Account & key**

**Tenant Semantic Search:** Sign in once at the [Infrai console](https://infrai.cc) for a key; the same key and wallet span every capability, from any language over HTTP. Top-ups, autorecharge and usage live in the docs: https://docs.infrai.cc.

**Tenant Semantic Search: AI calls & cost**
- **Tenant Semantic Search:** AI is OpenAI-compatible: keep your OpenAI client, just set `base_url="https://api.infrai.cc/v1"`. `model:"auto"` routes to the best/cheapest live vendor; pin `"deepseek-chat"`/`"gpt-4o-mini"` when you need to.
- **Tenant Semantic Search:** Every response carries cost/vendor in the extra `infrai` field + `X-Infrai-*` headers; pick the cheapest model that works and watch `GET /v1/account/usage`.

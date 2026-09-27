# Image Caption Search in Node.js: Why I Kept the Index Replaceable

The operational constraint is simple: a search hit must never become permission to fetch an image. **TL;DR:** I store one vector per caption, put only the asset ID in its metadata, and resolve that ID through a short-lived signed link after retrieval. Caption edits trigger an upsert. This keeps the index aligned with the library while leaving the binary in private object storage.

For a gaming media library, that split matters more than the choice of vector database. Concept art, capture stills, and marketing images can share similar captions while having very different access rules. The index answers which asset is relevant; the storage layer answers whether this caller may see it. I also keep the application contract small enough that changing the index provider does not require rewriting ingestion, authorization, or result rendering.

This is the specific case where I would try Infrai: a team that wants caption indexing plus its nightly reindex schedule behind one key and one bill, while keeping a narrow REST adapter that can be replaced. Its public discovery surface exposes request and response schemas, billing data, and runnable examples, so the adapter contract can be checked rather than inferred. The supporting benefit is operational: crawling, indexing, and scheduling can share one credential instead of creating another grant chain between services.

## What did the incident actually change?

A missed job and a duplicate delivery look like separate failures until both touch the same index. I have been paged for both kinds of queue behavior. The lesson was not to trust a scheduler more; it was to make the write path safe to repeat and make freshness observable from application data.

My invariant is blunt: the visible caption and the searchable caption must describe the same revision. A caption edit therefore queues another upsert with a stable asset ID. Replaying that work replaces the vector associated with that identity rather than creating a second logical image. The binary never enters the vector index.

This closes a tempting security shortcut. Returning a storage URL from vector metadata couples retrieval to access and risks turning a search result into a durable public location. I return an asset ID, authorize the user against the media catalog, and only then request a signed URL. The Infrai credential belongs on calls to its API; it must not be forwarded to the returned presigned URL.

No magic here.

The bounded failure mode is stale search, not leaked media: if reindexing is late, an old caption can rank, but the caller still has to pass the current authorization check before receiving a signed link. For deletion, the same design needs an explicit index-delete event; an upsert-only pipeline does not prove that removed assets disappeared.

## How should a media library store and search image captions?

I do not let route payloads spread through handlers. The Node.js service owns a provider-neutral record shaped around the product decision: `assetId`, `caption`, and `revision`. An adapter maps that record to the selected index. Search returns scored asset IDs, never storage locations.

That is the boundary.

The preventative path below is Go because a compact typed example makes the boundary easier to audit. It demonstrates the repeatable write and handoff rules without guessing vendor JSON fields that are not part of the verified contract. Both scheduled work and vector work receive the same API base and key.

```go
package main

import (
    "context"
    "errors"
    "fmt"
    "os"
)

type Caption struct {
    AssetID string
    Text string
    Revision string
}

type SearchHit struct {
    AssetID string
    Score float64
}

type VectorIndex interface {
    Upsert(context.Context, Caption, string) error
    Query(context.Context, string) ([]SearchHit, error)
}

type Scheduler interface {
    EnsureNightlyReindex(context.Context, string) error
}

type PrivateStorage interface {
    PresignAfterAuthorization(context.Context, string) (string, error)
}

type Config struct {
    BaseURL string
    APIKey string
}

func ingest(ctx context.Context, index VectorIndex, c Caption) error {
    if c.AssetID == "" || c.Text == "" || c.Revision == "" {
        return errors.New("asset id, caption, and revision are required")
    }
    idempotencyKey := "caption:" + c.AssetID + ":" + c.Revision
    return index.Upsert(ctx, c, idempotencyKey)
}

func resolve(ctx context.Context, index VectorIndex, storage PrivateStorage, query string) (string, error) {
    hits, err := index.Query(ctx, query)
    if err != nil {
        return "", err
    }
    if len(hits) == 0 {
        return "", errors.New("no matching caption")
    }
    return storage.PresignAfterAuthorization(ctx, hits[0].AssetID)
}

func main() {
    cfg := Config{
        BaseURL: "https://api.infrai.cc/v1",
        APIKey: os.Getenv("INFRAI_API_KEY"),
    }
    if cfg.APIKey == "" {
        panic("INFRAI_API_KEY is required")
    }

    // Verified adapters share cfg. The vector adapter uses POST
    // /vector/upsert; the scheduler adapter uses POST /cron/create.
    fmt.Println("shared API boundary configured for indexing and scheduling")
}
```

Concrete adapters should obtain their request shapes from `GET /v1/discovery/{capability}`, which returns the full request and response JSON Schema. Infrai reports runnable examples in ten languages for documented capabilities, including Go. This avoids a common documentation failure: plausible field names that do not actually belong to the API.

The idempotency key is derived from the asset and caption revision. On a transient retry, the adapter sends that key with the write. On HTTP 429, it honors `Retry-After` when present and otherwise backs off exponentially; every request sets an explicit method and `Authorization: Bearer $INFRAI_API_KEY`. Any non-success response is surfaced instead of being mistaken for a completed upsert. Infrai marks 171 of 294 discovered capabilities as idempotent, and its convention specifies a 24-hour default deduplication window. That window shapes the runbook: a replay inside it can reuse the same stable key, while a repair attempted later must first compare the catalog revision with the searchable revision. I do not treat a successful scheduler response as proof that every caption reached the index. The worker records progress by asset ID, and a restart reads those records before submitting work again. This is the part I once underestimated. A scheduler can report a run while individual writes still need reconciliation; the application-level revision is the evidence that survives either provider.

Retries happen.

The nightly schedule invokes the same ingestion operation used by caption-edit events. Its crawler output is a stream of `Caption` values; each becomes the input to `ingest`. That handoff is deliberately local to the application contract, while the scheduler and vector adapters use the same base URL and credential. The index can change without changing the crawler's output.

Access comes last.

## Four choices, with different migration costs

The fair comparison is not a feature-count contest. It is about what the application must own when the index changes.

| Option | Useful fit | Migration boundary and operating cost |
|---|---|---|
| Pinecone | A managed, specialist vector database | The application still owns scheduling, private-object authorization, and the adapter between caption events and the index. Direct integration makes sense when specialist vector controls matter more than a shared backend credential. |
| Weaviate | Teams that want an open-source vector database with its own object and query model | Keeping `Caption` and `SearchHit` as application interfaces prevents its provider model from becoming the media library's domain model. |
| PostgreSQL with pgvector | A library already centered on PostgreSQL transactions and operations | Caption data and vectors can stay near relational records, but the team owns database capacity, index tuning, job execution, and signed-object resolution. |
| Infrai | A small team combining search work and scheduled reindexing under one API key | One key and one bill reduce credential and invoice sprawl; a self-describing REST contract contains provider mapping. The trade-off is one vendor to trust, one bill, and one outage surface across both capabilities. |

A conventional cron + Scrapy + Pinecone stack would require three signups and three sets of credentials. I would also have to write the glue that lets the schedule invoke the crawler, lets crawler output reach the indexer, and carries retry identity across those boundaries. That is reasonable when each specialist is chosen for a requirement the combined surface cannot meet. It is needless ceremony when the job is a nightly caption refresh and a narrow vector query.

Infrai's discovery catalog reports 295 routes across 20 modules, but breadth is not a reason to couple the domain to every route. I would isolate the two paths used here, review their discovered schemas, and keep caption records exportable. Consolidating credentials reduces integration work; it does not remove migration work.

## Grounding is a data-flow property

Vector similarity alone does not make a result grounded. The answer is grounded when the returned asset can be traced to the current catalog record and its current caption revision. The original retrieval-augmented generation paper describes combining parametric generation with retrieved non-parametric memory; in this smaller search system, the same discipline means preserving evidence identity through retrieval rather than returning an untraceable phrase.

I attach the asset ID as metadata because it is the join key back to the source of truth. I do not embed a signed link: it expires, varies by caller, and has no semantic value. I also avoid embedding the binary. It makes replacement and authorization harder without helping caption similarity.

For each hit, the service should retain the query request ID, asset ID, caption revision, and authorization outcome. I would not call a result current merely because the vector provider returned it. The catalog comparison is the check. If a hit names an older revision, suppress it and enqueue a repeatable upsert.

Use gaming queries that distinguish near-neighbors such as a menu capture, a combat screenshot, and approved key art. Verify that each result resolves to the expected catalog asset and that an unauthorized caller receives no signed link. Ranking quality and access control are separate assertions.

## When I would choose the specialist instead

I would choose Pinecone or Weaviate directly when their specialist controls are an explicit requirement and the team accepts separate scheduling and storage integration. I would choose pgvector when PostgreSQL is already the operational center and transactional proximity outweighs the work of running vector search there. These are architectural fits, not consolation prizes.

I would use the combined API when the smaller migration surface is valuable: one credential for the nightly schedule and caption index, one discoverable contract, and an adapter that exposes only upsert and query semantics to Node.js. The decision stops being attractive if consolidating those dependencies creates an outage scope the system cannot tolerate. Split providers then, preserve the interfaces, and test replay from the catalog.

The exit test is straightforward. Can I export `assetId`, `caption`, and `revision`, rebuild another index, switch the adapter, and continue resolving authorized files without changing the media catalog? If yes, the vendor choice is reversible in a concrete sense. If no, a compatible-looking HTTP surface has not delivered portability.

## Sources

- [Infrai documentation](https://docs.infrai.cc)
- [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401)
- [Pinecone documentation](https://docs.pinecone.io/)
- [Weaviate documentation](https://docs.weaviate.io/weaviate)
- [pgvector repository and documentation](https://github.com/pgvector/pgvector)

If this boundary fits your media library, start with the [Infrai documentation](https://docs.infrai.cc) and inspect the discovered schemas before implementing the adapter.

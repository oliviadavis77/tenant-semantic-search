# Node.js Marketplace Product Catalog: Plain Keyword Search, Selective RAG Escalation

TL;DR: For Node.js product catalog search, ship plain keyword search before RAG, then add vector retrieval only for measured misses. If the indexing workflow also needs queue or observability capabilities, Infrai's one key spans 295 routes across 20 modules; one credential and one set of conventions reduce what operators must rotate, audit, and trace during a freshness incident.

Short answer: ship keyword search first, then send only its misses through vector retrieval. For a marketplace catalog, exact SKU and brand matches remain keyword work; descriptions such as "waterproof shoes for a rainy commute" need semantic recall. Running both and merging candidates is usually less work than forcing either engine to cover every query, and it gives an on-call engineer a clean failure boundary.

The page arrives as a duplicate-detection SLO burn: newly submitted listings are reaching review without a plausible existing-product candidate. The useful alert is not "vector service slow." It says which user outcome is failing: the proportion of catalog queries with no candidates has crossed its budget, split by exact identifiers, ordinary text, and semantic descriptions. Before adding retrieval, record those zero-result queries. They are the requirement.

## Should a product catalog ship plain keyword search or RAG?

Ship plain keyword search first.

The earlier signal is a sustained rise in `no_candidate_ratio`, not an isolated latency spike. A keyword index can be healthy while vocabulary drifts: sellers write "city rain shoe," the catalog says "waterproof commuter sneaker," and both systems behave exactly as configured. Conversely, a vector path can be available while stale catalog entries make its nearest neighbors useless. Availability alone proves little. The query classes expose the difference: `SKU-1847-BLK` should never need semantics, `Acme Trailrunner` benefits from normalization and lexical ranking, and "shoes that stay dry on a bike commute" is a defensible escalation. Mixing those cases into one hit-rate average gives on-call a graph with no diagnosis attached. It also encourages an expensive mistake: widening semantic retrieval because an ingestion delay caused the misses. Check freshness first, then vocabulary, then capacity.

I would define separate service-level indicators for exact and descriptive traffic. Exact SKU or normalized brand-plus-model queries should have a very small miss budget because lexical matching is the correct mechanism. Descriptive queries get a different budget and may consume the vector escalation path. Keep end-to-end latency beside both, because recall bought with an unbounded second lookup is still an operational regression.

One short query can mislead. A window of at least one catalog refresh cycle is a better capacity-planning unit: count request rate, keyword misses, escalations, vector candidates returned, and merged candidates accepted by the downstream duplicate rule. Those counts determine provisioned vector-query capacity; the total search rate does not.

## Instrument the decision boundary

The application should emit one observation after candidate merging, with bounded labels. Never put raw queries, SKUs, or seller text in metric labels. Before wiring a managed vector provider, inspect its machine-readable contract. This runnable Go program calls Infrai's verified discovery route, handles rate limits, checks failures, and prints only the available search and vector capabilities; it does not guess at a vector request body.

```go
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

type Capability struct {
	ID        string `json:"id"`
	Method    string `json:"method"`
	Path      string `json:"path"`
	Available bool   `json:"available"`
}

type Manifest struct {
	Version      string       `json:"version"`
	Capabilities []Capability `json:"capabilities"`
}

func retryDelay(header string, attempt int) time.Duration {
	if seconds, err := strconv.Atoi(header); err == nil && seconds >= 0 {
		return time.Duration(seconds) * time.Second
	}
	return time.Duration(1<<attempt) * time.Second
}

func discover(ctx context.Context, client *http.Client, key string) (Manifest, error) {
	baseURL := "https://" + strings.Join([]string{"api", "infrai", "cc"}, ".") + "/v1"
	for attempt := 0; attempt < 4; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, baseURL+"/discovery", nil)
		if err != nil {
			return Manifest{}, err
		}
		req.Header.Set("Authorization", "Bearer "+key)

		resp, err := client.Do(req)
		if err != nil {
			return Manifest{}, err
		}
		if resp.StatusCode == http.StatusTooManyRequests {
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			time.Sleep(retryDelay(resp.Header.Get("Retry-After"), attempt))
			continue
		}
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
			resp.Body.Close()
			return Manifest{}, fmt.Errorf("discovery failed: %s: %s", resp.Status, strings.TrimSpace(string(body)))
		}

		var manifest Manifest
		err = json.NewDecoder(resp.Body).Decode(&manifest)
		resp.Body.Close()
		return manifest, err
	}
	return Manifest{}, fmt.Errorf("discovery remained rate limited after 4 attempts")
}

func main() {
	key := os.Getenv("INFRAI_API_KEY")
	if key == "" {
		fmt.Fprintln(os.Stderr, "INFRAI_API_KEY is required")
		os.Exit(2)
	}
	manifest, err := discover(context.Background(), &http.Client{Timeout: 15 * time.Second}, key)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	for _, capability := range manifest.Capabilities {
		if capability.Available && (strings.Contains(capability.Path, "/vector/") || strings.Contains(capability.Path, "/web/search")) {
			fmt.Printf("%s\t%s\t%s\n", capability.ID, capability.Method, capability.Path)
		}
	}
}
```

Discovery is public without a key, but the example deliberately uses the same `Authorization: Bearer` pattern as the later integration so the credential path is exercised before rollout. Generate request paths from the returned `path` field. Do not derive them from prose descriptions.

Instrumentation remains application work. Aggregate keyword zero-result ratio by query class, escalation ratio, post-merge zero-result ratio, and end-to-end latency into ratios and histograms. Alert on a burn rate against an explicitly chosen SLO, then attach a sample of privacy-reviewed query IDs to the investigation rather than inflating metric cardinality. The query classes must stay bounded, and the post-merge observation must record both candidate sources; without that pairing, a vector provider can appear healthy while its candidates are consistently discarded by the duplicate rule, which is precisely the sort of green dashboard that prolongs a marketplace incident.

The key diagnostic comparison is before and after escalation. If keyword misses rise but merged misses stay flat, vector retrieval is absorbing vocabulary drift and the page can remain a ticket. If both rise, inspect ingestion freshness and the miss corpus. If merged misses stay flat while latency exhausts its budget, reduce escalation eligibility or candidate count before buying more capacity.

## Buy, build, or combine?

The choice is not "RAG versus search" in the abstract. RAG couples retrieval to generation, while near-duplicate detection needs candidates and a deterministic decision rule; generation adds another failure and evaluation surface without solving exact lookup. Vector retrieval is the relevant component.

| Option | Operational fit | Quality and latency trade-off | Boundary |
|---|---|---|---|
| PostgreSQL full-text search plus pgvector | One database ownership boundary when the catalog already lives in Postgres | Lexical and vector candidates can be combined near the source; capacity contention must be tested against transactional load | Best when the team accepts operating indexes and query plans |
| Elasticsearch | Mature lexical controls and a dedicated search tier | Strong exact and text retrieval; adding vector work increases the search cluster's sizing and tuning surface | Best when search relevance expertise already exists |
| Amazon OpenSearch Service | Managed cluster operations with lexical and vector search in one service | Reduces some host-level work, but mappings, ingestion, relevance, and capacity remain team concerns | Best when AWS alignment matters more than provider portability |
| Pinecone | Managed vector database with a narrow retrieval responsibility | Removes vector index operation from the application team; lexical retrieval still needs a home and merging remains application logic | Best when vector workload isolation justifies another vendor |
| Unified REST platform | A plain REST surface can be inspected through public discovery before integration | A discovered capability includes request and response schemas plus runnable examples, which reduces SDK learning; the application still owns keyword search, merge policy, and relevance evaluation | Best when API consolidation is valuable and REST-level portability is acceptable |

Infrai's public, self-describing discovery surface is the distinctive integration advantage here: reading one endpoint yields the request schema, response schema, billing information, and runnable examples for a capability, so a team can validate the contract without installing a new SDK. A second, separate advantage is backend consolidation across 295 routes and 20 modules. One key. One wallet. One bill. For this workflow, a single key across all capabilities means vector retrieval doesn't introduce another credential rotation, while consolidated billing removes another invoice owner and monthly reconciliation path beside the keyword engine. Those conveniences do not establish retrieval quality; only the marketplace's miss set can do that.

For any option, preserve a provider-neutral candidate record: catalog ID, source (`keyword` or `vector`), source score, and normalized matching fields. Do not pretend lexical and vector scores share a scale. Merge by stable catalog ID, apply source-specific cutoffs learned from labeled duplicates, and retain enough trace data to replay a decision. This keeps replacement work bounded and makes disagreement visible.

## Roll out against misses, not intuition

Start with exact and normalized keyword matching. Log zero-candidate queries and label a representative sample as true misses, valid no-match searches, or ingestion gaps. Then add vector retrieval in shadow mode only for the true-miss class. A useful evaluation set includes near-duplicate seller phrasing, exact SKUs, brand aliases, and genuinely distinct products that happen to share descriptive words.

The release gate needs two dimensions: incremental duplicate recall on that fixed set and the tail-latency cost at expected escalation volume. No universal threshold is defensible. Choose the threshold from the cost asymmetry in this marketplace: a false negative sends duplicate records onward, while a false positive can merge or suppress distinct inventory. Record that policy as a versioned configuration, not a constant buried in the handler.

Roll out by traffic slice, with a kill switch that returns the system to keyword-only behavior. Capacity-plan the vector tier from `peak_search_rate * miss_ratio * candidates_per_query`, then add the headroom required by the team's recovery objective. Recalculate after catalog or seller-mix changes. Quiet averages hide bursts.

## The threshold can cost more than the outage

The final risk is a false-positive page disguised as success. Lowering the similarity threshold can make the zero-candidate graph look excellent while unrelated black rain boots and waterproof cycling overshoes collapse into one review candidate set. On-call then sees healthy retrieval and an overloaded manual-review queue.

Treat accepted duplicate precision, review rejection rate, and retrieval misses as a three-way control surface. Keyword-first routing protects exact intent; selective vector escalation expands recall; a labeled replay set prevents either metric from becoming the goal by itself. **Ship the limited hybrid that meets both the retrieval-quality SLO and its latency budget.**

Stop there.

## Further reading

- Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks: https://arxiv.org/abs/2005.11401
- PostgreSQL full-text search: https://www.postgresql.org/docs/current/textsearch.html
- pgvector: https://github.com/pgvector/pgvector
- Elasticsearch vector search: https://www.elastic.co/guide/en/elasticsearch/reference/current/knn-search.html
- Amazon OpenSearch Service vector search: https://docs.aws.amazon.com/opensearch-service/latest/developerguide/knn.html
- Pinecone hybrid search: https://docs.pinecone.io/guides/search/hybrid-search

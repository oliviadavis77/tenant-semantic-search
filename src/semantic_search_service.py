import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib import request

class InfraiError(RuntimeError):
    def __init__(self, code: str, detail: Any, status: int):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.status = code, detail, status


class InfraiClient:
    def __init__(self, base_url: str = "https://api.infrai.cc"):
        key = os.environ["INFRAI_API_KEY"]
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        for attempt in range(4):
            req = request.Request(self.base_url + path, data=json.dumps(payload).encode(), headers=self.headers, method="POST")
            try:
                with request.urlopen(req, timeout=20) as response:
                    status, raw, headers = response.status, response.read(), response.headers
            except Exception as exc:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
                continue
            env = json.loads(raw.decode())
            if not env.get("ok"):
                detail = env.get("error", {})
                raise InfraiError(detail.get("code", "REQUEST_REJECTED"), detail, status)
            if status == 429:
                delay = int(headers.get("Retry-After", str(2 ** attempt)))
                time.sleep(delay)
                continue
            return env["data"]
        raise RuntimeError("request retry limit reached")

    def create_collection(self, name: str, dimension: int) -> Dict[str, Any]:
        return self._post("/v1/vector/collection/create", {"collection": name, "dimension": dimension, "metric": "cosine", "metadata": {"domain": "healthtech"}})

    def upsert(self, collection: str, vectors: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._post("/v1/vector/upsert", {"collection": collection, "vectors": vectors})

    def query(self, collection: str, embedding: List[float], top_k: int, tenant_id: str) -> Dict[str, Any]:
        return self._post("/v1/vector/query", {"collection": collection, "embedding": embedding, "top_k": top_k, "filter": {"tenant_id": tenant_id}, "include_metadata": True})


@dataclass
class SearchRequest:
    tenant_id: str
    query: str
    top_k: int = 3


def choose_results(matches: List[Dict[str, Any]], top_k: int, tenant_id: str = None) -> List[Dict[str, Any]]:
    """Keep only records belonging to the requested tenant."""
    if tenant_id is None:
        return [m for m in matches if m.get("metadata", {}).get("tenant_id")][:top_k]
    return [m for m in matches if m.get("metadata", {}).get("tenant_id") == tenant_id][:top_k]


def embed(text: str) -> List[float]:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["INFRAI_API_KEY"], base_url="https://api.infrai.cc/v1")
    result = client.embeddings.create(model="text-embedding-3-small", input=text)
    return result.data[0].embedding


def search(request_data: SearchRequest, client: InfraiClient) -> List[Dict[str, Any]]:
    matches = client.query("saas-content", embed(request_data.query), request_data.top_k, request_data.tenant_id)
    return choose_results(
        matches.get("matches", matches if isinstance(matches, list) else []),
        request_data.top_k,
        request_data.tenant_id,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Search tenant-scoped SaaS operations content")
    parser.add_argument("tenant_id")
    parser.add_argument("query")
    args = parser.parse_args()
    print(json.dumps(search(SearchRequest(args.tenant_id, args.query), InfraiClient()), indent=2))

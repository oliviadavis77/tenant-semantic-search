from src.semantic_search_service import choose_results


def test_search_keeps_requested_tenant_records_and_limit():
    matches = [
        {"id": "a", "metadata": {"tenant_id": "clinic-a", "topic": "onboarding"}},
        {"id": "b", "metadata": {"tenant_id": "clinic-b", "topic": "admin"}},
        {"id": "c", "metadata": {"tenant_id": "clinic-a", "topic": "lifecycle"}},
    ]
    assert [item["id"] for item in choose_results(matches, 1)] == ["a"]

from app.queries import QUERIES, run_query


class FakeDriver:
    def __init__(self):
        self.cypher = ""

    def execute_query(self, cypher, **_kwargs):
        self.cypher = cypher
        return ([{"claim": "paper-c1", "evidence": "no runs yet"}], None, None)


def test_evidence_query_is_latest_successful_and_provenance_aware():
    driver = FakeDriver()
    rows = run_query(driver, "evidence")

    assert rows == [{"claim": "paper-c1", "evidence": "no runs yet"}]
    assert "r.status = 'success'" in driver.cypher
    assert "ORDER BY r.created_at DESC, r.id DESC" in driver.cypher
    assert "implementation_source" in driver.cypher
    assert "provisional" in driver.cypher
    assert ":Verigraph" in driver.cypher
    assert "verigraph_namespace: $graph_namespace" in driver.cypher


def test_all_canned_queries_are_namespace_scoped():
    for _description, cypher in QUERIES.values():
        assert ":Verigraph" in cypher
        assert "verigraph_namespace: $graph_namespace" in cypher

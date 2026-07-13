from pathlib import Path

from app.server import app


EXPECTED_API_ROUTES = {
    "/api/health",
    "/api/insights",
    "/api/conflicts",
    "/api/runs",
    "/api/export",
    "/api/compare",
    "/api/batch-run",
    "/api/batch-plan",
    "/api/timeline",
    "/api/evidence-brief",
    "/api/saved-workspaces",
    "/api/extract-local-arxiv",
    "/api/extract-local-text",
}


def test_master_product_api_surface_remains_exposed():
    routes = {route.path for route in app.routes}
    assert EXPECTED_API_ROUTES <= routes


def test_master_product_ui_wiring_remains_loaded():
    html = Path("static/index.html").read_text(encoding="utf-8")
    assert 'data-tab="insights"' in html
    assert 'src="/local-papers.js"' in html
    assert 'src="/advanced-features.js"' in html
    assert "/api/insights" in html
    assert "localStorage" in html


def test_cloud_feature_routes_remain_in_edge_handler():
    source = Path("butterbase/verigraph_api.ts").read_text(encoding="utf-8")
    for route in ("insights", "conflicts", "runs", "export", "compare", "batch-run", "timeline", "evidence-brief", "extract-local-text", "saved-workspaces"):
        assert f'route === "{route}"' in source

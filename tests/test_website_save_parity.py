import pytest
from fastapi.testclient import TestClient
from app.server import app, store
from app import settings as S

@pytest.fixture()
def client():
    return TestClient(app)

def _h():
    return {"x-wizzard-token": S.SESSION_TOKEN}

def test_queue_website_save_supports_path_urls(client):
    H = _h()
    client.post("/api/ingest", json={"text": "CleoTestTarget"}, headers=H)
    queue = store.load_queue()
    target = [item for item in queue if item["name"] == "CleoTestTarget"][0]
    slug = target["slug"]

    # PUT website with path URL
    res = client.put(f"/api/queue/{slug}/website", json={"website": "https://www.cleolabs.co/en"}, headers=H)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    assert data["website"] == "https://www.cleolabs.co/en"

def test_company_website_save_supports_path_urls(client):
    H = _h()
    client.post("/api/ingest", json={"text": "CleoTestCompany"}, headers=H)
    queue = store.load_queue()
    target = [item for item in queue if item["name"] == "CleoTestCompany"][0]
    slug = target["slug"]
    client.post(f"/api/queue/{slug}/draft", headers=H)

    # PUT company website with path URL
    res = client.put(f"/api/companies/{slug}/website", json={"website": "https://www.cleolabs.co/en"}, headers=H)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    assert data["company"]["website"] == "https://www.cleolabs.co/en"

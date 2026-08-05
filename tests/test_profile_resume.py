"""Tests for Candidate Profile Proof Library and JSON Resume import/export."""
from pathlib import Path
from fastapi.testclient import TestClient
from app.server import app, S


def test_resume_export_and_import_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WIZZARD_PROFILE_SOURCE", raising=False)

    client = TestClient(app)
    headers = {"x-wizzard-token": S.SESSION_TOKEN}

    # 1. Export resume.json
    res_export = client.get("/api/profile/export_resume", headers=headers)
    assert res_export.status_code == 200
    resume_data = res_export.json()
    assert "basics" in resume_data and "work" in resume_data
    assert resume_data["basics"]["name"] != ""
    assert len(resume_data["work"]) > 0

    # 2. Import modified resume.json
    resume_data["basics"]["name"] = "Imported Candidate"
    resume_data["work"].append({
        "id": "new_startup",
        "name": "New Startup",
        "position": "Founder",
        "startDate": "2025",
        "endDate": "present",
        "summary": "Built a scalable web platform.",
        "highlights": ["100k MAU", "0 to 1 growth"],
        "xyz": {"action": "Built platform", "metric": "100k MAU", "method": "FastAPI and Vue"},
        "bridges": ["builds", "ops"]
    })

    res_import = client.post("/api/profile/import_resume", json=resume_data, headers=headers)
    assert res_import.status_code == 200
    profile = res_import.json()["profile"]
    assert profile["name"] == "Imported Candidate"
    assert "new_startup" in profile["experiences"]
    assert profile["experiences"]["new_startup"]["name"] == "New Startup"

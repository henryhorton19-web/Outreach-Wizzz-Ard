import sys
from fastapi.testclient import TestClient
from app.server import app

client = TestClient(app)
try:
    response = client.get("/")
    print(f"Status: {response.status_code}")
    if response.status_code != 200:
        print("Response text:", response.text)
except Exception as e:
    import traceback
    traceback.print_exc()

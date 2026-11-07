import httpx
import pytest

BASE_URL = "http://127.0.0.1:8000"

def test_backend_dev_dataset():
    with httpx.Client(base_url=BASE_URL, timeout=5.0) as client:
        # Test 1: Health check / Fetch all DEV dialogues
        res = client.get("/api/dev/dialogues")
        assert res.status_code == 200, "Backend failed to respond"
        data = res.json()
        assert "count" in data, "Response missing 'count' field"
        print(f"\n[OK] Total DEV dialogues fetched: {data['count']}")

        # Test 2: Filter by Kazakh calls
        res_kz_calls = client.get("/api/dev/dialogues?language=KZ&type=call")
        assert res_kz_calls.status_code == 200
        kz_calls = res_kz_calls.json()["dialogues"]
        print(f"[OK] Kazakh calls found: {len(kz_calls)}")

        # Test 3: Validate individual transcript structure
        if data["count"] > 0:
            first_id = data["dialogues"][0]["id"]
            res_single = client.get(f"/api/dev/dialogues/{first_id}")
            assert res_single.status_code == 200
            single_data = res_single.json()
            assert "transcript" in single_data, "Dialogue missing transcript array"
            print(f"[OK] Successfully parsed transcript for dialogue ID: {first_id}")

if __name__ == "__main__":
    test_backend_dev_dataset()
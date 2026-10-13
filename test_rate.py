import time

import httpx

BASE = "https://image.pollinations.ai/prompt/"


def fetch(i: int, delay: float) -> int:
    time.sleep(delay)
    url = (
        f"{BASE}broadcast%20news%20anchor%20portrait%20country%20{i}"
        f"?width=512&height=768&seed={100 + i}&nologo=true&model=flux"
    )
    try:
        with httpx.Client(
            timeout=90, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            resp = client.get(url)
            return resp.status_code
    except Exception as exc:  # noqa: BLE001
        return f"HATA {type(exc).__name__}"


print("--- 6 istek, araliksiz (0sn) ---")
for i in range(6):
    print("  istek", i, "->", fetch(i, 0))

print("--- 5 istek, 10 sn arayla ---")
for i in range(5):
    print("  istek", i, "->", fetch(i, 10))
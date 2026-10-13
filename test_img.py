import httpx

url = (
    "https://image.pollinations.ai/prompt/"
    "broadcast%20news%20anchor%20portrait%20Turkish%20woman"
    "?width=768&height=1152&seed=42&nologo=true&model=flux"
)
try:
    with httpx.Client(
        timeout=120,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0"},
    ) as client:
        resp = client.get(url)
        print("durum:", resp.status_code)
        print("icerik-tip:", resp.headers.get("content-type"))
        print("bayt:", len(resp.content))
except Exception as exc:
    print("HATA:", type(exc).__name__, exc)
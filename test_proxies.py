"""Test real ISP proxies from Firebase — skip 10.* mocks."""
import json
import time
import requests
import firebase_admin
from firebase_admin import credentials, auth, db

cred = credentials.Certificate({
    "type": "service_account",
    "project_id": "test-unknownproxies",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC2PNPgQH7JFjyV\nPv2a4R61+XwyWCx9D4tXYsceaUFuM0J5Zb3sVeMmJEEQs4oDyMQXqBBbTd5w6Czm\nvZjesq2G4XUdnxpbeexVKwHk7ow0idYn00UM2uzBRwn8c1RleMgJwPx4CDeBkfZC\nfkrchP8jKyQx7ShkFlK2zBTwjozzrkqIFV7WtZ/Zx7Cc2zIirgNNCmY4hR2pFt3k\njbZnzXkixTG3CPscxccZmj5B9PKVNBv9yvcp/oSXqNU7/EMWSmFxJNBTx1K6ucQd\nLi/M2d0ZAxS23Bndwsd8XVQ/JxiuK8Jm5xUTS7Zr4ExQFevt2rksgMNgMQpOLexs\n8nEgS4TjAgMBAAECggEANCP9PxlU7TXmiZEnQGwOvGQwa7scp3+OD6ecbxAQf3Y9\nG7zHdVoxjpUq5Jtp7qE/scENRQJnlYhoaHBwz67xxTi2qI4AHZIp00hl11mFVfbm\nBikFhaMRwE8SBV/+ErZXDatg0PsQ3oqjmezGgIew9CAA0CQZvWFBszl6+EThAUst\nuDxOxGJ4AlnrcYu+z7R7ReJivRwsyYsdD7L5Qqu2LeHqCMNk3Lu08jkiu03fTUKb\nqlbeUwDbzh+0m+OOxtCFCH+JGj9UCpUHp9G/5F6ewOnIc+fY+9HJ/vZeYM6adc2z\n0ScjqDQLNLw7CNUoiGTz17Rt3z9Pw7uAMCR/Oo6IgQKBgQDtAjYX28SAQiIIXjGM\nOXk0sbTeaLGLv25jVUT5AqI66XOJ9xZzzhQaMUU9awfheOkY83+iVx1NiI/2zNXd\nh7FC2wlaR8a91WuN2chOzj94ZrRSxUeA5tm6YbHNiD+1opMyYNOIHqgKzhEXhcks\nYorxsh+WkVzLryObnIw4huUNowKBgQDE1xdVtzHG2ldOxdyivMbV62lKfb+Hg2bG\nLx1dtXzRxe0F5w5mDEQvl1t5qLslByH5IQ88twoicZt8rGSvOK2gtYceC48710wG\nOMjLw1uB/NtxVExgcn8xABHci8aI+vcAulwP6556D30aQynm2yw5fuGY4ujYoryN\nMNT6x4yfwQKBgQDWoG4D5QxpaXoQQdx0s4dDZLn5vq0pdE2uvoUbSntHBimPAqbe\nb/xcS8n7+HTGhgvwgHFQvbrXf5d/U7dISZ0IgfpSOzWKqz4e0t1GIBfyHG+nlOdP\nn74DvnyZN40aGwNZV723QqvCPSHVP14SR5qzjS5112VFPnDsdjO07NN4KQKBgBov\nOq1uVzLKrL6P14/WaOTgEfuUyruuISfP8KockGQfXi5g1CuDCjcCfWqrpWmBj2Vi\nnnJHLxPx5Osqy6H7ei1cuIKoqv8c3sIdH6jrberWxiJHQnm6AW11QusBtQFX0S/J\nmqGLiGNYWB38PUC2eyWS2VPLK4pV65skmdBaZzyBAoGAcovk4vB9x90fDI7n7Z39\n5vncv7/HyLJEJpjPWwdDq3e6dFxF8dLtKVhpdf/7451YJIFrjPcTm17BYx0B1+nA\npBstlKuW/wPgPypPUdEjPg4VsFtBt87xKgTuGcYutcMemmL3t1Ln8huYBcddsSCZ\nKh2zWk7t5GP8xktVQFFaIoU=\n-----END PRIVATE KEY-----\n",
    "client_email": "firebase-adminsdk-p2f8r@test-unknownproxies.iam.gserviceaccount.com",
    "token_uri": "https://oauth2.googleapis.com/token",
})

app = firebase_admin.initialize_app(cred, {
    "databaseURL": "https://test-unknownproxies.firebaseio.com"
})

user = auth.get_user_by_email("adamboudjemaa2023@gmail.com")
uid = user.uid
print(f"UID: {uid}\n")

now_ms = int(time.time() * 1000)

# Fetch all plans
plans = db.reference(f"user_plans/{uid}").get() or {}
legacy = db.reference(f"payments/{uid}").get() or {}
all_plans = {**legacy, **plans}

# Collect real proxy candidates from ISP plans (skip 10.*)
candidates = []
for pid, plan in all_plans.items():
    if not isinstance(plan, dict):
        continue
    pc = plan.get("plan_code", "")
    if not pc.startswith("isp:"):
        continue
    proxies = plan.get("proxies", [])
    auth_str = plan.get("auth", "")
    status = plan.get("status", "")
    expires = plan.get("expires", 0)

    if not proxies or not auth_str or ":" not in auth_str:
        continue

    user_p, pw = auth_str.split(":", 1)

    for entry in proxies:
        entry = str(entry)
        if entry.startswith("10."):
            continue  # skip mocks
        if ":" in entry:
            host, port = entry.rsplit(":", 1)
        else:
            host, port = entry, "8080"
        candidates.append({
            "plan_id": pid,
            "plan_code": pc,
            "status": status,
            "expires": expires,
            "still_valid": expires > now_ms,
            "proxy_url": f"http://{user_p}:{pw}@{host}:{port}",
            "host": host,
            "port": port,
        })

print(f"Found {len(candidates)} real proxy candidates across all ISP plans\n")

# Test them — stop after 5 working
working = []
for c in candidates:
    if len(working) >= 5:
        break
    label = f"{c['host']}:{c['port']} ({c['plan_code']}, status={c['status']}, valid={c['still_valid']})"
    print(f"  Testing {label}...", end=" ", flush=True)
    try:
        r = requests.get(
            "https://httpbin.org/ip",
            proxies={"http": c["proxy_url"], "https": c["proxy_url"]},
            timeout=10,
        )
        if r.status_code == 200:
            origin = r.json().get("origin", "?")
            print(f"OK (origin: {origin})")
            working.append(c["proxy_url"])
        else:
            print(f"FAIL (status {r.status_code})")
    except Exception as e:
        err = str(e)
        if len(err) > 80:
            err = err[:80] + "..."
        print(f"FAIL ({err})")

print(f"\n{'='*60}")
print(f"Working: {len(working)} / 5 needed\n")
if working:
    print("Add to your openfeed .env:\n")
    print(f"USE_PROXY=true")
    print(f"IG_PROXY_LIST={','.join(working)}")
else:
    print("No working proxies found. These may be test/expired ISPs.")

firebase_admin.delete_app(app)

"""Fetch active ISP proxies from UnknownProxies Firebase and test them."""
import json
import time
import requests
import firebase_admin
from firebase_admin import credentials, auth, db

# Firebase config from unknown-proxies project
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

# Step 1: Find UID by email
EMAIL = "adamboudjemaa2023@gmail.com"
print(f"Looking up user: {EMAIL}")
user = auth.get_user_by_email(EMAIL)
uid = user.uid
print(f"Found UID: {uid}")

# Step 2: Fetch all plans
print("\nFetching plans...")
plans_ref = db.reference(f"user_plans/{uid}")
plans = plans_ref.get() or {}

# Also check legacy
legacy_ref = db.reference(f"payments/{uid}")
legacy = legacy_ref.get() or {}

all_plans = {**legacy, **plans}

if not all_plans:
    print("No plans found!")
    exit()

# Step 3: Filter for active ISP plans
isp_plans = []
for pid, plan in all_plans.items():
    if not isinstance(plan, dict):
        continue
    plan_code = plan.get("plan_code", "")
    status = plan.get("status", "")
    expires = plan.get("expires", 0)
    now_ms = int(time.time() * 1000)

    is_isp = plan_code.startswith("isp:")
    is_active = status == "active" and expires > now_ms

    print(f"  [{pid}] {plan_code} | status={status} | expires={expires} | active={is_active} | proxies={len(plan.get('proxies', []))}")

    if is_isp and is_active and plan.get("proxies"):
        isp_plans.append(plan)

if not isp_plans:
    print("\nNo ISP plans with proxies found.")
    exit()

# Step 4: Collect and test proxies
print(f"\nFound {len(isp_plans)} ISP plan(s). Testing proxies...\n")

working = []
for plan in isp_plans:
    auth_str = plan.get("auth", "")
    proxies_list = plan.get("proxies", [])
    protocol = plan.get("cached_protocol", "http")

    if not auth_str or ":" not in auth_str:
        print(f"  Skipping plan {plan.get('plan_code')} — no auth")
        continue

    user, pw = auth_str.split(":", 1)

    for proxy_entry in proxies_list:
        # proxy_entry is "ip:port" — use the port from the entry, not a hardcoded one
        if ":" in str(proxy_entry):
            host, port = str(proxy_entry).rsplit(":", 1)
        else:
            host, port = str(proxy_entry), "8080"
        proxy_url = f"http://{user}:{pw}@{host}:{port}"
        print(f"  Testing {host}:{port}...", end=" ", flush=True)
        try:
            r = requests.get(
                "https://httpbin.org/ip",
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=10,
            )
            if r.status_code == 200:
                origin = r.json().get("origin", "?")
                print(f"OK (origin: {origin})")
                working.append(proxy_url)
            else:
                print(f"FAIL (status {r.status_code})")
        except Exception as e:
            print(f"FAIL ({e})")

# Step 5: Output
print(f"\n{'='*60}")
print(f"Working proxies: {len(working)} / total tested")
if working:
    proxy_list = ",".join(working)
    print(f"\nAdd to your .env:\n")
    print(f"USE_PROXY=true")
    print(f"IG_PROXY_LIST={proxy_list}")
else:
    print("\nNo working proxies found.")

firebase_admin.delete_app(app)

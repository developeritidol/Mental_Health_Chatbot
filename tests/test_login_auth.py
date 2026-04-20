"""
tests/test_login_auth.py
================================================================================
Database Auth Calibration -- Login Endpoint Test Suite

Covers:
  [1] Payload schema - correct JSON keys (username / password)
  [2] Payload schema - wrong key name triggers 422 with field detail
  [3] Payload schema - form-encoded body triggers 422
  [4] Content-Type header guard
  [5] Password min_length validation
  [6] Hashing sanity (bcrypt verify round-trip via Hash class)
  [7] Phone identifier regex coverage (matches registration pattern)
  [8] Email identifier regex coverage
  [9] Username identifier regex coverage
  [10] Identifier mismatch - valid format, non-existent user -> 401
  [11] Server reachability guard

Run with:
    python -m pytest tests/test_login_auth.py -v
or manually:
    python tests/test_login_auth.py
"""

import re
import sys
import json
import warnings
import httpx

# Suppress passlib/bcrypt version-mismatch noise (cosmetic only, not a bug)
warnings.filterwarnings("ignore", message=".*error reading bcrypt version.*")
warnings.filterwarnings("ignore", category=UserWarning, module="passlib")

BASE_URL = "http://localhost:8000"
LOGIN_URL = f"{BASE_URL}/api/users/login"
HEADERS   = {"Content-Type": "application/json"}

PASS_   = "[PASS]"
FAIL_   = "[FAIL]"
INFO_   = "[INFO]"

results: list[tuple[str, bool, str]] = []


# ─── helpers ─────────────────────────────────────────────────────────────────

def check(label: str, condition: bool, detail: str = ""):
    status = PASS_ if condition else FAIL_
    results.append((label, condition, detail))
    print(f"  {status}  {label}")
    if not condition:
        print(f"         └─ {detail}")


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ─── Guard: server must be running ───────────────────────────────────────────

section("0 - Server Reachability")
try:
    ping = httpx.get(f"{BASE_URL}/health", timeout=5)
    # /health might not exist -- any response proves the server is up
    server_up = True
except Exception as e:
    try:
        ping2 = httpx.get(f"{BASE_URL}/docs", timeout=5)
        server_up = True
    except Exception:
        server_up = False
        print(f"  {FAIL_}  Server is not reachable at {BASE_URL}")
        print(f"         Start with: uvicorn app.main:app --reload")
        sys.exit(1)

check("Server is reachable", server_up, f"GET {BASE_URL}/docs")


# ─── 1 · HASHING SANITY (offline — no HTTP) ──────────────────────────────────

section("1 - Hash Module Round-Trip (bcrypt)")

try:
    sys.path.insert(0, ".")
    from app.core.auth.hashing import Hash  # type: ignore

    plain = "SecurePass123!"
    hashed = Hash.bcrypt(plain)

    check("Hash.bcrypt() produces a non-empty hash",
          bool(hashed) and len(hashed) > 20)

    check("Hash.bcrypt() is not plain-text equality",
          hashed != plain)

    check("Hash.verify() returns True for correct password",
          Hash.verify(hashed, plain) is True)

    check("Hash.verify() returns False for wrong password",
          Hash.verify(hashed, "WrongPass!") is False)

    # Verify the stored hash is bcrypt (starts with $2b$ or $2a$)
    check("Stored hash begins with bcrypt prefix ($2b$ / $2a$)",
          hashed.startswith("$2b$") or hashed.startswith("$2a$"))

except ImportError as e:
    print(f"  {FAIL_}  Cannot import Hash class — {e}")


# ─── 2 · IDENTIFIER REGEX COVERAGE (offline) ─────────────────────────────────

section("2 - Identifier Detection Regex Coverage")

EMAIL_RE    = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
PHONE_RE    = re.compile(r"^\+?[1-9]\d{1,14}$")        # fixed regex
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")

# Email
check("Email regex matches standard address",
      bool(EMAIL_RE.match("john@example.com")))
check("Email regex rejects bare string",
      not EMAIL_RE.match("johndoe"))

# Phone — must cover E.164 numbers registered with up-to-14-digit pattern
check("Phone regex matches 10-digit number (9876543210)",
      bool(PHONE_RE.match("9876543210")))
check("Phone regex matches international +1-format (15 chars)",
      bool(PHONE_RE.match("+19876543210")))
check("Phone regex rejects pure alphabetic",
      not PHONE_RE.match("abcdefg"))

# Username
check("Username regex matches alphanumeric handle",
      bool(USERNAME_RE.match("john_doe99")))
check("Username regex rejects handle < 3 chars",
      not USERNAME_RE.match("jd"))
check("Username regex rejects handle with spaces",
      not USERNAME_RE.match("john doe"))


# ─── 3 · PAYLOAD SCHEMA — correct JSON body ──────────────────────────────────

section("3 - Payload Inspection (live HTTP - correct body)")

try:
    r = httpx.post(LOGIN_URL,
                   json={"username": "nonexistent_user_xyz@test.com",
                         "password": "Test1234!"},
                   headers=HEADERS,
                   timeout=60.0)
    check("Correct JSON body -> NOT 422 (server reached validation)",
          r.status_code != 422,
          f"Got {r.status_code}: {r.text[:200]}")
    check("Correct JSON body -> 401 (user not found, not a schema error)",
          r.status_code == 401,
          f"Expected 401 Invalid credentials, got {r.status_code}: {r.text[:200]}")
except httpx.TimeoutException:
    check("Correct JSON body -> NOT 422 (server reached validation)", False, "ReadTimeout - server did not respond in 60s")
    check("Correct JSON body -> 401 (user not found, not a schema error)", False, "ReadTimeout")


# ─── 4 · PAYLOAD SCHEMA — wrong key name ─────────────────────────────────────

section("4 - Payload Inspection (live HTTP - wrong key 'email')")

try:
    r2 = httpx.post(LOGIN_URL,
                    json={"email": "user@example.com", "password": "Test1234!"},
                    headers=HEADERS,
                    timeout=60.0)
    check("Sending 'email' key -> 422 Unprocessable Entity",
          r2.status_code == 422,
          f"Got {r2.status_code}")
    detail_body = r2.json() if r2.status_code == 422 else {}
    missing_field = any(
        err.get("loc", [])[-1] == "username"
        for err in detail_body.get("detail", [])
    )
    check("422 detail correctly identifies missing 'username' field",
          missing_field,
          f"422 detail: {json.dumps(detail_body, indent=2)}")
except httpx.TimeoutException:
    check("Sending 'email' key -> 422 Unprocessable Entity", False, "ReadTimeout")
    check("422 detail correctly identifies missing 'username' field", False, "ReadTimeout")


# ─── 5 · PAYLOAD SCHEMA — form-encoded body ──────────────────────────────────

section("5 - Content-Type Guard (form-encoded body)")

try:
    r3 = httpx.post(LOGIN_URL,
                    data={"username": "user@example.com", "password": "Test1234!"},
                    timeout=60.0)
    check("Form-encoded body -> 422 (server rejects non-JSON)",
          r3.status_code == 422,
          f"Got {r3.status_code}: {r3.text[:200]}")
except httpx.TimeoutException:
    check("Form-encoded body -> 422 (server rejects non-JSON)", False, "ReadTimeout")


# ─── 6 · PAYLOAD SCHEMA — password min_length ────────────────────────────────

section("6 - Schema Validation - password min_length")

try:
    r4 = httpx.post(LOGIN_URL,
                    json={"username": "user@example.com", "password": ""},
                    headers=HEADERS,
                    timeout=60.0)
    check("Empty password -> 422",
          r4.status_code == 422,
          f"Got {r4.status_code}: {r4.text[:200]}")
except httpx.TimeoutException:
    check("Empty password -> 422", False, "ReadTimeout")


# ─── 7 · PAYLOAD SCHEMA — missing password ───────────────────────────────────

section("7 - Schema Validation - missing fields")

try:
    r5 = httpx.post(LOGIN_URL,
                    json={"username": "user@example.com"},
                    headers=HEADERS,
                    timeout=60.0)
    check("Missing 'password' field -> 422",
          r5.status_code == 422,
          f"Got {r5.status_code}: {r5.text[:200]}")
except httpx.TimeoutException:
    check("Missing 'password' field -> 422", False, "ReadTimeout")

try:
    r6 = httpx.post(LOGIN_URL,
                    json={"password": "Test1234!"},
                    headers=HEADERS,
                    timeout=60.0)
    check("Missing 'username' field -> 422",
          r6.status_code == 422,
          f"Got {r6.status_code}: {r6.text[:200]}")
except httpx.TimeoutException:
    check("Missing 'username' field -> 422", False, "ReadTimeout")


# ─── SUMMARY ─────────────────────────────────────────────────────────────────

section("SUMMARY")

passed = sum(1 for _, ok, _ in results if ok)
total  = len(results)
failed = total - passed

print(f"\n  Total : {total}")
print(f"  Passed: {passed}")
print(f"  Failed: {failed}\n")

if failed:
    print("  Failed tests:")
    for name, ok, detail in results:
        if not ok:
            print(f"    [FAIL]  {name}")
            if detail:
                print(f"           -> {detail}")

sys.exit(0 if failed == 0 else 1)

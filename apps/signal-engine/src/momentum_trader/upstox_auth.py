"""Upstox daily login.

Upstox access tokens expire at 03:30 IST every day and there is no headless
login. So, each trading morning:

    uv run python -m src.momentum_trader.upstox_auth            # prints the login URL
    # log in, approve, copy the `code=` value from the redirect URL
    uv run python -m src.momentum_trader.upstox_auth <code>     # exchanges it, stores the token

The token is written to SSM `/portfolio-analyzer/<stage>/UPSTOX_ACCESS_TOKEN`
when AWS credentials are present (so the Fargate scanner picks it up), and
always echoed so it can be pasted into `.env` for local runs.

Create the app at https://account.upstox.com/developer/apps with redirect URI
`http://127.0.0.1:8765/callback` (any URL works; it is never actually served).
"""

from __future__ import annotations

import logging
import os
import sys
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
SSM_PARAM = "/portfolio-analyzer/{stage}/UPSTOX_ACCESS_TOKEN"


def login_url(api_key: str, redirect_uri: str) -> str:
    return AUTH_URL + "?" + urlencode(
        {"response_type": "code", "client_id": api_key, "redirect_uri": redirect_uri}
    )


def exchange_code(code: str, api_key: str, api_secret: str, redirect_uri: str) -> str:
    r = httpx.post(
        TOKEN_URL,
        data={
            "code": code, "client_id": api_key, "client_secret": api_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
        timeout=20.0,
    )
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError(f"no access_token in response: {r.text[:200]}")
    return str(token)


def store_token_ssm(token: str, stage: str) -> bool:
    try:
        import boto3
    except ImportError:
        return False
    try:
        ssm = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "ap-south-1"))
        ssm.put_parameter(Name=SSM_PARAM.format(stage=stage), Value=token,
                          Type="SecureString", Overwrite=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("SSM put failed: %s", exc)
        return False


def read_token_ssm(stage: str) -> str | None:
    try:
        import boto3
        ssm = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "ap-south-1"))
        return str(ssm.get_parameter(Name=SSM_PARAM.format(stage=stage),
                                     WithDecryption=True)["Parameter"]["Value"])
    except Exception:  # noqa: BLE001
        return None


def main(argv: list[str]) -> int:
    api_key = os.getenv("UPSTOX_API_KEY", "")
    secret = os.getenv("UPSTOX_API_SECRET", "")
    redirect = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8765/callback")
    stage = os.getenv("STAGE", "dev")
    if not api_key:
        print("UPSTOX_API_KEY not set (put it in .env)", file=sys.stderr)
        return 2
    if len(argv) < 2:
        print("Open this URL, log in, then re-run with the `code` from the redirect:\n")
        print(login_url(api_key, redirect))
        return 0
    token = exchange_code(argv[1], api_key, secret, redirect)
    stored = store_token_ssm(token, stage)
    print(f"UPSTOX_ACCESS_TOKEN={token}")
    print(f"(stored in SSM {SSM_PARAM.format(stage=stage)}: {stored})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("../../.env")
    load_dotenv(".env", override=True)
    raise SystemExit(main(sys.argv))

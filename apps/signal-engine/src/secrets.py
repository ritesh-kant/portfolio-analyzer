"""Secrets abstraction — reads from env vars locally, AWS SSM on Lambda.

Usage (called automatically by Settings on first access):

    from .secrets import resolve_secret
    value = resolve_secret("ANTHROPIC_API_KEY")

On local dev the function is a no-op (env vars already set via .env).
On Lambda, AWS_SECRETS_ENABLED=true activates SSM resolution at cold-start
so values are populated into os.environ before Pydantic-settings reads them.
"""

import logging
import os

logger = logging.getLogger(__name__)

# SSM path prefix — mirrors serverless.yml convention
_SSM_PREFIX = "/portfolio-analyzer"

_SECRETS_KEYS = (
    "MONGODB_URI",
    "SIGNAL_ENGINE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "NVIDIA_API_KEY",
)


def bootstrap_secrets(stage: str = "dev") -> None:
    """Pull secrets from SSM into os.environ at Lambda cold-start.

    Call once before Settings() is instantiated:

        bootstrap_secrets(stage=os.getenv("STAGE", "dev"))
        settings = Settings()

    Safe to call multiple times — skips keys already set in the environment.
    """
    if os.getenv("AWS_SECRETS_ENABLED", "").lower() != "true":
        return  # local dev — env vars already loaded via .env

    try:
        import boto3  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("boto3 not installed — skipping SSM secret resolution")
        return

    ssm = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "ap-south-1"))
    names = [
        f"{_SSM_PREFIX}/{stage}/{key}"
        for key in _SECRETS_KEYS
        if not os.getenv(key)  # skip if already injected by serverless.yml
    ]
    if not names:
        return

    try:
        resp = ssm.get_parameters(Names=names, WithDecryption=True)
        for param in resp.get("Parameters", []):
            env_key = param["Name"].rsplit("/", 1)[-1]
            os.environ[env_key] = param["Value"]
            logger.debug("secrets: loaded %s from SSM", env_key)

        missing = [p for p in resp.get("InvalidParameters", [])]
        if missing:
            logger.warning("secrets: SSM parameters not found: %s", missing)
    except Exception as exc:
        logger.error("secrets: SSM resolution failed: %s", exc)

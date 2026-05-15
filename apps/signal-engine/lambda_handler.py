"""AWS Lambda entry point — wraps the FastAPI app with the Mangum ASGI adapter.

Deploy as a container image (see serverless.yml).  The Lambda runtime calls
`handler(event, context)` which Mangum translates into an ASGI request/response
cycle understood by FastAPI.

Environment variables are resolved at cold-start from the Lambda execution
environment (injected by Serverless Framework via SSM references).
"""

from mangum import Mangum

from src.main import app

# lifespan="off" because FastAPI lifespan events (startup/shutdown) don't map
# cleanly onto Lambda's per-invocation model — DB clients are initialised lazily.
handler = Mangum(app, lifespan="off")

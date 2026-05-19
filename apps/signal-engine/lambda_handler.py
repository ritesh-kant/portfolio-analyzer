"""AWS Lambda entry point.

The direct-invocation path (pipelineConsumer → run_pipeline) was removed during
the demolition phase of the signal-engine rebuild. Only the HTTP path remains;
the new pipeline will expose new endpoints from quant/ in Month 3.
"""

import logging

from mangum import Mangum

from src.main import app

logger = logging.getLogger(__name__)

_http_handler = Mangum(app, lifespan="off")


def handler(event: dict, context: object) -> object:
    if "run_id" in event:
        logger.warning(
            "direct invoke received run_id=%s but pipeline is under reconstruction; ignoring",
            event.get("run_id"),
        )
        return {"status": "pipeline_under_reconstruction"}
    return _http_handler(event, context)

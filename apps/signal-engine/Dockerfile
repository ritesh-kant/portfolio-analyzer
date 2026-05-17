FROM public.ecr.aws/lambda/python:3.12-arm64

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR ${LAMBDA_TASK_ROOT}

COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --extra aws --no-hashes -o /tmp/requirements.txt && \
    uv pip install --system --no-cache -r /tmp/requirements.txt

COPY src/ src/
COPY lambda_handler.py .

CMD ["lambda_handler.handler"]

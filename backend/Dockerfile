# The deployed images (see infra/terraform), built from one base as two targets:
#   api     the REST API as a web server (uvicorn) behind the Lambda Web Adapter, which lets a
#           Lambda function URL stream a response as it's written
#   ingest  the scheduled ingest, a plain Lambda handler (fantasy_helper.aws.ingest)
# Build one with `docker build --target api|ingest backend`.
FROM public.ecr.aws/lambda/python:3.12 AS base

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /bin/uv
WORKDIR ${LAMBDA_TASK_ROOT}

# Dependencies first, so a code change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --no-hashes --extra aws -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt

# The repo's backend/ layout: config.py finds data/ two levels above itself.
COPY data ./data
COPY src ./src
RUN echo "${LAMBDA_TASK_ROOT}/src" > "$(python3 -c 'import site; print(site.getsitepackages()[0])')/fantasy_helper.pth" \
    && python3 -c "import fantasy_helper.aws, fantasy_helper.api.app; from fantasy_helper.calendar import load_calendar; from fantasy_helper.config import get_settings; load_calendar(get_settings().season)"

FROM base AS ingest
CMD ["fantasy_helper.aws.ingest"]

# Last, so a build without --target gets the API.
FROM base AS api
# On Lambda the adapter runs as an extension and passes each request to the server on
# AWS_LWA_PORT once the readiness path answers; anywhere else the image is just the server.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/api/health
ENTRYPOINT ["python3", "-m", "uvicorn", "fantasy_helper.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
CMD []

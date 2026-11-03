# The deployed image (see infra/terraform): AWS Lambda's Python base, serving the REST API
# (fantasy_helper.aws.api) unless a function overrides the command (the ingest does).
FROM public.ecr.aws/lambda/python:3.12

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
    && python3 -c "import fantasy_helper.aws; from fantasy_helper.calendar import load_calendar; from fantasy_helper.config import get_settings; load_calendar(get_settings().season)"

CMD ["fantasy_helper.aws.api"]

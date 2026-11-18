# Deploy

How the app is deployed to AWS and Vercel, and how to run it after that. For local setup, see [development.md](development.md).

```
EventBridge Scheduler (hourly) -> ingest (Lambda) --> Postgres (Neon, free plan)
                                                          ^
browser -> Next.js (Vercel) -- /api/* --> API (Lambda function URL)
```

The two functions run two targets of one image (`backend/Dockerfile`): the API as a web server
(uvicorn) behind the [Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter), which
lets a function URL stream a response, and the ingest as a plain Lambda handler. Both read their settings from one JSON
secret, `fantasy-helper/app` (`{"FH_DATABASE_URL": "...", "FH_OPENAI_API_KEY": "...", ...}`), through
`FH_SECRET_ID`. Reads are
public. A Refresh within 2 minutes of the last successful ingest returns that run, and a Postgres
advisory lock keeps a Refresh and the scheduled ingest from overlapping.

**Cost (estimates).** About $1 a month: the Secrets Manager secret ($0.40), image storage and
logs. Lambda stays inside its always-free tier (1M requests and 400,000 GB-seconds a month), and
Vercel Hobby is free. Neon's free plan allows 0.5 GB of storage, 100 CU-hours of compute and 5 GB
of network transfer per month. Transfer is the limit to watch late in the season, since each
context load reads the box-score history (Neon's console shows usage). Set `alert_email` to get
an AWS Budgets email at 80% of $5. The chat's OpenAI usage is billed by OpenAI on top, and the
app caps it at `agent_daily_budget_usd` ($1) over any 24 hours. Measured on the live site, a
question costs $0.0001–0.001. Setting a monthly budget on the OpenAI project is a second backstop.

**You need:** an AWS account, a [Neon](https://neon.com) account and a [Vercel](https://vercel.com)
account; the AWS CLI with a profile (`brew install awscli`, then `aws configure sso` or
`aws configure`; credentials stay in `~/.aws`); and Terraform 1.11+ (`brew tap hashicorp/tap &&
brew install hashicorp/tap/terraform`).

### First deploy

1. **Database.** Create a Neon project in AWS us-east-1 and copy its **direct** connection string,
   not the `-pooler` host: the ingest lock needs a session of its own.
2. **Bootstrap stack:** state bucket, image repository, app secret and GitHub deploy role.
   ```bash
   cd infra/terraform/bootstrap
   terraform init && terraform apply
   ```
3. **Secret value.** In the Secrets Manager console, open `fantasy-helper/app` and store the
   plaintext `{"FH_DATABASE_URL": "postgresql://...?sslmode=require"}`. Or:
   `aws secretsmanager put-secret-value --secret-id fantasy-helper/app --secret-string '{"FH_DATABASE_URL": "..."}'`.
4. **Schema and data.** From `backend/`, before step 6 schedules any ingest: the copy needs an
   empty database. Both commands read the URL from the secret through your AWS profile (add
   `AWS_PROFILE=<name>` for a named profile). An `FH_DATABASE_URL` in `backend/.env` would take
   precedence over the secret, so make sure none is set.
   ```bash
   export FH_SECRET_ID=fantasy-helper/app AWS_REGION=us-east-1
   uv run --extra aws alembic upgrade head
   uv run --extra aws fh-copy-db sqlite:///fantasy.db
   ```
5. **First image.** In the GitHub repo's Settings > Secrets and variables > Actions > Variables, add
   `AWS_DEPLOY_ROLE_ARN` (the bootstrap's `deploy_role_arn` output) and `AWS_REGION`
   (`us-east-1`). Then run Actions > Deploy on `main`. It pushes both images and notes that the
   functions don't exist yet.
6. **Main stack:** both functions, the function URL, the schedule, logs and the budget.
   ```bash
   cd infra/terraform
   cp terraform.tfvars.example terraform.tfvars   # set alert_email
   terraform init -backend-config="bucket=<state_bucket output>" -backend-config="region=us-east-1"
   terraform apply                                # prints api_url
   ```
7. **Check that ESPN answers from AWS** with one ingest now:
   ```bash
   aws lambda invoke --function-name fantasy-helper-ingest /dev/stdout
   aws logs tail /aws/lambda/fantasy-helper-ingest --since 10m   # "ingest N succeeded"
   ```
8. **Frontend.** In Vercel, import the repo, set Root Directory to `frontend`, and add the
   environment variable `API_URL` = the `api_url` output, *before* the first build: Next.js bakes
   rewrites in at build time. Vercel previews every other branch itself, but
   [`frontend/vercel.json`](../frontend/vercel.json) turns off its deploys of `main`: the Deploy
   workflow publishes production instead, after the backend. For that, create a Vercel access token
   (Account Settings > Tokens) and add it to the GitHub repo as the secret `VERCEL_TOKEN`, plus the
   variables `VERCEL_ORG_ID` (the team ID) and `VERCEL_PROJECT_ID` (Project Settings > General).
9. **Verify.** The Vercel URL loads, and after the next full hour
   `aws logs tail /aws/lambda/fantasy-helper-ingest --since 1h` shows the scheduled run.

### After that

- **Deploys:** once CI passes for a push to `main`, Actions > Deploy runs on its own: the backend
  first, then the frontend, so the site never calls an API older than itself. The backend step
  migrates the database (`alembic upgrade head`), moves each function to its new image and checks
  `/api/health` and `/api/health/stream`; if that check fails, both functions go back to the
  images they ran before. Only then does the frontend build with Vercel's production settings and go live;
  if the backend fails, the old frontend stays up. If CI runs finish out of order, only `main`'s
  newest commit deploys. To redeploy without a push, run Actions > Deploy on `main` by hand. The
  deploy role only trusts `main`, so other branches can't deploy. Every push to `main` deploys
  both halves, so even a frontend-only change takes CI plus the backend deploy to go live.
- **Frontend previews:** Vercel still builds every branch other than `main` itself.
- **Infrastructure:** `terraform plan` / `apply` in `infra/terraform`. Deploys never change the
  infrastructure, and Terraform leaves the functions' image alone.
- **Production data locally:** copy it into a fresh SQLite file:
  `FH_DATABASE_URL=sqlite:///prod.db uv run alembic upgrade head && FH_DATABASE_URL=sqlite:///prod.db uv run fh-copy-db '<neon url>'`.
- **Teardown:** `terraform destroy` in `infra/terraform`, then in `bootstrap`. The state bucket
  must be emptied first (it keeps old versions), and the secret stays recoverable for 7 days.
- **Streaming:** the API can send a response as it's written, which the chat needs. Two settings
  must agree, and `api_streaming` in `infra/terraform` sets both: the function URL's invoke mode
  and the Lambda Web Adapter's. `terraform apply` changes them seconds apart, and requests in
  between may fail. The deploy's smoke test reads `/api/health/stream` and, when the URL streams,
  fails unless its 4 events arrive spread over more than a second. To go back, apply with
  `-var api_streaming=false`.
- **Changing the secret:** each function reads it once per cold start, so a new value applies
  after the next deploy, or once Lambda recycles its environments.
- **Turning on the chat:** until it's set up, every question gets "The chat isn't set up yet." Add
  five keys to `fantasy-helper/app`, keeping `FH_DATABASE_URL`: `FH_OPENAI_API_KEY`,
  `FH_AGENT_MODEL` (e.g. `gpt-6-luna`), and that model's prices in USD per million tokens from
  OpenAI's pricing page, `FH_AGENT_INPUT_PRICE`, `FH_AGENT_CACHED_INPUT_PRICE` and
  `FH_AGENT_OUTPUT_PRICE`. In the console: Retrieve secret value > Edit > Key/value, add a row
  for each, then Save. `put-secret-value` replaces the whole secret, so from the CLI write back the
  existing keys too. Then run Actions > Deploy on `main`. The spending limits are Terraform
  variables, `agent_daily_budget_usd` and `agent_visitor_daily_messages`. A turn whose worst case
  costs more than the whole daily budget turns the chat off with a 503 and a log line naming the
  settings to change.
- **Visitor addresses:** the API runs uvicorn with `--no-access-log`. uvicorn takes the client
  address from `X-Forwarded-For` (the adapter connects from 127.0.0.1), so its access log would
  write each visitor's IP to CloudWatch. The chat's spending cap stores only a salted hash.

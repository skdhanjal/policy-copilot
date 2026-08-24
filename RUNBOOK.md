# Deployment Runbook — Policy Copilot on GCP

Real, tested sequence to bring the full stack up from zero. Follow in
order — several steps depend on earlier ones completing first.

## Prerequisites (one-time, per machine)

- `gcloud` CLI installed, authenticated (`gcloud auth login`)
- `gcloud auth application-default login` (separate from above — Terraform needs this)
- Terraform installed
- Docker installed and running
- `cloud-sql-proxy` installed: see infra setup notes
- `postgresql-client` installed (`sudo apt-get install postgresql-client`)

## Step 1 — Export all secrets as Terraform variables

Every `terraform` command in this runbook needs these exported first, in
the SAME terminal session (they don't persist across new terminals):

```bash
cd infra
export $(grep TF_VAR_db_password ../.env)
export TF_VAR_openai_api_key=$(grep OPENAI_API_KEY ../.env | cut -d= -f2)
export TF_VAR_gemini_api_key=$(grep GEMINI_API_KEY ../.env | cut -d= -f2)
export TF_VAR_litellm_master_key=$(grep LITELLM_MASTER_KEY ../.env | cut -d= -f2)
export TF_VAR_gateway_app_key=$(grep GATEWAY_APP_KEY ../.env | cut -d= -f2)
```

## Step 2 — First apply (creates infra, images don't exist yet)

```bash
terraform apply -auto-approve
```

**Expected result:** Cloud SQL, Redis, VPC connector, Secret Manager,
Artifact Registry repo all succeed. LiteLLM/API Cloud Run services and
Jobs will FAIL here — their images don't exist in the fresh registry
yet. This is expected; continue to Step 3.

## Step 3 — Build and push both Docker images

```bash
gcloud auth configure-docker asia-south1-docker.pkg.dev

# LiteLLM image (config baked in, from infra/)
docker build -t asia-south1-docker.pkg.dev/sentinel-desk-dev/policy-copilot/litellm:v1 -f litellm.Dockerfile .
docker push asia-south1-docker.pkg.dev/sentinel-desk-dev/policy-copilot/litellm:v1

# API image (from project root -- uses main Dockerfile with CPU-only torch fix)
cd ..
docker build -t asia-south1-docker.pkg.dev/sentinel-desk-dev/policy-copilot/api:v1 .
docker push asia-south1-docker.pkg.dev/sentinel-desk-dev/policy-copilot/api:v1
cd infra
```

**Note:** the API image build takes ~3-12 minutes depending on Docker
cache state. Run `docker system df` before this if disk space is a
concern (see Troubleshooting below).

## Step 4 — Second apply (deploys the actual services)

```bash
terraform apply -auto-approve
```

**Expected result:** LiteLLM service, API service, ingest job, and
schema-setup job all created successfully now that images exist.

## Step 5 — Apply the database schema

The schema-setup Cloud Run Job was created in Step 4 but not executed
yet. Run it:

```bash
gcloud run jobs execute policy-copilot-schema-setup --region=asia-south1 --project=sentinel-desk-dev --wait
```

**Verify it worked** (don't just trust "Done"):

```bash
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=policy-copilot-schema-setup" \
  --project=sentinel-desk-dev --limit=15 --freshness=2m --format="value(textPayload)"
```

Should show `CREATE TABLE` x3, `CREATE EXTENSION` x2, `INSERT 0 3`, and
`Container called exit(0)`. Re-running this job will correctly FAIL
(tables already exist) -- that's expected, not a bug.

## Step 6 — Run ingest LOCALLY into the cloud database

**Important:** the Cloud Run ingest Job exists but does NOT work --
eCFR blocks Cloud Run's outbound IP range (403 Forbidden, confirmed via
diagnostic testing, see DECISIONS.md D49). Ingest must run from your
local machine instead, tunneled into the cloud database.

> **WARNING -- easy to forget:** Steps 6a-6e open a real, live
> security exposure (public database, password-only protection) for the
> DURATION of ingest. This has actually happened before in real use --
> the team moved on to other work after ingest finished and did NOT
> revert 6e, leaving the database publicly exposed unnoticed. Set a
> reminder, or run `grep ipv4_enabled infra/main.tf` immediately after
> ingest completes as a hard checkpoint before doing anything else.

**6a. Temporarily re-enable Cloud SQL public IP** (local machine can't
reach the private IP -- it's not inside the VPC):

```bash
sed -i 's/ipv4_enabled    = false/ipv4_enabled    = true/' main.tf
terraform apply -auto-approve
```

**6b. Open the proxy tunnel** (leave running in this terminal, or background with `&`):

```bash
cd ..
cloud-sql-proxy sentinel-desk-dev:asia-south1:policy-copilot-db --port 5436 &
sleep 5
```

**6c. Run ingest through the tunnel:**

```bash
POSTGRES_DSN="postgresql://copilot:$TF_VAR_db_password@localhost:5436/copilot" python3 -m app.rag.ingest
```

Takes real time (several minutes) -- fetches from eCFR, embeds locally,
writes through the tunnel.

**6d. Verify real data landed:**

```bash
PGPASSWORD=$TF_VAR_db_password psql -h localhost -p 5436 -U copilot -d copilot -c "SELECT count(*) FROM chunk;"
```

Should show ~1200 rows.

**6e. Close the tunnel, switch Cloud SQL back to private:**

```bash
pkill -f cloud-sql-proxy
cd infra
sed -i 's/ipv4_enabled    = true/ipv4_enabled    = false/' main.tf
terraform apply -auto-approve
```

## Step 7 — Verify end to end

Both services are private by default except LiteLLM (see Known Trade-offs
below). To test the private API service, temporarily grant public access:

```bash
cat >> main.tf <<'EOF'

resource "google_cloud_run_v2_service_iam_member" "api_public_test" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
EOF
terraform apply -auto-approve
```

```bash
API_URL=$(gcloud run services describe policy-copilot-api --region=asia-south1 --project=sentinel-desk-dev --format="value(status.url)")
curl -s -X POST $API_URL/query -H "Content-Type: application/json" -d '{"question": "what does 314.3 require"}' | python3 -m json.tool
```

Should return a real, grounded answer with `grounding_failed: false`.

**Then lock back down:**

```bash
grep -n "api_public_test" main.tf
# note the line numbers, delete that resource block, then:
terraform apply -auto-approve
```

## Known trade-offs (deliberate, documented)

- **LiteLLM is permanently PUBLIC** (`allUsers` + `run.invoker`),
  protected only by `LITELLM_MASTER_KEY`. Root cause: Cloud Run private
  services require a Google identity token for service-to-service
  calls, which our plain HTTP client (`generate.py`) doesn't send.
  Proper fix (fetching identity tokens) deferred -- see DECISIONS.md D50.
- **Ingest cannot run in Cloud Run** -- eCFR blocks cloud-hosted
  requests. Must run locally via the proxy-tunnel method (Step 6).
- **LiteLLM has no persistent database** -- no spend tracking, no
  virtual keys beyond the master key. API service uses
  `LITELLM_MASTER_KEY` directly, not a scoped virtual key.

## Full teardown

```bash
cd infra
terraform destroy -auto-approve
```

**Known issue:** may fail with
`FLOW_SN_DC_RESOURCE_PREVENTING_DELETE_CONNECTION` due to Memorystore's
own auto-created VPC peering (separate from the one Terraform tracks).
If stuck: go to GCP Console → VPC Network → VPC connectivity → VPC
network peering, manually delete `servicenetworking-googleapis-com`,
then re-run `terraform destroy`. See DECISIONS.md D45 for full detail.

## What terraform apply alone does NOT restore

- Database schema and data (Steps 5-6 must be redone)
- Docker images (Step 3 must be redone -- Artifact Registry is destroyed too)

See DECISIONS.md D44 for the original version of this gap analysis.

## Troubleshooting quick reference

- **Docker Desktop stuck at "engine starting":** try `wsl --shutdown`
  first; if that doesn't work, a full Windows restart usually clears it
  (D47).
- **Disk space issues:** `docker system df`, then `docker system prune -f`
  and `docker builder prune -f`. Check `nvidia`/`torch` aren't bloating
  the API image (should be ~2.2GB with CPU-only torch, not ~8.7GB).
- **"relation does not exist" errors:** schema wasn't applied (Step 5
  skipped or failed silently -- verify with the logging command shown).
- **401/403 on /query:** check GATEWAY_APP_KEY env var on the API
  service actually points to `litellm_master_key`'s secret, not
  `gateway_app_key`'s (see D50).
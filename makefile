SHELL := /bin/bash

# ==============================================================================
# Configuration Variables
# ==============================================================================
REGION ?= $(AWS_REGION)
STACK ?= ProofbriefStack
BACKEND_ENV ?= backend/.env
OUTPUTS_JSON ?= cdk-outputs.json
GITHUB_SECRET_NAME ?= github/proofbrief
GITHUB_SECRET_ARN ?=
GITHUB_TOKEN ?=
BEDROCK_MODEL_ID ?= anthropic.claude-3-haiku-20240307-v1:0
DB_NAME ?= postgres
DELETE_SECRET ?= 0
DELETE_BUCKET ?= 0

# ==============================================================================
# Embedded Python Scripts
# Using 'define' is the most robust way to handle multiline scripts in Make.
# ==============================================================================

# Script for the 'gen-env' target
define GEN_ENV_SCRIPT
import json, os, pathlib, sys, subprocess
stack = os.environ.get("STACK", "ProofbriefStack")
outf = os.environ.get("OUTPUTS_JSON", "cdk-outputs.json")
envp = os.environ.get("BACKEND_ENV", "backend/.env")
region = os.environ.get("REGION") or os.environ.get("AWS_REGION") or os.environ.get("CDK_DEFAULT_REGION") or "us-east-1"
bedrock = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
db_name = os.environ.get("DB_NAME", "postgres")
secret_name = os.environ.get("GITHUB_SECRET_NAME", "github/proofbrief").strip()
secret_arn = os.environ.get("GITHUB_SECRET_ARN", "").strip()
token = os.environ.get("GITHUB_TOKEN", "").strip()
with open(outf, "r") as f:
    data = json.load(f)
if stack not in data:
    sys.stderr.write(f"Missing stack outputs for {stack}\n"); sys.exit(2)
o = data[stack]
bucket = o["S3BucketName"]; cluster_arn = o["DatabaseClusterARN"]; db_secret_arn = o["DatabaseSecretARN"]
def aws(args):
    return subprocess.run(["aws", *args, "--region", region], capture_output=True, text=True)
if not secret_arn and token:
    d = aws(["secretsmanager", "describe-secret", "--secret-id", secret_name, "--query", "ARN", "--output", "text"])
    if d.returncode == 0 and d.stdout.strip():
        secret_arn = d.stdout.strip()
    else:
        c = aws(["secretsmanager", "create-secret", "--name", secret_name, "--secret-string", json.dumps({"GITHUB_TOKEN": token}), "--query", "ARN", "--output", "text"])
        if c.returncode != 0:
            sys.stderr.write(c.stderr); sys.exit(3)
        secret_arn = c.stdout.strip()
pathlib.Path("backend").mkdir(exist_ok=True)
pathlib.Path(envp).write_text("\n".join([
    f"AWS_REGION={region}",
    f"S3_BUCKET_NAME={bucket}",
    f"DB_CLUSTER_ARN={cluster_arn}",
    f"DB_SECRET_ARN={db_secret_arn}",
    f"DB_NAME={db_name}",
    f"BEDROCK_MODEL_ID={bedrock}",
    f"GITHUB_SECRET_ARN={secret_arn}",
]) + "\n")
print(f"Wrote {envp}")
endef
export GEN_ENV_SCRIPT

# Script for the 'db-check' target
define DB_CHECK_SCRIPT
from shared.utils import get_db_engine
e = get_db_engine()
with e.connect() as c:
    v = c.exec_driver_sql("select 1").scalar()
print("DB OK:", v==1)
endef
export DB_CHECK_SCRIPT

# Script for the 'get-bucket' target
define GET_BUCKET_SCRIPT
import json, os, subprocess
outputs = "cdk-outputs.json"
stack = os.environ.get("STACK", "ProofbriefStack")
region = os.environ.get("REGION") or os.environ.get("AWS_REGION") or "us-east-1"
name = ""
try:
    with open(outputs, "r") as f:
        d = json.load(f)
    name = d.get(stack, {}).get("S3BucketName", "")
except Exception:
    pass
if not name:
    r = subprocess.run(["aws", "cloudformation", "describe-stacks", "--stack-name", stack, "--region", region, "--query", "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue|[0]", "--output", "text"], capture_output=True, text=True)
    if r.returncode == 0:
        name = r.stdout.strip()
print(name)
endef
export GET_BUCKET_SCRIPT

# ==============================================================================
# Targets
# ==============================================================================
.PHONY: all venv deploy cdk-deploy gen-env alembic-up db-check seed teardown get-bucket clean-bucket post-destroy-delete-bucket

all: deploy

venv:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -U pip && pip install -r requirements.txt

deploy: cdk-deploy gen-env alembic-up db-check

cdk-deploy:
	cd infra && . ../.venv/bin/activate && \
	if [ -n "$(REGION)" ]; then export CDK_DEFAULT_REGION="$(REGION)"; fi; \
	cdk deploy --require-approval never --outputs-file ../$(OUTPUTS_JSON)

gen-env:
	. .venv/bin/activate && \
	STACK="$(STACK)" OUTPUTS_JSON="$(OUTPUTS_JSON)" BACKEND_ENV="$(BACKEND_ENV)" \
	REGION="$(REGION)" AWS_REGION="$(AWS_REGION)" CDK_DEFAULT_REGION="$(CDK_DEFAULT_REGION)" \
	BEDROCK_MODEL_ID="$(BEDROCK_MODEL_ID)" DB_NAME="$(DB_NAME)" \
	GITHUB_SECRET_NAME="$(GITHUB_SECRET_NAME)" GITHUB_SECRET_ARN="$(GITHUB_SECRET_ARN)" \
	GITHUB_TOKEN="$(GITHUB_TOKEN)" python3 -c "$$GEN_ENV_SCRIPT"

alembic-up:
	cd backend && . ../.venv/bin/activate && alembic upgrade head

db-check:
	# These commands now run in the same shell instance
	cd backend
	python3 - <<'PY'
	import sys
	from shared.utils import get_db_engine

	try:
	    e = get_db_engine()
	    with e.connect() as c:
	        v = c.exec_driver_sql("select 1").scalar()
	    is_ok = (v == 1)
	    if is_ok:
	        print("DB OK:", is_ok)
	    else:
	        print("DB check failed: query did not return 1", file=sys.stderr)
	    sys.exit(0 if is_ok else 1)
	except Exception as err:
	    print(f"DB connection error: {err}", file=sys.stderr)
	    sys.exit(1)
	PY


seed:
	. .venv/bin/activate && python scripts/seed_and_start.py \
	--email "$(EMAIL)" --candidate "$(CANDIDATE)" --job-title "$(JOB_TITLE)" \
	--resume "$(RESUME)" --jd "$(JD)" --stack "$(STACK)" \
	$(if $(API_URL),--api-url "$(API_URL)",)

get-bucket:
	@. .venv/bin/activate; python3 -c "$$GET_BUCKET_SCRIPT"

clean-bucket:
	@BKT=$$(make -s get-bucket REGION=$(REGION) STACK=$(STACK)); \
	if [ -n "$$BKT" ]; then \
	  echo "Emptying s3://$$BKT ..."; \
	  aws s3 rm s3://$$BKT --recursive --region $(REGION) >/dev/null 2>&1 || true; \
	else \
	  echo "No bucket found to clean."; \
	fi

post-destroy-delete-bucket:
	@if [ "$(DELETE_BUCKET)" = "1" ]; then \
	  BKT=$$(make -s get-bucket REGION=$(REGION) STACK=$(STACK)); \
	  if [ -n "$$BKT" ]; then \
	    echo "Deleting bucket s3://$$BKT ..."; \
	    aws s3 rb s3://$$BKT --force --region $(REGION) || true; \
	  else \
	    echo "No bucket name available for deletion."; \
	  fi; \
	fi

teardown: clean-bucket
	cd infra && . ../.venv/bin/activate && if [ -n "$(REGION)" ]; then export CDK_DEFAULT_REGION="$(REGION)"; fi; cdk destroy --force $(STACK)
	rm -f $(BACKEND_ENV) $(OUTPUTS_JSON)
	@if [ "$(DELETE_SECRET)" = "1" ]; then \
		aws secretsmanager delete-secret --secret-id $(GITHUB_SECRET_NAME) --force-delete-without-recovery --region $(REGION) || true; \
	fi
	$(MAKE) post-destroy-delete-bucket REGION=$(REGION) STACK=$(STACK)
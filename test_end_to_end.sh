#!/usr/bin/env bash
set -euo pipefail

# ==========================================
# Config (override via env)
# ==========================================
STACK_NAME="${STACK_NAME:-ProofbriefStack}"
AWS_REGION="${AWS_REGION:-us-east-1}"

# If you already have your API base like "https://xxx.execute-api.us-east-1.amazonaws.com/prod"
# you can set it here to skip discovery:
BASE_URL="${BASE_URL:-}"

# Files to upload
RESUME_FILE="${RESUME_FILE:-./samples/SWE_Resume.pdf}"
JD_FILE="${JD_FILE:-./samples/jd.txt}"

# Optional Cognito ID token:
#   export ID_TOKEN="eyJraWQiOi...<snip>..."
# Optional Cognito ID token
ID_TOKEN="${ID_TOKEN:-}"

# Always define this (empty array is fine)
AUTH_ARGS=()
if [[ -n "$ID_TOKEN" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${ID_TOKEN}")
  echo "✅ Using Cognito ID token for auth"
else
  echo "▶ No ID_TOKEN provided. Assuming API allows unauthenticated access (dev)."
fi


# Polling config
MAX_POLL="${MAX_POLL:-60}"   # 60 * 5s = 5 minutes
SLEEP_SECS="${SLEEP_SECS:-5}"

# ==========================================
# Helpers
# ==========================================
fail() { echo "❌ $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "Missing dependency: $1"; }

say() { echo "▶ $*"; }
ok()  { echo "✅ $*"; }

json_get() { # jq wrapper that fails nicely if key missing
  local key="$1"
  jq -er "$key" 2>/dev/null || return 1
}

normalize_url() {
  # Remove trailing slash
  local url="${1%/}"
  # If it ends with /briefs, strip that segment
  if [[ "$url" == */briefs ]]; then
    url="${url%/briefs}"
  fi
  echo "$url"
}

discover_base_url() {
  say "Discovering API Gateway URL from CloudFormation outputs (stack=$STACK_NAME, region=$AWS_REGION) ..."
  # Try multiple output keys people commonly use
  local query
  # Try ApiGatewayUrl, ProofBriefApiEndpoint, ApiUrl (any that exist)
  query="Stacks[0].Outputs[?OutputKey=='ApiGatewayUrl'].OutputValue | [0]"
  local url
  url="$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "$query" \
    --output text 2>/dev/null || true)"
  if [[ -z "$url" || "$url" == "None" ]]; then
    query="Stacks[0].Outputs[?OutputKey=='ProofBriefApiEndpoint'].OutputValue | [0]"
    url="$(aws cloudformation describe-stacks \
      --stack-name "$STACK_NAME" \
      --region "$AWS_REGION" \
      --query "$query" \
      --output text 2>/dev/null || true)"
  fi
  if [[ -z "$url" || "$url" == "None" ]]; then
    query="Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue | [0]"
    url="$(aws cloudformation describe-stacks \
      --stack-name "$STACK_NAME" \
      --region "$AWS_REGION" \
      --query "$query" \
      --output text 2>/dev/null || true)"
  fi
  [[ -z "$url" || "$url" == "None" ]] && fail "Could not find an API URL in stack outputs. Add an output like 'ApiGatewayUrl'."

  # Normalize (strip trailing / and any accidental /briefs that some stacks output)
  url="$(normalize_url "$url")"

  # Some stacks output the full resource path; make sure we return the stage root only.
  # If the path has more than 3 slashes after domain (e.g., /prod/briefs), cut to /prod
  # Example base: https://xxx.execute-api.us-east-1.amazonaws.com/prod/briefs
  # We want:      https://xxx.execute-api.us-east-1.amazonaws.com/prod
  if [[ "$url" =~ ^https://[^/]+/[^/]+/[^/]+(/.*)$ ]]; then
    # Already includes stage; if there are extra segments, trim to first segment after domain
    # Split by '/' and keep first 4 components
    IFS='/' read -r -a parts <<< "$url"
    # parts[0]="https:" parts[1]="" parts[2]="xxx.execute-api..." parts[3]="prod" parts[4...] = extra
    if [[ ${#parts[@]} -gt 4 ]]; then
      url="${parts[0]}//${parts[2]}/${parts[3]}"
    fi
  fi

  echo "$url"
}

# ==========================================
# Preconditions
# ==========================================
need aws
need curl
need jq

# Create sample files dir if needed
mkdir -p "$(dirname "$RESUME_FILE")" "$(dirname "$JD_FILE")"

# Ensure JD exists (create a tiny default if missing)
if [[ ! -f "$JD_FILE" ]]; then
  say "JD file not found; creating a minimal sample at $JD_FILE"
  cat > "$JD_FILE" <<'EOF'
Senior Software Engineer
Must-have: Python, AWS (Lambda, S3), SQL, GitHub
Nice-to-have: React, TypeScript
EOF
fi

# Ensure resume exists (don’t auto-generate PDFs)
[[ -f "$RESUME_FILE" ]] || fail "Resume PDF not found at: $RESUME_FILE"

# Discover BASE_URL if not provided
if [[ -z "$BASE_URL" ]]; then
  BASE_URL="$(discover_base_url)"
fi
ok "BASE_URL = $BASE_URL"

AUTH_ARGS=()
if [[ -n "$ID_TOKEN" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${ID_TOKEN}")
  ok "Using Cognito ID token for auth"
else
  say "No ID_TOKEN provided. Assuming API allows unauthenticated access (dev)."
fi

# ==========================================
# 1) Create brief: POST /briefs
# ==========================================
say "POST /briefs"
CREATE_RES=$(
  curl -sS -X POST "${BASE_URL}/briefs" \
    -H "Content-Type: application/json" \
    "${AUTH_ARGS[@]}" \
    -d '{"candidate":{"fullName":"Test Candidate"},"job":{"title":"Test Role"}}'
)
echo "$CREATE_RES" | jq .

if ! BRIEF_ID=$(echo "$CREATE_RES" | json_get '.briefId'); then
  fail "Failed to create brief (no briefId). Check the response above."
fi
RESUME_PUT=$(echo "$CREATE_RES" | json_get '.uploads.resume.putUrl' || true)
JD_PUT=$(echo "$CREATE_RES" | json_get '.uploads.jd.putUrl' || true)
[[ -z "$RESUME_PUT" || -z "$JD_PUT" ]] && fail "Missing presigned URLs in response."

ok "Brief: $BRIEF_ID"
ok "Got presigned PUT URLs."

# ==========================================
# 2) Upload resume (PDF)
# ==========================================
say "Uploading resume..."
curl -sS -X PUT "$RESUME_PUT" -H "Content-Type: application/pdf" --upload-file "$RESUME_FILE" >/dev/null
ok "Resume uploaded."

# ==========================================
# 3) Upload JD (text)
# ==========================================
say "Uploading JD..."
curl -sS -X PUT "$JD_PUT" -H "Content-Type: text/plain" --upload-file "$JD_FILE" >/dev/null
ok "JD uploaded."

# ==========================================
# 4) Kick off pipeline: PUT /briefs/{id}/start
# ==========================================
say "PUT /briefs/$BRIEF_ID/start"
curl -sS -X PUT "${BASE_URL}/briefs/${BRIEF_ID}/start" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  | jq .
ok "Pipeline started."

# ==========================================
# 5) Poll for result: GET /briefs/{id}
# ==========================================
say "Polling GET /briefs/$BRIEF_ID ..."
for ((i=1; i<=MAX_POLL; i++)); do
  STATUS_JSON=$(curl -sS "${BASE_URL}/briefs/${BRIEF_ID}" "${AUTH_ARGS[@]}")
  STATUS=$(echo "$STATUS_JSON" | jq -r '.status // empty')
  echo "[$i/${MAX_POLL}] status=${STATUS:-unknown}"
  if [[ "$STATUS" == "DONE" ]]; then
    ok "DONE. Final payload:"
    echo "$STATUS_JSON" | jq .
    exit 0
  fi
  sleep "$SLEEP_SECS"
done

fail "Timed out waiting for DONE"

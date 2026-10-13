#!/usr/bin/env bash
set -euo pipefail

INSTANCE="${CPI_P11_GCP_INSTANCE:-kalsh3-cpi-p11-20261014}"
ZONE="${CPI_P11_GCP_ZONE:-us-east4-b}"
MACHINE_TYPE="${CPI_P11_GCP_MACHINE_TYPE:-e2-small}"
PROJECT="${CPI_P11_GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
UV_VERSION="0.12.23"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "BLOCKER: gcloud CLI is required"
  exit 2
fi

if [ -z "$PROJECT" ] || [ "$PROJECT" = "(unset)" ]; then
  echo "BLOCKER: no Google Cloud project is configured"
  echo "Set CPI_P11_GCP_PROJECT or run: gcloud config set project PROJECT_ID"
  exit 2
fi

ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -n1)"
if [ -z "$ACCOUNT" ]; then
  echo "BLOCKER: no active gcloud account"
  exit 2
fi

cd "$REPO"

if [ "$(git branch --show-current)" != "main" ]; then
  echo "BLOCKER: provision only from canonical main"
  exit 2
fi

git fetch origin

HEAD="$(git rev-parse HEAD)"
ORIGIN_MAIN="$(git rev-parse origin/main)"
if [ "$HEAD" != "$ORIGIN_MAIN" ]; then
  echo "BLOCKER: local main must exactly equal origin/main"
  echo "HEAD=$HEAD"
  echo "origin/main=$ORIGIN_MAIN"
  exit 2
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "BLOCKER: canonical checkout must be clean"
  git status --short
  exit 2
fi

echo "=== CPI P11 GCE PROVISION PLAN ==="
echo "project=$PROJECT"
echo "zone=$ZONE"
echo "instance=$INSTANCE"
echo "machine_type=$MACHINE_TYPE"
echo "runtime_head=$HEAD"
echo "active_account=$ACCOUNT"

gcloud services enable compute.googleapis.com --project="$PROJECT"

if gcloud compute instances describe "$INSTANCE"   --project="$PROJECT"   --zone="$ZONE" >/dev/null 2>&1
then
  echo "BLOCKER: instance already exists: $INSTANCE"
  echo "Refusing to reuse an unverified VM."
  exit 2
fi

gcloud compute instances create "$INSTANCE"   --project="$PROJECT"   --zone="$ZONE"   --machine-type="$MACHINE_TYPE"   --image-family=ubuntu-2404-lts-amd64   --image-project=ubuntu-os-cloud   --boot-disk-type=pd-balanced   --boot-disk-size=20GB   --provisioning-model=STANDARD   --maintenance-policy=MIGRATE   --restart-on-failure   --shielded-secure-boot   --deletion-protection   --labels=purpose=cpi-p11,experiment=cpi-e1-p11

echo
echo "=== WAIT FOR SSH ==="
SSH_READY=0
for attempt in $(seq 1 30); do
  if gcloud compute ssh "$INSTANCE"     --project="$PROJECT"     --zone="$ZONE"     --command='echo CPI_P11_VM_SSH_READY'     --quiet
  then
    SSH_READY=1
    break
  fi
  echo "SSH attempt $attempt/30 not ready; sleeping 10s"
  sleep 10
done

if [ "$SSH_READY" != "1" ]; then
  echo "BLOCKER: VM did not become SSH-ready"
  exit 2
fi

BOOTSTRAP="$(mktemp)"
trap 'rm -f "$BOOTSTRAP"' EXIT

cat > "$BOOTSTRAP" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

RUNTIME_HEAD="${1:?runtime head required}"
UV_VERSION="${2:?uv version required}"
REPO="$HOME/kalsh3_p11_cloud"
UV_VENV="$HOME/cpi-p11-uv"

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y   ca-certificates   git   python3   python3-venv

if [ -e "$REPO" ] && [ ! -d "$REPO/.git" ]; then
  echo "BLOCKER: cloud repo path exists but is not a git repository"
  exit 2
fi

if [ ! -d "$REPO/.git" ]; then
  git clone https://github.com/kensgithubaccount/kalsh3.git "$REPO"
fi

cd "$REPO"
git fetch origin
git switch main
git pull --ff-only

if [ "$(git rev-parse HEAD)" != "$RUNTIME_HEAD" ]; then
  echo "BLOCKER: cloud main does not equal requested runtime head"
  echo "requested=$RUNTIME_HEAD"
  echo "actual=$(git rev-parse HEAD)"
  exit 2
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "BLOCKER: cloud canonical checkout is not clean"
  git status --short
  exit 2
fi

if [ ! -x "$UV_VENV/bin/uv" ]; then
  python3 -m venv "$UV_VENV"
  "$UV_VENV/bin/pip" install --disable-pip-version-check "uv==$UV_VERSION"
fi

if [ "$("$UV_VENV/bin/uv" --version | awk '{print $2}')" != "$UV_VERSION" ]; then
  echo "BLOCKER: unexpected uv version"
  "$UV_VENV/bin/uv" --version
  exit 2
fi

export PATH="$UV_VENV/bin:$PATH"

bash scripts/arm_cpi_e1_p11_systemd.sh
EOF
chmod 700 "$BOOTSTRAP"

gcloud compute scp "$BOOTSTRAP" "$INSTANCE:/tmp/cpi_p11_bootstrap.sh"   --project="$PROJECT"   --zone="$ZONE"   --quiet

gcloud compute ssh "$INSTANCE"   --project="$PROJECT"   --zone="$ZONE"   --command="bash /tmp/cpi_p11_bootstrap.sh '$HEAD' '$UV_VERSION'"   --quiet

echo
echo "=== REMOTE ARMING RECEIPT ==="
gcloud compute ssh "$INSTANCE"   --project="$PROJECT"   --zone="$ZONE"   --command='cat ~/cpi_p11_vm_scheduler_20261014/arming_receipt.txt'   --quiet

echo
echo "=== REMOTE TIMER STATUS ==="
gcloud compute ssh "$INSTANCE"   --project="$PROJECT"   --zone="$ZONE"   --command="systemctl list-timers --all 'kalsh3-cpi-p11-*' --no-pager"   --quiet

echo
echo "=== VM IDENTITY ==="
gcloud compute instances describe "$INSTANCE"   --project="$PROJECT"   --zone="$ZONE"   --format='yaml(name,status,zone,machineType,networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP,scheduling.provisioningModel,scheduling.onHostMaintenance,scheduling.automaticRestart,deletionProtection)'

echo
echo "CPI P11 GCE VM IS PROVISIONED AND ARMED."
echo "Do not remove the Mac LaunchAgents until this output has been independently reviewed."

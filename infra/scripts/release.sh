#!/usr/bin/env bash
# Package the current HEAD as a release tarball and upload to the artifacts
# bucket. Updates s3://.../latest/bull-call.tar.gz to point to this release.
#
# Usage:  infra/scripts/release.sh <version> [dev|live]
# Examples:
#   infra/scripts/release.sh v0.1.0         # upload to dev
#   infra/scripts/release.sh v0.1.0 live    # upload to live
#
# Requires: a clean git working tree, AWS_PROFILE configured (default: busyweb).
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <version> [dev|live]" >&2
    exit 2
fi

VERSION="$1"
ENV="${2:-dev}"
PROFILE="${AWS_PROFILE:-busyweb}"
REGION="${AWS_REGION:-us-east-1}"

if [[ "$ENV" != "dev" && "$ENV" != "live" ]]; then
    echo "ENV must be 'dev' or 'live' (got: $ENV)" >&2
    exit 2
fi

if ! [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?$ ]]; then
    echo "version must be semver (e.g. v0.1.0, v1.2.3-rc1); got: $VERSION" >&2
    exit 2
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "working tree is dirty; commit or stash before releasing" >&2
    git status --short
    exit 2
fi

ACCOUNT="$(aws --profile "$PROFILE" sts get-caller-identity --query Account --output text)"
BUCKET="bull-call-${ENV}-artifacts-${ACCOUNT}"

if ! aws --profile "$PROFILE" --region "$REGION" s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "artifacts bucket ${BUCKET} not found; deploy data stack first:" >&2
    echo "  infra/scripts/deploy.sh deploy ${ENV}" >&2
    exit 3
fi

TAR=$(mktemp -t "bull-call.${VERSION}.XXXXXX").tar.gz
trap "rm -f $TAR" EXIT
git archive --format=tar.gz -o "$TAR" HEAD
SIZE=$(du -h "$TAR" | awk '{print $1}')
SHA=$(shasum -a 256 "$TAR" | awk '{print $1}')
echo "  packaged HEAD ($(git rev-parse --short HEAD)) -> $TAR  ($SIZE)"
echo "  sha256: $SHA"

aws --profile "$PROFILE" --region "$REGION" s3 cp "$TAR" \
    "s3://${BUCKET}/releases/${VERSION}/bull-call.tar.gz" \
    --metadata "git-sha=$(git rev-parse HEAD),sha256=${SHA}"

aws --profile "$PROFILE" --region "$REGION" s3 cp "$TAR" \
    "s3://${BUCKET}/latest/bull-call.tar.gz" \
    --metadata "release-version=${VERSION},git-sha=$(git rev-parse HEAD),sha256=${SHA}"

echo
echo "released ${VERSION} to ${BUCKET}"
echo "  s3://${BUCKET}/releases/${VERSION}/bull-call.tar.gz"
echo "  s3://${BUCKET}/latest/bull-call.tar.gz  -> ${VERSION}"
echo
echo "deploy with:"
echo "  infra/scripts/deploy.sh deploy ${ENV}              # uses 'latest'"
echo "  AWS_VERSION=${VERSION} infra/scripts/deploy.sh deploy ${ENV}  # pin"

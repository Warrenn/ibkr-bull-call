#!/usr/bin/env bash
# Deploy the bull-call AWS stacks in order: data → network → compute.
#
# Usage:
#   infra/scripts/deploy.sh validate [dev|live]
#   infra/scripts/deploy.sh deploy   [dev|live]
#   infra/scripts/deploy.sh status   [dev|live]
#   infra/scripts/deploy.sh destroy  [dev|live]
#
# Env: AWS_PROFILE (default: busyweb), AWS_REGION (default: us-east-1)
set -euo pipefail

ACTION="${1:-deploy}"
ENV="${2:-dev}"
PROFILE="${AWS_PROFILE:-busyweb}"
REGION="${AWS_REGION:-us-east-1}"

if [[ "$ENV" != "dev" && "$ENV" != "live" ]]; then
    echo "ENV must be 'dev' or 'live' (got: $ENV)" >&2
    exit 2
fi

INFRA_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATES_DIR="${INFRA_DIR}/cloudformation"

stacks=(data network compute)

aws_cmd() {
    aws --profile "$PROFILE" --region "$REGION" "$@"
}

stack_name() {
    echo "bull-call-${1}-${ENV}"
}

# ---- Resource names ------------------------------------------------------
# These mirror the values in cloudformation/data.yaml. Centralised here so
# the destroy-time UX notice stays in sync with the deploy logic — if the
# template ever renames the table/bucket, update these helpers and the
# operator-facing message updates automatically.

state_table_name() {
    echo "bull-call-${ENV}-state"
}

artifacts_bucket_pattern() {
    # The bucket name interpolates ``AWS::AccountId`` in CloudFormation;
    # we don't have that handy in shell without an extra API call, so use
    # the literal pattern the operator can substitute or `aws sts` into.
    echo "bull-call-${ENV}-artifacts-<account-id>"
}

validate_one() {
    local tpl="$1"
    echo "→ validating ${tpl}"
    aws_cmd cloudformation validate-template --template-body "file://${TEMPLATES_DIR}/${tpl}.yaml" >/dev/null
}

deploy_one() {
    local tpl="$1"
    local name; name="$(stack_name "$tpl")"
    echo "→ deploying ${name}"
    local -a params=( "Env=${ENV}" )
    if [[ "$tpl" == "compute" ]] && [[ -n "${AWS_VERSION:-}" ]]; then
        params+=( "Version=${AWS_VERSION}" )
    fi
    aws_cmd cloudformation deploy \
        --stack-name "$name" \
        --template-file "${TEMPLATES_DIR}/${tpl}.yaml" \
        --parameter-overrides "${params[@]}" \
        --capabilities CAPABILITY_IAM \
        --no-fail-on-empty-changeset
}

status_one() {
    local tpl="$1"
    local name; name="$(stack_name "$tpl")"
    aws_cmd cloudformation describe-stacks --stack-name "$name" \
        --query "Stacks[0].{Name:StackName,Status:StackStatus,Updated:LastUpdatedTime}" \
        --output table || echo "  (stack ${name} not found)"
}

destroy_one() {
    local tpl="$1"
    local name; name="$(stack_name "$tpl")"
    echo "→ destroying ${name}"
    aws_cmd cloudformation delete-stack --stack-name "$name"
    aws_cmd cloudformation wait stack-delete-complete --stack-name "$name"
}

case "$ACTION" in
    validate)
        for s in "${stacks[@]}"; do validate_one "$s"; done
        echo "all templates valid"
        ;;
    deploy)
        for s in "${stacks[@]}"; do validate_one "$s"; done
        for s in "${stacks[@]}"; do deploy_one "$s"; done
        echo
        echo "next step: seed credentials (one-time, then daily 2FA on your phone):"
        echo "  infra/scripts/seed-secrets.sh ${ENV}"
        ;;
    status)
        for s in "${stacks[@]}"; do status_one "$s"; done
        ;;
    destroy)
        # Reverse order: compute → network → data
        for s in compute network data; do destroy_one "$s"; done
        cat <<EOF

stacks deleted, but stateful resources are RETAINED on purpose:
  - DynamoDB table:        $(state_table_name)
  - S3 release tarballs:   $(artifacts_bucket_pattern)

they hold operator-critical data (trade history, release history) and
survive 'destroy' so a wrong-env / fat-finger / automation glitch
can't wipe them.

to fully wipe (irreversible):
  aws dynamodb delete-table --table-name $(state_table_name) \\
    --profile ${PROFILE} --region ${REGION}
  aws s3 rb s3://bull-call-${ENV}-artifacts-\$(aws sts get-caller-identity --query Account --output text --profile ${PROFILE}) \\
    --force --profile ${PROFILE} --region ${REGION}
EOF
        ;;
    *)
        echo "usage: $0 [validate|deploy|status|destroy] [dev|live]" >&2
        exit 2
        ;;
esac

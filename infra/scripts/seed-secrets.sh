#!/usr/bin/env bash
# Replace the placeholder String params with real SecureString values.
# Run once after `deploy.sh deploy`. Safe to re-run to rotate creds.
#
# Usage:  infra/scripts/seed-secrets.sh [dev|live]
#
# Reads from stdin (silently) so creds never end up in shell history.
set -euo pipefail

ENV="${1:-dev}"
PROFILE="${AWS_PROFILE:-busyweb}"
REGION="${AWS_REGION:-us-east-1}"
PREFIX="/${ENV}/ibkr-bull-call"

# -r so backslashes in the username/password are taken literally
# (otherwise read interprets them as line-continuations and mangles the
# input — disastrous for a password that happens to contain a backslash).
read -r -p "IBKR username: " USERID
read -r -s -p "IBKR password: " PASSWORD
echo

aws --profile "$PROFILE" --region "$REGION" ssm put-parameter \
    --name "${PREFIX}/tws_userid" \
    --value "$USERID" \
    --type SecureString \
    --overwrite >/dev/null

aws --profile "$PROFILE" --region "$REGION" ssm put-parameter \
    --name "${PREFIX}/tws_password" \
    --value "$PASSWORD" \
    --type SecureString \
    --overwrite >/dev/null

# Drop variable references; not a guarantee but reduces lifetime.
unset USERID PASSWORD

echo "credentials updated under ${PREFIX} (us-east-1)"
echo "next step: ssh in via SSM Session Manager and verify ibeam started:"
echo "  aws ssm start-session --target <instance-id> --profile ${PROFILE} --region ${REGION}"

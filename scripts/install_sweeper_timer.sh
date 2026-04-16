#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
#
# Install the onex-dispatch-claim-sweeper systemd user timer (OMN-8927).
# Run on both local machine and .201.
#
# Usage: bash scripts/install_sweeper_timer.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SYSTEMD_SRC="${REPO_ROOT}/systemd"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo "Installing onex-dispatch-claim-sweeper systemd user timer..."

mkdir -p "${SYSTEMD_USER_DIR}"

cp "${SYSTEMD_SRC}/onex-dispatch-claim-sweeper.service" "${SYSTEMD_USER_DIR}/"
cp "${SYSTEMD_SRC}/onex-dispatch-claim-sweeper.timer"   "${SYSTEMD_USER_DIR}/"

systemctl --user daemon-reload
systemctl --user enable --now onex-dispatch-claim-sweeper.timer

echo "Timer installed and started."
systemctl --user status onex-dispatch-claim-sweeper.timer --no-pager

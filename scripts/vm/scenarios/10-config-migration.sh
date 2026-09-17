#!/usr/bin/env bash
# A reinstall over an existing, hand-edited config must keep the user's values
# and add only what is missing. Mirrors the upgrade path: install_config.py's
# migration adds `confirmations`, and a threshold tightened by hand below the
# strictest shipped level must stay on a single frame, or the upgrade would turn
# a login that mostly worked into one that never completes.
source /tmp/lib.sh
CFG=/etc/ubuntu-hello/config.ini

# Case 1: a config from before the key existed, at the old default threshold.
sudo sed -i '/^confirmations = /d' "$CFG"
sudo sed -i 's/^certainty = .*/certainty = 3.5/' "$CFG"
sudo sed -i 's/^dark_threshold = .*/dark_threshold = 75/' "$CFG"      # a hand edit that must survive
pkg_reinstall
check kept_hand_edit          grep -q '^dark_threshold = 75' "$CFG"
check kept_threshold          grep -q '^certainty = 3.5' "$CFG"
check added_confirmations     grep -q '^confirmations = ' "$CFG"
note  added_value "$(grep '^confirmations = ' "$CFG" | awk '{print $3}')"
check added_value_is_fast_level test "$(grep '^confirmations = ' "$CFG" | awk '{print $3}')" = 2

# Case 2: a hand-tightened threshold below the strictest level.
sudo sed -i '/^confirmations = /d' "$CFG"
sudo sed -i 's/^certainty = .*/certainty = 2.0/' "$CFG"
pkg_reinstall
check tight_threshold_kept    grep -q '^certainty = 2.0' "$CFG"
check tight_stays_single_frame test "$(grep '^confirmations = ' "$CFG" | awk '{print $3}')" = 1

# Case 3: an existing value is never overwritten.
sudo sed -i 's/^confirmations = .*/confirmations = 9/' "$CFG"
pkg_reinstall
check existing_value_untouched test "$(grep '^confirmations = ' "$CFG" | awk '{print $3}')" = 9
check comments_survive        grep -q '^# The certainty of the detected face' "$CFG"

# Restore the shipped defaults for the scenarios that follow.
sudo sed -i 's/^certainty = .*/certainty = 2.6/; s/^confirmations = .*/confirmations = 4/' "$CFG"
emit

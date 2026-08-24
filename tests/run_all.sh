#!/usr/bin/env bash
# One entry point for the whole suite, so CI and a laptop run the same thing.
# Any failing suite fails the run; every suite runs regardless, so one bad
# commit shows all its damage in a single pass rather than one layer at a time.
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0
PYTHON_BIN="${PYTHON:-python3}"
NODE_BIN="${NODE:-node}"
run() {
  echo ""
  echo "──────── $1 ────────"
  shift
  "$@" || fail=1
}

run "density rules"        "$PYTHON_BIN" tests/test_density.py
run "instrument states"     "$PYTHON_BIN" tests/test_instrument_state.py
run "Stage 1-5 integration"  "$PYTHON_BIN" tests/test_stage6_integration.py
run "Stage 8 photo manifest" "$PYTHON_BIN" tests/test_stage8_photo_manifest.py
run "Stage 9 attachment consistency" "$PYTHON_BIN" tests/test_stage9_attachment_consistency.py
run "Stage 10 Saved Pits" "$PYTHON_BIN" tests/test_stage10_saved_pits.py
run "Stage 11 workspace" "$PYTHON_BIN" tests/test_stage11_workspace.py
run "Stage 12 security" "$PYTHON_BIN" tests/test_stage12_security.py
run "Stage 12 operations" "$PYTHON_BIN" tests/test_stage12_ops.py
run "Stage 12 storage lifecycle" "$PYTHON_BIN" tests/test_stage12_storage_lifecycle.py
run "Stage 13 interface" "$PYTHON_BIN" tests/test_stage13_ui.py
run "Stage 14 field transfer" "$PYTHON_BIN" tests/test_stage14_transfer.py
run "Resource Stage 1 downloads" "$PYTHON_BIN" tests/test_resource_stage1_downloads.py
run "Resource Stage 2 uploads" "$PYTHON_BIN" tests/test_resource_stage2_uploads.py
run "Resource Stage 3 HEIC" "$PYTHON_BIN" tests/test_resource_stage3_heic.py
run "Resource Stage 4 profiles" "$PYTHON_BIN" tests/test_resource_stage4_profiles.py
run "Resource Stage 5 threads" "$PYTHON_BIN" tests/test_resource_stage5_threads.py
run "Resource Stage 6 resilience" "$PYTHON_BIN" tests/test_resource_stage6_resilience.py
run "Resource Stage 6 live Waitress" "$PYTHON_BIN" tests/test_resource_stage6_live.py
run "Resource Stage 7 sizing" "$PYTHON_BIN" tests/test_resource_stage7_sizing.py
run "weather multi-select" "$PYTHON_BIN" tests/test_weather_multiselect.py
run "ground-condition multi-select" "$PYTHON_BIN" tests/test_ground_multiselect.py
run "Stage 13 interface behaviour" "$NODE_BIN" tests/test_stage13_ui.mjs
run "profile-table Enter navigation" "$NODE_BIN" tests/test_table_enter_navigation.mjs
run "optional radio clear selection" "$NODE_BIN" tests/test_clearable_radios.mjs
run "weather multi-select UI" "$NODE_BIN" tests/test_weather_multiselect.mjs
run "ground-condition multi-select UI" "$NODE_BIN" tests/test_ground_multiselect.mjs
run "Stage 12 CSRF UI" "$NODE_BIN" tests/test_stage12_csrf_ui.mjs
run "instrument UI"         "$NODE_BIN"    tests/test_instrument_ui.mjs
run "record workflow UI"    "$NODE_BIN"    tests/test_record_workflow_ui.mjs
run "attachment outbox"    "$NODE_BIN"    tests/test_attachment_outbox.mjs
run "attachment uploads"   "$NODE_BIN"    tests/test_attachment_flush.mjs
run "attachment manifest UI" "$NODE_BIN"   tests/test_attachment_manifest_ui.mjs
run "Saved Pits finder UI" "$NODE_BIN"   tests/test_stage10_saved_pits_ui.mjs
run "workspace UI" "$NODE_BIN"   tests/test_stage11_workspace_ui.mjs
run "profile empty-state API" "$PYTHON_BIN" tests/test_profile_empty_state.py
run "profile empty states" "$NODE_BIN" tests/test_profile_empty_state_ui.mjs
run "profile figure"       "$PYTHON_BIN" tests/test_plot.py
run "end-to-end smoke"     "$PYTHON_BIN" tests/test_smoke.py
run "Stage 12 Flask security" "$PYTHON_BIN" tests/test_stage12_flask_security.py
run "coordinate transform" "$NODE_BIN"    tests/test_coords.mjs
run "DOM behaviour"        "$NODE_BIN"    tests/test_dom.mjs

echo ""
if [ "$fail" -ne 0 ]; then
  echo "SUITE FAILED"
  exit 1
fi
echo "all suites passed"

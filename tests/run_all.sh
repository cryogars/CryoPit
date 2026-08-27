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
run "density rail parity"  node tests/test_density_rail_parity.mjs
run "instrument states"     "$PYTHON_BIN" tests/test_instrument_state.py
run "archive integration"  "$PYTHON_BIN" tests/test_archive_integration.py
run "photo manifest" "$PYTHON_BIN" tests/test_photo_manifest.py
run "attachment consistency" "$PYTHON_BIN" tests/test_attachment_consistency.py
run "Saved Pits" "$PYTHON_BIN" tests/test_saved_pits.py
run "workspace" "$PYTHON_BIN" tests/test_workspace.py
run "security core" "$PYTHON_BIN" tests/test_security_core.py
run "backup / restore" "$PYTHON_BIN" tests/test_backup_restore.py
run "storage lifecycle" "$PYTHON_BIN" tests/test_storage_lifecycle.py
run "interface contract" "$PYTHON_BIN" tests/test_interface_contract.py
run "field transfer" "$PYTHON_BIN" tests/test_field_transfer.py
run "resource downloads" "$PYTHON_BIN" tests/test_resource_downloads.py
run "resource uploads" "$PYTHON_BIN" tests/test_resource_uploads.py
run "resource HEIC" "$PYTHON_BIN" tests/test_resource_heic.py
run "resource profiles" "$PYTHON_BIN" tests/test_resource_profiles.py
run "resource threads" "$PYTHON_BIN" tests/test_resource_threads.py
run "resource resilience" "$PYTHON_BIN" tests/test_resource_resilience.py
run "resource live server" "$PYTHON_BIN" tests/test_resource_live_server.py
run "resource sizing" "$PYTHON_BIN" tests/test_resource_sizing.py
run "weather multi-select" "$PYTHON_BIN" tests/test_weather_multiselect.py
run "ground-condition multi-select" "$PYTHON_BIN" tests/test_ground_multiselect.py
run "interface behaviour" "$NODE_BIN" tests/test_interface_ui.mjs
run "profile-table Enter navigation" "$NODE_BIN" tests/test_table_enter_navigation.mjs
run "optional radio clear selection" "$NODE_BIN" tests/test_clearable_radios.mjs
run "weather multi-select UI" "$NODE_BIN" tests/test_weather_multiselect.mjs
run "ground-condition multi-select UI" "$NODE_BIN" tests/test_ground_multiselect.mjs
run "CSRF UI" "$NODE_BIN" tests/test_csrf_ui.mjs
run "instrument UI"         "$NODE_BIN"    tests/test_instrument_ui.mjs
run "record workflow UI"    "$NODE_BIN"    tests/test_record_workflow_ui.mjs
run "attachment outbox"    "$NODE_BIN"    tests/test_attachment_outbox.mjs
run "attachment uploads"   "$NODE_BIN"    tests/test_attachment_flush.mjs
run "attachment manifest UI" "$NODE_BIN"   tests/test_attachment_manifest_ui.mjs
run "Saved Pits finder UI" "$NODE_BIN"   tests/test_saved_pits_ui.mjs
run "workspace UI" "$NODE_BIN"   tests/test_workspace_ui.mjs
run "profile empty-state API" "$PYTHON_BIN" tests/test_profile_empty_state.py
run "profile empty states" "$NODE_BIN" tests/test_profile_empty_state_ui.mjs
run "profile figure"       "$PYTHON_BIN" tests/test_plot.py
run "end-to-end smoke"     "$PYTHON_BIN" tests/test_smoke.py
run "Flask security" "$PYTHON_BIN" tests/test_flask_security.py
run "coordinate transform" "$NODE_BIN"    tests/test_coords.mjs
run "DOM behaviour"        "$NODE_BIN"    tests/test_dom.mjs

echo ""
if [ "$fail" -ne 0 ]; then
  echo "SUITE FAILED"
  exit 1
fi
echo "all suites passed"

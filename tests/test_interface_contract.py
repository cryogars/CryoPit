"""Stage 13 visual-system, responsive and accessibility regression checks."""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "cryopit/templates/base.html").read_text(encoding="utf-8")
WORKSPACE = (ROOT / "cryopit/templates/workspace.html").read_text(encoding="utf-8")
CSS = (ROOT / "cryopit/static/css/95_stage13.css").read_text(encoding="utf-8")
CSS_BASE = (ROOT / "cryopit/static/css/00_base.css").read_text(encoding="utf-8")
JS = (ROOT / "cryopit/static/js/95_stage13_ui.js").read_text(encoding="utf-8")
UI_JS = (ROOT / "cryopit/static/js/40_ui.js").read_text(encoding="utf-8")
SECTIONS = list((ROOT / "cryopit/templates/sections").glob("*.html"))


def test_no_remote_font_dependency():
    assert "fonts.googleapis.com" not in BASE
    assert "fonts.gstatic.com" not in BASE
    assert "ui-sans-serif" in CSS and "ui-monospace" in CSS


def test_mobile_and_browser_metadata_are_present():
    assert 'name="viewport"' in BASE
    assert 'name="theme-color"' in BASE
    assert 'name="color-scheme"' in BASE


def test_workspace_has_clear_actions_and_operational_assurance():
    assert 'class="workspace-assurance"' in WORKSPACE
    assert "Owner-scoped" in WORKSPACE
    assert "Offline-capable" in WORKSPACE
    assert "Recoverable archive" in WORKSPACE
    assert 'id="workspace-new"' in WORKSPACE
    assert 'id="workspace-find"' in WORKSPACE
    assert 'class="workspace-action-arrow"' in WORKSPACE


def test_every_section_has_a_visible_subtitle():
    assert len(SECTIONS) == 12
    for path in SECTIONS:
        text = path.read_text(encoding="utf-8")
        assert '<span class="sec-heading">' in text, path.name
        assert '<span class="sec-subtitle">' in text, path.name


def test_density_cutter_lives_with_density_autofill_controls():
    identity = (ROOT / "cryopit/templates/sections/01_identity.html").read_text(encoding="utf-8")
    density = (ROOT / "cryopit/templates/sections/05_density.html").read_text(encoding="utf-8")
    temperature = (ROOT / "cryopit/templates/sections/04_temperature.html").read_text(encoding="utf-8")

    assert "Density cutter (cc)" not in identity
    for cutter_id in ("dc100", "dc250", "dc1000"):
        assert f'id="{cutter_id}"' not in identity
        assert f'id="{cutter_id}"' in density

    assert '<div class="row" style="margin-bottom:16px">' in density
    assert "Auto-fill depths" in density
    assert '>every 10 cm</option>' in density
    assert '>every 5 cm</option>' in density
    assert "↧ generate from total depth" in density

    # Density deliberately reuses the compact field-card/autofill language
    # already established by the Temperature section.
    assert "Auto-fill depths" in temperature
    assert "↧ generate from total depth" in temperature


def test_example_placeholders_are_opt_in_and_explicitly_classified():
    config = (ROOT / "cryopit/config.py").read_text(encoding="utf-8")
    web = (ROOT / "cryopit/web.py").read_text(encoding="utf-8")
    core = (ROOT / "cryopit/static/js/00_core.js").read_text(encoding="utf-8")
    tables = (ROOT / "cryopit/static/js/20_tables.js").read_text(encoding="utf-8")
    layers = (ROOT / "cryopit/static/js/80_layer_density.js").read_text(encoding="utf-8")
    identity = (ROOT / "cryopit/templates/sections/01_identity.html").read_text(encoding="utf-8")

    assert 'SHOW_EXAMPLE_PLACEHOLDERS = _bool("CRYOPIT_SHOW_EXAMPLE_PLACEHOLDERS", "false")' in config
    assert '__SHOW_EXAMPLE_PLACEHOLDERS__' in core
    assert 'function examplePlaceholder(value)' in core
    assert 'data-example-placeholder="120"' in identity
    assert 'data-example-placeholder="65.157650"' in identity
    assert '_render_example_placeholders' in web
    assert 'if not SHOW_EXAMPLE_PLACEHOLDERS:' in web
    assert 'examplePlaceholder(\'-2.0\')' in tables
    assert 'examplePlaceholder(\'250\')' in layers

    # Instructional and UI-state placeholders are deliberately not classified
    # as sample data and must survive when examples are off.
    assert 'placeholder="Your name"' in identity
    assert 'placeholder="Type location"' in identity
    assert 'placeholder="—"' in identity


def test_static_example_placeholder_renderer_materializes_only_when_enabled():
    source = (ROOT / "cryopit/web.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_EXAMPLE_PLACEHOLDER_ATTR"
            for t in node.targets
        ):
            wanted.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_render_example_placeholders":
            wanted.append(node)
    assert len(wanted) == 2
    module = ast.Module(body=wanted, type_ignores=[])

    sample = '<input data-example-placeholder="120">'
    off = {"re": re, "SHOW_EXAMPLE_PLACEHOLDERS": False}
    exec(compile(module, "web.py", "exec"), off)
    assert off["_render_example_placeholders"](sample) == sample

    on = {"re": re, "SHOW_EXAMPLE_PLACEHOLDERS": True}
    exec(compile(module, "web.py", "exec"), on)
    rendered = on["_render_example_placeholders"](sample)
    assert 'data-example-placeholder="120"' in rendered
    assert 'placeholder="120"' in rendered


def test_example_placeholder_env_boolean_and_campaign_fallback():
    code = (
        "import cryopit.config as c; "
        "print(str(c.SHOW_EXAMPLE_PLACEHOLDERS).lower()); "
        "print(c.CAMPAIGN)"
    )
    base = os.environ.copy()
    base.pop("CRYOPIT_SHOW_EXAMPLE_PLACEHOLDERS", None)
    base.pop("CRYOPIT_CAMPAIGN", None)
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=base,
                          text=True, capture_output=True, check=True)
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == "false"
    assert re.fullmatch(r"WY\d{4}", lines[1])

    enabled = base.copy()
    enabled["CRYOPIT_SHOW_EXAMPLE_PLACEHOLDERS"] = "true"
    enabled["CRYOPIT_CAMPAIGN"] = "FIELDTEST"
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=enabled,
                          text=True, capture_output=True, check=True)
    assert proc.stdout.strip().splitlines() == ["true", "FIELDTEST"]


def test_autofill_and_copy_remain_real_values_not_example_placeholders():
    tables = (ROOT / "cryopit/static/js/20_tables.js").read_text(encoding="utf-8")
    # User-triggered depth generation/copy must continue writing actual values.
    assert 'value="${h}"' in tables
    assert 'value="${top}"' in tables
    assert 'value="${bot}"' in tables
    # Only the companion sample-looking measurement prompt is conditional.
    assert 'placeholder="${examplePlaceholder(\'-2.0\')}"' in tables
    assert 'placeholder="${examplePlaceholder(\'1.173\')}"' in tables


def test_example_placeholder_contract_is_documented():
    cfg = (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    prod = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    assert '`CRYOPIT_SHOW_EXAMPLE_PLACEHOLDERS` | `false`' in cfg
    assert 'defaults to the current water year' in cfg
    assert 'Temperature Auto-fill depths' in cfg
    assert 'Density Auto-fill depths' in cfg
    assert 'LWC Copy intervals from Density' in cfg
    assert '#CRYOPIT_SHOW_EXAMPLE_PLACEHOLDERS=false' in env
    assert 'CRYOPIT_SHOW_EXAMPLE_PLACEHOLDERS=false' in prod


def test_responsive_contrast_motion_and_print_modes_exist():
    assert "@media(max-width:720px)" in CSS
    assert "@media(max-width:520px)" in CSS
    assert "@media(prefers-contrast:more)" in CSS
    assert "@media(prefers-reduced-motion:reduce)" in CSS
    assert "@media print" in CSS


def test_stage13_buttons_and_attachment_states_are_explicitly_styled():
    flat = re.sub(r"/\*[\s\S]*?\*/", "", CSS)
    for selector in (".workspace-action", ".workspace-link", ".workspace-current-actions button"):
        assert re.search(re.escape(selector) + r"\{[^}]*background", flat), selector
    primary_default = re.search(
        r"\.workspace-current-actions button\.workspace-continue\{([^}]*)\}",
        flat,
    )
    assert primary_default, "current-work primary default state"
    assert "background:var(--acc)" in primary_default.group(1)
    assert "color:var(--accent-ink)" in primary_default.group(1)
    primary_states = re.search(
        r"\.workspace-current-actions \.workspace-continue:hover,\s*"
        r"\.workspace-current-actions \.workspace-continue:focus-visible,\s*"
        r"\.workspace-current-actions \.workspace-continue:active\{([^}]*)\}",
        flat,
    )
    assert primary_states, "current-work primary interaction states"
    assert "background:var(--acc)" in primary_states.group(1)
    assert "color:var(--accent-ink)" in primary_states.group(1)
    assert "--accent-ink:#ffffff" in CSS_BASE
    assert "--accent-ink:#08161f" in CSS_BASE
    assert ".att-chip::before" in CSS
    assert ".att-chip.failed::before" in CSS
    assert ".att-chip.uploading::before" in CSS
    assert "body.record-banner-open" in CSS
    assert "body.post-banner-open" in CSS



def test_current_work_button_has_stable_hook_and_draft_fallback():
    assert 'id="workspace-current-continue"' in WORKSPACE
    assert '>Continue draft</button>' in WORKSPACE
    workspace_js = (ROOT / "cryopit/static/js/65_workspace.js").read_text(encoding="utf-8")
    assert "_loaded_site_id?'Continue record':'Continue draft'" in workspace_js
    assert "setAttribute('aria-label',continueLabel)" in workspace_js


def test_toggle_card_native_input_fills_the_whole_pill():
    flat = re.sub(r"/\*[\s\S]*?\*/", "", CSS_BASE)
    rule = re.search(r"\.tog input\{([^}]*)\}", flat)
    assert rule, "toggle input rule"
    body = rule.group(1)
    assert "position:absolute" in body
    assert "inset:0" in body
    assert "width:100%" in body and "height:100%" in body
    assert "opacity:0" in body
    assert "pointer-events:none" not in body
    assert "width:1px" not in body and "height:1px" not in body
    reset = re.search(r'\.tog > input\[type="radio"\],\.tog > input\[type="checkbox"\]\{([^}]*)\}', flat)
    assert reset, "toggle input field-card reset"
    assert "min-height:0" in reset.group(1)
    assert "padding:0" in reset.group(1)
    assert "background:transparent" in reset.group(1)
    assert ".tog > span" in flat and "pointer-events:none" in flat


def test_optional_radio_groups_have_explicit_clear_actions():
    weather = (ROOT / "cryopit/templates/sections/02_weather.html").read_text(encoding="utf-8")
    ground = (ROOT / "cryopit/templates/sections/03_ground.html").read_text(encoding="utf-8")
    # Weather became multi-select in 3.7.0rc5, so each native checkbox can be
    # cleared directly. The explicit clear action remains for optional radios.
    assert weather.count("data-clearable-radio") == 0
    assert weather.count("data-weather-multi") == 4
    assert ground.count("data-clearable-radio") == 5
    assert "function initClearableRadios()" in UI_JS
    assert "button.type='button'" in UI_JS
    assert "selected.checked=false" in UI_JS
    assert "focus({preventScroll:true})" in UI_JS
    flat = re.sub(r"/\*[\s\S]*?\*/", "", CSS_BASE)
    clear = re.search(r"\.radio-clear\{([^}]*)\}", flat)
    assert clear, "clear-selection style"
    assert "background:transparent" in clear.group(1)
    assert ".radio-clear:focus-visible" in flat

def test_section_status_anchor_supports_stage13_heading_wrapper():
    assert "title.closest('.sec-heading')" in UI_JS


def test_field_accessibility_enhancer_is_presentation_only():
    assert "aria-labelledby" in JS
    assert "aria-label" in JS
    assert "has-value" in JS
    for forbidden in ("/api/", "doArchive", "collect(", "uploadAttachment", "fetch("):
        assert forbidden not in JS


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]
if __name__ == "__main__":
    failures = 0
    for test in TESTS:
        try:
            test()
            print("PASS", test.__name__)
        except Exception as exc:
            failures += 1
            print("FAIL", test.__name__, repr(exc))
    if failures:
        raise SystemExit(f"{failures} Stage 13 UI tests failed")
    print(f"{len(TESTS)} Stage 13 UI tests passed")

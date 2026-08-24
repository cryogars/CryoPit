"""Profile empty-state contract without importing Flask."""
import ast
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
WEB = os.path.join(ROOT, "cryopit", "web.py")


def _load_helpers():
    source = open(WEB, encoding="utf-8").read()
    tree = ast.parse(source, WEB)
    wanted = {"_num_or_none", "_has_profile_data"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), WEB, "exec"), ns)
    return ns["_has_profile_data"], source


def test_profile_data_categories():
    has_data, _ = _load_helpers()
    assert not has_data({})
    assert not has_data({"lwc": [{"top": 10, "bottom": 0, "a": 3.2}]})
    assert not has_data({"temperature": [{"height": 100, "temp": None}]})
    assert has_data({"temperature": [{"height": 100, "temp": -5.2}]})
    assert not has_data({"density": [{"top": 100, "bottom": 0, "a": 0}]})
    assert has_data({"density": [{"top": 100, "bottom": 0, "a": 280}]})
    assert not has_data({"stratigraphy": [{"top": 100, "bottom": None}]})
    assert has_data({"stratigraphy": [{"top": 100, "bottom": 0}]})


def test_api_guard_precedes_matplotlib_import():
    _, source = _load_helpers()
    route = source[source.index('@bp.post("/api/profile")'):source.index('# ---------------------------------------------------------------------------', source.index('@bp.post("/api/profile")'))]
    assert 'if not _has_profile_data(payload):' in route
    assert route.index('if not _has_profile_data(payload):') < route.index('from .plot import render_profile')
    assert '"empty": True' in route


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn(); print("PASS", fn.__name__)
    print(f"{len(tests)} profile empty-state backend tests passed")

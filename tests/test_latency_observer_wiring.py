import ast
from pathlib import Path


def test_latency_observer_is_registered_on_pipeline_worker():
    source = Path(__file__).resolve().parents[1] / "apps" / "voice" / "bot.py"
    module = ast.parse(source.read_text())
    calls = [
        node for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PipelineWorker"
    ]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert "observers" in keywords
    assert isinstance(keywords["observers"], ast.List)
    assert len(keywords["observers"].elts) == 1
    assert isinstance(keywords["observers"].elts[0], ast.Name)
    assert keywords["observers"].elts[0].id == "latency_observer"

    params = keywords["params"]
    assert isinstance(params, ast.Call)
    assert isinstance(params.func, ast.Name)
    assert params.func.id == "PipelineParams"
    assert "observers" not in {keyword.arg for keyword in params.keywords}

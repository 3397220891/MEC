import os
import tempfile

# Must be set before app.main is imported anywhere: keep tests off the real DB / cache.
_tmp = tempfile.mkdtemp(prefix="orbitalguard-test-")
os.environ.setdefault("ORBITALGUARD_DB", ":memory:")
os.environ.setdefault("LLM_CACHE_DIR", os.path.join(_tmp, "cache"))
os.environ.setdefault("LLM_LOG_PATH", os.path.join(_tmp, "llm.jsonl"))
for k in ("DEEPSEEK_API_KEY", "LOCAL_BASE_URL", "OPERATOR_TOKEN"):
    os.environ.pop(k, None)

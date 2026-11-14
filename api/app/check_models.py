"""列出每个服务商当前可用的模型名，用来确认 .env 里的模型名写得对。
运行：docker compose exec api python -m app.check_models
"""
from openai import OpenAI

from .llm import providers_from_env

if __name__ == "__main__":
    providers = providers_from_env()
    if not providers:
        print("没有配置任何服务商：检查 .env 里的 DEEPSEEK_API_KEY / LOCAL_BASE_URL")
    for p in providers:
        print(f"== {p.name} ({p.base_url}) 配置的模型: {p.model}, 视觉: {p.vision_model}")
        try:
            for m in OpenAI(base_url=p.base_url, api_key=p.api_key).models.list():
                print("  ", m.id)
        except Exception as e:  # noqa: BLE001
            print("   列表获取失败:", type(e).__name__, e)

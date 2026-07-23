# -*- coding: utf-8 -*-
"""SDK 获取可用模型列表的冒烟测试。"""
import asyncio
import json

from qoder_agent_sdk import QoderSDKClient, QoderAgentOptions, qodercli_auth


async def main():
    opts = QoderAgentOptions(
        auth=qodercli_auth(),
        cwd="/Users/lixiang/code/webgpu/super-me",
        tools=[],
        max_turns=1,
    )
    client = QoderSDKClient(options=opts)
    await client.connect(None)
    try:
        models = await client.get_available_models()
        print("TYPE:", type(models).__name__)
        print(json.dumps(models, ensure_ascii=False, default=lambda o: getattr(o, "__dict__", str(o)), indent=1)[:2000])
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=90))

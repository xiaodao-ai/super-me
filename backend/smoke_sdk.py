# -*- coding: utf-8 -*-
"""SDK 连通性冒烟测试：验证 qodercli 登录态可用。"""
import asyncio

from qoder_agent_sdk import query, QoderAgentOptions, qodercli_auth


async def main():
    opts = QoderAgentOptions(
        auth=qodercli_auth(),
        cwd="/Users/lixiang/code/webgpu/super-me",
        tools=[],
        max_turns=1,
    )
    async for msg in query(prompt="只回复两个字：在线", options=opts):
        t = type(msg).__name__
        print("MSG", t)
        if t == "AssistantMessage":
            for b in getattr(msg, "content", []):
                if hasattr(b, "text"):
                    print("TEXT:", b.text[:80])
        if t == "ResultMessage":
            print("RESULT subtype:", getattr(msg, "subtype", None))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=90))

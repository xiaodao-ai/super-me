# -*- coding: utf-8 -*-
"""独立复现 TL 项目规划调用，观察消息流与耗时。"""
import asyncio
import time

from qoder_agent_sdk import query, QoderAgentOptions, qodercli_auth
from personas import PROJECT_PLAN_PROMPT


async def main():
    t0 = time.time()
    opts = QoderAgentOptions(
        auth=qodercli_auth(),
        cwd="/Users/lixiang/code/webgpu/super-me/workspace",
        tools=[],
        disallowed_tools=["Bash"],
        permission_mode="bypassPermissions",
        allow_dangerously_skip_permissions=True,
        extra_args={},
        max_turns=1,
    )
    prompt = PROJECT_PLAN_PROMPT.format(
        title="英语单词卡片小站",
        desc="纯前端静态网页：10个单词卡片，点击翻面看释义。需测试和评审。")
    async for msg in query(prompt=prompt, options=opts):
        n = type(msg).__name__
        print(f"[{time.time() - t0:6.1f}s] {n}")
        if n == "AssistantMessage":
            for b in getattr(msg, "content", []):
                if hasattr(b, "text"):
                    print("   TEXT:", b.text[:150].replace("\n", " "))
        if n == "ResultMessage":
            print("   subtype:", getattr(msg, "subtype", None))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=180))

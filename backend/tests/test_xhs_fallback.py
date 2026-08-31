import sys
import types
import unittest


# 本地测试环境不安装 Agent 运行时；为导入被测编排模块提供最小桩。
if "hello_agents" not in sys.modules:
    hello_agents = types.ModuleType("hello_agents")
    hello_agents.SimpleAgent = type("SimpleAgent", (), {})
    hello_agents.HelloAgentsLLM = type("HelloAgentsLLM", (), {})
    hello_agents_tools = types.ModuleType("hello_agents.tools")
    hello_agents_tools.MCPTool = type("MCPTool", (), {})
    sys.modules["hello_agents"] = hello_agents
    sys.modules["hello_agents.tools"] = hello_agents_tools

if "backend.app.services.llm_service" not in sys.modules:
    llm_service = types.ModuleType("backend.app.services.llm_service")
    llm_service.get_llm = lambda: None
    sys.modules["backend.app.services.llm_service"] = llm_service

if "backend.app.config" not in sys.modules:
    config = types.ModuleType("backend.app.config")
    config.get_settings = lambda: None
    sys.modules["backend.app.config"] = config

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner


class XhsFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        self.events = []

    async def _progress(self, stage, message, progress):
        self.events.append((stage, message, progress))

    async def test_normal_xhs_result_is_unchanged(self):
        expected = "这是小红书热门精选游记的提取结果：景点 A"

        result = await self.planner._search_attractions_with_xhs_fallback(
            "东京", "拍照", "zh", self._progress, 10,
            search_func=lambda city, keywords, language: expected,
        )

        self.assertEqual(result, expected)
        self.assertEqual(self.events, [])

    async def test_missing_or_expired_cookie_falls_back(self):
        def fail(*_args):
            raise RuntimeError("小红书 Cookie 未配置或已过期")

        result = await self.planner._search_attractions_with_xhs_fallback(
            "东京", "拍照", "zh", self._progress, 10, search_func=fail,
        )

        self.assertIn("没有来自小红书的景点候选或真实评价", result)
        self.assertIn("不得声称候选来自小红书", result)
        self.assertEqual(
            self.events,
            [("attraction_search", "小红书数据不可用，已使用降级方案继续生成。", 10)],
        )

    async def test_extraction_failure_text_also_falls_back(self):
        result = await self.planner._search_attractions_with_xhs_fallback(
            "东京", "拍照", "zh", self._progress, 10,
            search_func=lambda *_args: "尝试提取小红书结构化数据失败，降级回常规处理。",
        )

        self.assertIn("小红书研究数据不可用", result)
        self.assertEqual(len(self.events), 1)


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()

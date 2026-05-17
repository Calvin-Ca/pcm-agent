"""
技术债 #5：意图关键词散落在 IntentRouter.__init__

验证关键词/规则提取到 config/intent_rules.yaml 后：
1. 从 yaml 加载的关键词集合与原硬编码完全等价（代表性意图逐项比对）
2. yaml 缺失/损坏时 IntentRouter 仍能构造并用内置默认正常分类（降级安全）
3. 等价重构未改变意图判定行为（若干代表性 query 的分类结果不变）

IntentRouter 是 LLM 不可用时的降级 fallback，构造期/降级期绝不能因缺配置崩溃。
"""

import pytest

from app.services.intent_router import IntentRouter, IntentType


# 迁移前 __init__ 中硬编码的"黄金值"快照（从 git 历史 / 原源码逐字摘录）。
# yaml 加载结果必须与此完全一致，证明提取是无损重构。
GOLDEN_KNOWLEDGE = [
    "什么是", "如何", "怎么", "为什么", "规则", "制度", "政策", "流程",
    "说明", "介绍", "解释", "定义", "faq", "常见问题", "帮助",
    "填写", "申请", "方法", "步骤",
    "截止", "截止时间", "规定", "要求", "期限", "时限", "多久",
    "什么时候", "几点", "几日", "不得", "须", "应当", "必须",
    "类型", "有哪些", "哪些类型", "分类", "种类", "有什么类型",
    "分为哪些", "包括哪些", "有几种",
]

GOLDEN_TOOL = {
    "query_timesheet": [
        "查询工时", "我的工时", "查看工时", "本人工时", "工时记录",
        "工时", "工作时间", "加班", "考勤", "打卡", "上班", "下班",
        "本周工时", "本月工时",
    ],
    "query_project": [
        "查询项目", "项目信息", "项目详情", "项目成员", "项目进度",
        "查看项目", "显示项目",
        "项目",
    ],
    "compute_statistics": [
        "统计", "汇总", "报表", "分析", "总计", "平均", "对比",
        "统计数据", "数据分析",
        "统计部门", "统计工时", "统计项目", "部门统计", "项目统计",
    ],
}

GOLDEN_COMPLEX = [
    "并且", "然后", "接着", "同时", "另外", "还要", "以及",
    "生成报告", "制作图表", "发送邮件", "导出数据", "批量处理",
    "并生成", "并统计", "然后", "同时",
]

GOLDEN_CHAT = [
    "你好", "您好", "嗨", "hello", "hi",
    "谢谢", "感谢", "多谢",
    "再见", "拜拜", "bye",
    "很高兴", "随便聊聊", "天气",
]


class TestKeywordsLoadedFromYaml:
    """① yaml 加载后关键词集合与原硬编码等价。"""

    def test_knowledge_keywords_equivalent(self):
        r = IntentRouter()
        assert r.knowledge_keywords == GOLDEN_KNOWLEDGE

    def test_tool_keywords_equivalent(self):
        r = IntentRouter()
        assert r.tool_keywords == GOLDEN_TOOL

    def test_complex_indicators_equivalent(self):
        r = IntentRouter()
        assert r.complex_indicators == GOLDEN_COMPLEX

    def test_chat_keywords_equivalent(self):
        r = IntentRouter()
        assert r.chat_keywords == GOLDEN_CHAT


class TestYamlMissingFallsBackToDefault:
    """② yaml 缺失/损坏 → 用内置默认，降级路径不崩。"""

    def test_missing_yaml_uses_builtin_default(self, monkeypatch):
        # 强制 yaml 路径指向不存在的文件
        import app.services.intent_router as mod

        monkeypatch.setattr(
            mod, "_INTENT_RULES_PATH", "/nonexistent/__no_such_intent_rules__.yaml"
        )
        # 构造不应抛异常
        r = IntentRouter()
        # 仍有内置默认关键词
        assert r.knowledge_keywords == GOLDEN_KNOWLEDGE
        assert r.tool_keywords == GOLDEN_TOOL
        assert r.complex_indicators == GOLDEN_COMPLEX

    def test_corrupt_yaml_uses_builtin_default(self, tmp_path, monkeypatch):
        import app.services.intent_router as mod

        bad = tmp_path / "intent_rules.yaml"
        bad.write_text("this: [is, not: valid: yaml: ::", encoding="utf-8")
        monkeypatch.setattr(mod, "_INTENT_RULES_PATH", str(bad))

        r = IntentRouter()  # 不应抛
        assert r.knowledge_keywords == GOLDEN_KNOWLEDGE
        assert r.tool_keywords == GOLDEN_TOOL

    def test_classification_still_works_without_yaml(self, monkeypatch):
        """缺配置时降级规则分类仍正常工作。"""
        import app.services.intent_router as mod

        monkeypatch.setattr(
            mod, "_INTENT_RULES_PATH", "/nonexistent/__no_such__.yaml"
        )
        r = IntentRouter()
        # 工时查询 → TOOL_EXECUTION
        res = r._rule_based_classify("查询我的工时")
        assert res.intent_type == IntentType.TOOL_EXECUTION
        # 知识问答
        res2 = r._rule_based_classify("什么是工时填报制度")
        assert res2.intent_type == IntentType.KNOWLEDGE_QA


class TestBehaviorUnchanged:
    """③ 等价重构未改判定行为（代表性 query 分类结果不变）。"""

    @pytest.mark.parametrize(
        "msg,expected",
        [
            # 期望值 = 迁移前当前实现的真实输出（等价 = 与现状一致，
            # 重构不得改判定逻辑，故照抄现状基线）
            ("查询我的工时", IntentType.TOOL_EXECUTION),
            ("统计部门工时", IntentType.TOOL_EXECUTION),
            ("什么是加班制度", IntentType.KNOWLEDGE_QA),
            # "工时填报截止时间是几点" 现状即被判为 tool_execution（"工时"强信号
            # 压过知识问句），保留现状不擅改
            ("工时填报截止时间是几点", IntentType.TOOL_EXECUTION),
        ],
    )
    def test_rule_based_classify_unchanged(self, msg, expected):
        r = IntentRouter()
        res = r._rule_based_classify(msg.lower(), msg)
        assert res.intent_type == expected

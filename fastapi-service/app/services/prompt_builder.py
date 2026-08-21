"""
Prompt Builder - 带记忆上下文的消息构建器（Task 39）

将短期会话历史和长期用户记忆注入 LLM 调用，构建 OpenAI messages 格式的消息列表。

输出结构：
    [
      {"role": "system",    "content": "...system prompt...\n\n用户记忆:\n- ..."},
      {"role": "user",      "content": "历史消息1"},     ← 最近 N 轮
      {"role": "assistant", "content": "历史回复1"},
      ...
      {"role": "user",      "content": "当前消息"},       ← 最新一条
    ]
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PromptBuilder:
    """
    带记忆的 Prompt 构建器。

    在构建 LLM messages 时：
    1. 将长期记忆摘要追加到 system prompt
    2. 将短期会话历史展开为 user/assistant 轮次
    3. 追加当前用户消息
    """

    def __init__(
        self,
        session_memory_service=None,
        user_memory_service=None,
    ):
        self._session_memory = session_memory_service
        self._user_memory = user_memory_service

    async def build_messages_with_history(
        self,
        user_message: str,
        session_id: Optional[str],
        user_id: Optional[str],
        base_system_prompt: str,
        top_k_memories: int = 5,
    ) -> List[Dict[str, str]]:
        """
        构建带历史和记忆的 OpenAI messages 列表。

        Args:
            user_message:       当前用户消息
            session_id:         会话 ID（为 None 时不加载历史）
            user_id:            用户 ID（为 None 时不加载长期记忆）
            base_system_prompt: 基础系统提示词
            top_k_memories:     加载最相关的长期记忆条数

        Returns:
            符合 OpenAI messages 格式的消息列表
        """
        # 1. 加载长期记忆，追加到 system prompt
        system_content = base_system_prompt
        if user_id and self._user_memory:
            try:
                memories = await self._user_memory.retrieve_relevant_memories(
                    user_id=user_id,
                    query=user_message,
                    top_k=top_k_memories,
                )
                if memories:
                    memory_lines = "\n".join(f"- {m.content}" for m in memories)
                    system_content += f"\n\n关于该用户的已知信息：\n{memory_lines}"
                    logger.debug(f"注入 {len(memories)} 条长期记忆: user_id={user_id}")
            except Exception as e:
                logger.warning(f"加载长期记忆失败，跳过: {e}")

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_content}
        ]

        # 2. 加载短期会话历史（短期记忆）
        if session_id and self._session_memory:
            try:
                history = await self._session_memory.get_conversation_history(session_id)
                for msg in history:
                    messages.append({"role": msg.role, "content": msg.content})
                if history:
                    logger.debug(f"注入 {len(history)} 条历史消息: session_id={session_id}")
            except Exception as e:
                logger.warning(f"加载会话历史失败，跳过: {e}")

        # 3. 追加当前消息
        messages.append({"role": "user", "content": user_message})

        return messages

    async def get_history_as_messages(
        self,
        session_id: Optional[str],
    ) -> List[Dict[str, str]]:
        """
        仅获取会话历史（不含 system prompt 和当前消息），
        用于意图分类时提供上下文。
        """
        if not session_id or not self._session_memory:
            return []
        try:
            history = await self._session_memory.get_conversation_history(session_id)
            return [{"role": msg.role, "content": msg.content} for msg in history]
        except Exception as e:
            logger.warning(f"获取会话历史失败: {e}")
            return []

    async def get_business_state(self, session_id: Optional[str]) -> Dict[str, Any]:
        if not session_id or not self._session_memory:
            return {}
        try:
            return await self._session_memory.get_business_state(session_id)
        except Exception as e:
            logger.warning(f"获取会话业务状态失败: {e}")
            return {}

    async def update_business_state(
        self,
        session_id: Optional[str],
        user_id: Optional[str],
        business_state: Dict[str, Any],
    ) -> None:
        if not session_id or not self._session_memory:
            return
        try:
            await self._session_memory.update_business_state(
                session_id=session_id,
                user_id=user_id or "anonymous",
                business_state=business_state,
            )
        except Exception as e:
            logger.warning(f"保存会话业务状态失败: {e}")


# ─── 全局实例 ─────────────────────────────────────────────────────────────────

_prompt_builder: Optional[PromptBuilder] = None


def initialize_prompt_builder(session_memory_service=None, user_memory_service=None) -> None:
    """初始化全局 PromptBuilder（在应用启动时调用）"""
    global _prompt_builder
    _prompt_builder = PromptBuilder(
        session_memory_service=session_memory_service,
        user_memory_service=user_memory_service,
    )
    logger.info("✅ PromptBuilder 初始化完成")


def get_prompt_builder() -> Optional[PromptBuilder]:
    """获取全局 PromptBuilder 实例"""
    return _prompt_builder

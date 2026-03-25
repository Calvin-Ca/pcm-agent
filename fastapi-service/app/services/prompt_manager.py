"""
Prompt Manager - 基于 LangChain PromptTemplate 的 Prompt 管理模块

从 app/prompts/ 目录加载 YAML 文件，支持变量替换和热重载。

用法：
    from app.services.prompt_manager import get_prompt_manager

    pm = get_prompt_manager()
    text = pm.format("system")                           # 无变量
    text = pm.format("intent_classify", message="...", history_section="", tools_desc="...")
    text = pm.get_str("param_extract_system")            # 读取 YAML 中的字符串字段
    template = pm.get_chat_template("rag")               # 获取 ChatPromptTemplate 对象
"""

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class PromptManager:
    """
    Prompt 管理器。

    - 启动时加载 prompts/ 目录下所有 YAML
    - format(name, **vars) 返回格式化后的字符串
    - get_chat_template(name) 返回 ChatPromptTemplate（用于 LCEL chain）
    - get_str(name) 读取 YAML 中的顶层字符串字段（如 system prompt）
    - reload() 手动重新加载所有文件（热重载钩子调用此方法）
    """

    def __init__(self, prompts_dir: Path = _PROMPTS_DIR):
        self._dir = prompts_dir
        self._lock = threading.Lock()
        self._raw: Dict[str, Dict] = {}        # YAML 原始内容，按文件名（不含扩展名）
        self._templates: Dict[str, Any] = {}   # 缓存的 LangChain template 对象
        self._load_all()
        self._start_watcher()

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def format(self, name: str, **kwargs: Any) -> str:
        """
        格式化指定 prompt，返回字符串。

        name 对应 prompts/<name>.yaml 文件（不含扩展名）。
        kwargs 为模板变量。
        """
        template = self._get_template(name)
        if template is None:
            logger.warning(f"Prompt '{name}' 不存在，返回空字符串")
            return ""
        try:
            if isinstance(template, ChatPromptTemplate):
                # ChatPromptTemplate → 取 system message 文本
                messages = template.format_messages(**kwargs)
                return "\n\n".join(m.content for m in messages)
            elif isinstance(template, PromptTemplate):
                return template.format(**kwargs)
        except KeyError as e:
            logger.error(f"Prompt '{name}' 缺少变量 {e}，kwargs={list(kwargs.keys())}")
        return ""

    def get_chat_template(self, name: str) -> Optional[ChatPromptTemplate]:
        """返回 ChatPromptTemplate 对象，供 LCEL chain 直接使用。"""
        template = self._get_template(name)
        if isinstance(template, ChatPromptTemplate):
            return template
        logger.warning(f"Prompt '{name}' 不是 chat 类型")
        return None

    def get_str(self, field: str, file: Optional[str] = None) -> str:
        """
        读取 YAML 中的顶层字符串字段。

        示例：pm.get_str("param_extract_system", file="param_extract")
              pm.get_str("weekly_report_system", file="weekly_report")

        若 file 为 None，则在所有已加载文件中查找第一个匹配的字段。
        """
        with self._lock:
            if file:
                return self._raw.get(file, {}).get(field, "")
            for raw in self._raw.values():
                if field in raw:
                    return raw[field]
        return ""

    def reload(self) -> None:
        """手动触发重新加载所有 YAML 文件。"""
        self._load_all()
        logger.info("Prompt 配置已热重载")

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        """加载 prompts/ 目录下所有 .yaml 文件。"""
        if not self._dir.exists():
            logger.warning(f"prompts 目录不存在: {self._dir}")
            return

        new_raw: Dict[str, Dict] = {}
        new_templates: Dict[str, Any] = {}

        for yaml_file in self._dir.glob("*.yaml"):
            name = yaml_file.stem
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    continue
                new_raw[name] = data
                tmpl = self._parse_template(data)
                if tmpl is not None:
                    new_templates[name] = tmpl
                logger.debug(f"已加载 prompt: {name}")
            except Exception as e:
                logger.error(f"加载 prompt 文件 {yaml_file.name} 失败: {e}")

        with self._lock:
            self._raw = new_raw
            self._templates = new_templates

        logger.info(f"Prompt 管理器已加载 {len(new_templates)} 个模板: {list(new_templates.keys())}")

    def _parse_template(self, data: dict) -> Optional[Any]:
        """根据 YAML 内容解析为对应的 LangChain template 对象。"""
        prompt_type = data.get("_type", "prompt")

        if prompt_type == "chat":
            messages_data = data.get("messages", [])
            messages = []
            for m in messages_data:
                role = m.get("role", "human")
                content = m.get("content", "")
                if role == "system":
                    messages.append(("system", content))
                elif role == "human":
                    messages.append(("human", content))
                elif role == "ai":
                    messages.append(("ai", content))
            if messages:
                return ChatPromptTemplate.from_messages(messages)

        elif prompt_type == "prompt":
            template_str = data.get("template", "")
            input_vars = data.get("input_variables", [])
            if template_str:
                return PromptTemplate(
                    template=template_str.rstrip("\n"),
                    input_variables=input_vars,
                )

        return None

    def _get_template(self, name: str) -> Optional[Any]:
        with self._lock:
            return self._templates.get(name)

    def _start_watcher(self) -> None:
        """尝试启动 watchdog 文件监听，失败时静默跳过（watchdog 为可选依赖）。"""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            manager = self

            class _Handler(FileSystemEventHandler):
                def on_modified(self, event):
                    if not event.is_directory and event.src_path.endswith(".yaml"):
                        logger.info(f"检测到 prompt 文件变更: {event.src_path}，触发热重载")
                        manager.reload()

                on_created = on_modified
                on_deleted = on_modified

            observer = Observer()
            observer.schedule(_Handler(), str(self._dir), recursive=False)
            observer.daemon = True
            observer.start()
            logger.info("Prompt 热重载监听已启动")
        except ImportError:
            logger.info("watchdog 未安装，跳过 prompt 热重载（pip install watchdog 可启用）")
        except Exception as e:
            logger.warning(f"启动 prompt 热重载监听失败: {e}")


# ── 全局单例 ──────────────────────────────────────────────────────────────────

_instance: Optional[PromptManager] = None
_init_lock = threading.Lock()


def get_prompt_manager() -> PromptManager:
    """获取全局 PromptManager 单例（延迟初始化）。"""
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = PromptManager()
    return _instance

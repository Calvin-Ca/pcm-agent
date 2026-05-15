import os
import pytest
from app.services.llm_client import LLMClient, get_planner_llm_client


def test_planner_factory_falls_back_to_chat_when_planner_unset(monkeypatch):
    monkeypatch.delenv("PLANNER_LLM_API_KEY", raising=False)
    monkeypatch.setenv("CHAT_LLM_API_KEY", "chat-key")
    monkeypatch.setenv("CHAT_LLM_API_BASE", "http://chat-base/v1")
    monkeypatch.setenv("CHAT_LLM_MODEL", "qwen3-8b")
    client = get_planner_llm_client()
    assert isinstance(client, LLMClient)
    assert client.api_key == "chat-key"
    assert client.model == "qwen3-8b"


def test_planner_factory_uses_planner_when_set(monkeypatch):
    monkeypatch.setenv("PLANNER_LLM_API_KEY", "planner-key")
    monkeypatch.setenv("PLANNER_LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("PLANNER_LLM_MODEL", "qwen-plus")
    client = get_planner_llm_client()
    assert client.api_key == "planner-key"
    assert client.model == "qwen-plus"
    assert "dashscope" in client.api_base

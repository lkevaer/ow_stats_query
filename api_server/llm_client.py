from __future__ import annotations

import json
from typing import Any, Dict, Optional


class LLMClient:
    """直接调用 OpenAI 兼容接口的 LLM 客户端"""

    def __init__(
        self,
        *,
        enabled: bool = False,
        provider: str = "openai",
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        custom_persona_prompt: str = "",
        timeout_seconds: int = 60,
        http_client=None,
    ) -> None:
        self.enabled = bool(enabled and api_key) if enabled else False
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.custom_persona_prompt = custom_persona_prompt or (
            "你是一位冷静专业的守望先锋电竞分析师。请用简短而有针对性的点评，从数据驱动地点评玩家本\\u5bf9"
            "这局对局的表现。用简体中文输出。"
        )
        self.timeout_seconds = int(timeout_seconds or 60)
        self._httpx_client = http_client

    async def _get_client(self):
        if self._httpx_client is not None:
            return self._httpx_client
        import httpx

        return httpx.AsyncClient(timeout=self.timeout_seconds)

    def _messages_for(self, user_content: str, system_prompt: Optional[str] = None) -> list[Dict[str, Any]]:
        messages: list[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})
        return messages

    async def chat(self, user_content: str, *, system_prompt: Optional[str] = None) -> Optional[str]:
        if not self.enabled:
            return None
        messages = self._messages_for(user_content, system_prompt or self.custom_persona_prompt)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        client = await self._get_client()
        try:
            if hasattr(client, "post") and hasattr(client, "__aenter__"):
                resp = await client.post(url, headers=headers, json=payload)
            else:
                resp = client.post(url, headers=headers, json=payload)
            if hasattr(resp, "status_code") and resp.status_code >= 400:
                text = getattr(resp, "text", "") if hasattr(resp, "text") else ""
                raise RuntimeError(f"LLM call failed: {resp.status_code} {text}")
            data = resp.json() if hasattr(resp, "json") else json.loads(resp.text)
            choices = data.get("choices") or []
            if not choices:
                return ""
            first = choices[0]
            msg = first.get("message") if isinstance(first, dict) else None
            if isinstance(msg, dict):
                return str(msg.get("content") or "")
            return str(first or "")
        except Exception as exc:
            # 降级：不抛出异常，返回 None，让上层决定如何处理
            return None

    async def translate_batch(self, texts, *, glossary=()) -> list[str]:
        """补丁翻译：批量翻译一组文本。未配置则返回原文。"""
        if not self.enabled:
            return [str(t) for t in texts]
        user_content = (
            "请把以下守望先锋补丁说明翻译成简体中文，术语保持游戏术语一致性。"
            "只输出 JSON 数组格式的翻译结果数组，不要其他文字。\n\n"
            + json.dumps([str(t) for t in texts], ensure_ascii=False)
        )
        result = await self.chat(user_content)
        if not result:
            return [str(t) for t in texts]
        try:
            parsed = json.loads(result)
            if isinstance(parsed, list) and len(parsed) == len(list(texts)):
                return [str(item) for item in parsed]
        except Exception:
            pass
        return [str(t) for t in texts]

    def render_persona_prompt_for_match(self, match_summary_text: str) -> str:
        return (
            f"{self.custom_persona_prompt}\n\n"
            f"--- 对局数据摘要 ---\n{match_summary_text}\n\n请给出点评。"
        )

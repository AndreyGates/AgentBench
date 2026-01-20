from src.client.agent import AgentClient
from langchain_gigachat import GigaChat
from typing import List
import os
import json

class IncidentAnalyzer(AgentClient):
    """Простейший адаптер GigaChat для AgentBench v0.2"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Инициализация GigaChat
        self.llm = GigaChat(
            model=kwargs['model'],
            credentials=kwargs['credentials'],
            scope=kwargs['scope'],
            temperature=kwargs['body']['temperature'],
            top_p=kwargs['body']['top_p'],
            max_tokens=kwargs['body']['max_tokens'],
            verify_ssl_certs=False,
            profanity_check=False
        )
    
    def inference(self, history: List[dict]) -> str:
        """
        Единственный публичный метод:
        history (v0.2 формат) -> plain text ответ
        """
        # Конвертируем v0.2 history в LangChain формат
        messages = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            messages.append({"role": role, "content": msg["content"]})
        
        # Вызов LLM
        try:
            response = self.llm.invoke(messages)
            return response.content
        
        except Exception as e:
            # Возвращаем ошибку в JSON (task worker обработает)
            return json.dumps({"error": str(e)})

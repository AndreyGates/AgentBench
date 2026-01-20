# ADAPTATION.md

## Цель адаптации

Этот форк AgentBench v0.2 расширен для **тестирования GigaChat-агентов на custom задачах кибербезопасности** с возможностью измерения специализированных метрик качества.

### Что добавлено:
- ✅ Поддержка GigaChat API (LangChain integration)
- ✅ Пример task + agent для анализа инцидентов безопасности

### Отличия от baseline AgentBench v0.2:
- **Baseline:** Оценка готовых LLM на стандартных задачах (dbbench, webshop)
- **Адаптация:** Разработка и тестирование специализированных агентов КБ на кастомных задачах

---

## Быстрый старт (пример: incident_analysis)

### Шаг 0: Установка зависимостей

```bash
# Базовые зависимости AgentBench
pip install -r requirements.txt
```


### Шаг 1: Запуск Task Server

```bash
# Запускает controller + все task workers
python -m src.start_task -a
# (см. configs/start_task.yaml для запуска нужных задач)
```

### Шаг 2: Запуск Assigner (evaluation)

```bash
# В новом терминале:
python -m src.assigner
# (см. configs/assignments/default.yaml для направления агента на нужную задачу)
```

### Шаг 3: Анализ результатов

Результаты решения задачи агентом сохраняются в папку <b>outputs/[LAST_TIMESTAMP]/[AGENT_NAME]/[TASK_NAME]</b>

**Отчет overall.json:**

```json
{
    "total": 3,
    "validation": {
        "running": 0.0,
        "completed": 0.3333333333333333,
        "agent context limit": 0.0,
        "agent validation failed": 0.3333333333333333,
        ...
    },
    "custom": {
        "accuracy": 0.333,
        "precision": 1.0,
        "recall": 1.0,
        "f1_score": 1.0,
        "avg_rounds_per_sample": 0.67,
        ...
    }
}
```


---

## Создание custom task (полная инструкция)

### 1. Подготовка датасета

**Структура:** `data/<task_name>/samples.jsonl`

Пример `data/incident_analysis/logs.jsonl`:

```json
{"id": 0, "incident_summary": "User 'admin' failed login 15 times", "logs": [...], "is_incident": true, "explanation": "Brute force attack"}
{"id": 1, "incident_summary": "API rate limit hit by user 'dev'", "logs": [...], "is_incident": false, "explanation": "Legitimate testing"}
```

**Требования:**

- Каждая строка = 1 sample (valid JSON)
- Обязательные поля: `id`, ground truth для валидации
- Опциональные: дополнительный контекст, метаданные


### 2. Реализация Task Worker

**Путь:** `src/server/tasks/<task_name>/task.py`

**Минимальный шаблон:**

```python
from src.server.task import Task, Session
from src.typings import TaskOutput, SampleStatus
from typing import List
import json

SYSTEM_PROMPT = """
[Описание задачи для LLM]
[Формат ожидаемых действий: Action: Operation / Action: Answer]
"""

class IncidentAnalysis(Task):
    def __init__(self, data_file: str, max_round: int = 5, **kwargs):
        super().__init__(**kwargs)
        self.data_file = data_file
        self.max_round = max_round
        
        # Загрузка датасета
        with open(data_file, "r") as f:
            self.samples = [json.loads(line) for line in f]
    
    def get_indices(self) -> List[int]:
        """Список всех sample indices"""
        return list(range(len(self.samples)))
    
    async def start_sample(self, index: int, session: Session) -> TaskOutput:
        """Выполнение одного sample"""
        sample = self.samples[index]
        
        # 1. Inject system prompt
        session.inject({"role": "user", "content": SYSTEM_PROMPT})
        session.inject({"role": "agent", "content": "Ok."})
        
        # 2. Inject вопрос
        session.inject({"role": "user", "content": sample["question"]})
        
        # 3. Multi-turn loop (опционально)
        rounds = 0
        while rounds < self.max_round:
            response = await session.action()
            agent_msg = response.content
            
            # Проверка финального ответа
            if "Action: Answer" in agent_msg:
                result = self._validate_answer(agent_msg, sample)
                return TaskOutput(
                    status=SampleStatus.COMPLETED,
                    result=result,
                    history=session.history
                )
            
            # Или выполнение операции
            elif "Action: Operation" in agent_msg:
                operation_result = self._execute_operation(agent_msg, sample)
                session.inject({"role": "user", "content": operation_result})
                rounds += 1
            
            else:
                return TaskOutput(
                    status=SampleStatus.AGENT_VALIDATION_FAILED,
                    result={"error": "invalid_format"},
                    history=session.history
                )
        
        # Max rounds exceeded
        return TaskOutput(
            status=SampleStatus.TASK_LIMIT_REACHED,
            result={"error": "timeout"},
            history=session.history
        )
    
    def _validate_answer(self, agent_msg: str, sample: dict) -> dict:
        """Парсинг и валидация ответа агента"""
        # TODO: Извлечь ответ из agent_msg
        # TODO: Сравнить с ground truth
        return {"is_correct": True/False, ...}
    
    def _execute_operation(self, agent_msg: str, sample: dict) -> str:
        """Выполнение операции агента (опционально для multi-turn)"""
        # TODO: Парсить Tool и Arguments
        # TODO: Вернуть результат операции
        return "Operation result: ..."
    
    def calculate_overall(self, results: List[TaskOutput]) -> dict:
        """Custom metrics для задачи"""
        total = len(results)
        correct = sum(1 for r in results if r.result.get("is_correct"))
        
        return {
            "accuracy": correct / total if total > 0 else 0,
            # Добавить свои метрики (precision, recall, F1 и т.д.)
        }
```


### 3. Конфигурация Task

**Путь:** `configs/tasks/<task_name>.yaml`

```yaml
default:
  module: src.server.tasks.incident_analysis.IncidentAnalysis
  parameters:
    concurrency: 1
    max_round: 5

incident_analysis:
  parameters:
    name: incident_analysis
    data_file: "data/incident_analysis/logs.jsonl"
```


### 4. Создание Agent

**Путь:** `src/client/agents/<agent_name>.py`

**Простой адаптер:**

```python
from src.client.agent import AgentClient
from langchain_gigachat import GigaChat
from typing import List
import os

class IncidentAnalyzer(AgentClient):
    def __init__(self, model: str = "GigaChat", **kwargs):
        super().__init__(**kwargs)
        
        self.llm = GigaChat(
            model=kwargs["model"],
            credentials=kwargs["credentials"],
            scope=kwargs["scope"],
            verify_ssl_certs=False,
            temperature=0.1  # Детерминизм для evaluation
        )
    
    def inference(self, history: List[dict]) -> str:
        """
        Единственный публичный метод.
        history: v0.2 format [{"role": "user/agent", "content": "..."}]
        returns: plain text ответ
        """
        # Конвертация в LangChain формат
        messages = [
            {"role": "user" if msg["role"] == "user" else "assistant",
             "content": msg["content"]}
            for msg in history
        ]
        
        # Вызов LLM
        response = self.llm.invoke(messages)
        return response.content
```

**Продвинутый (с внешним агентом):**

```python
from my_langgraph_agent import create_agent  # Ваш кастомный агент

class IncidentAnalyzer(AgentClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.agent = create_agent()  # LangGraph/CrewAI/etc
    
    def inference(self, history: List[dict]) -> str:
        messages = [(msg["role"], msg["content"]) for msg in history]
        result = self.agent.invoke({"messages": messages})
        return result["messages"][-1].content
```


### 5. Конфигурация Agent

**Путь:** `configs/agents/<agent_name>.yaml`

```yaml
module: src.client.agents.IncidentAnalyzer
parameters:
  url: https://gigachat.devices.sberbank.ru/api/v1
  model: GigaChat-2-Max
  body:
    temperature: 0.1
    top_p: 0.9
    max_tokens: 2048
  scope: GIGACHAT_API_PERS
  credentials: <placeholder for your key>
```


### 6. Конфигурация Assignment

**Путь:** `configs/assignments/<assignment_name>.yaml`

```yaml
import: definition.yaml

concurrency:
  task:
    incident_analysis: 1 # number of task workers for each task
  agent:
    incident_analyzer: 1 # number of task workers for each agent

assignments: # List[Assignment] | Assignment
  - agent: # "task": List[str] | str ,  "agent": List[str] | str
      - incident_analyzer
    task:
      - incident_analysis

output: "outputs/{TIMESTAMP}"
```


---

## Структура проекта (после адаптации)

```
AgentBench-v0.2/
├── data/
│   ├── incident_analysis/
│   │   └── logs.jsonl              # Датасет для incident_analysis
│   └── my_custom_task/
│       └── samples.jsonl           # Ваш датасет
│
├── src/
│   ├── server/tasks/
│   │   ├── incident_analysis/
│   │   │   └── task.py             # Task Worker (пример)
│   │   └── my_custom_task/
│   │       └── task.py             # Ваш Task Worker
│   │
│   └── client/agents/
│       ├── incident_analyzer.py    # GigaChat агент (пример)
│       └── my_custom_agent.py      # Ваш агент
│
├── configs/
│   ├── tasks/
│   │   ├── incident_analysis.yaml
│   │   └── my_custom_task.yaml
│   │
│   ├── agents/
│   │   └── incident_analyzer.yaml
│       └── my_custom_agent.py
│   │
│   └── assignments/
│       ├── default.yaml
│       └── my_assignment.yaml
│
├── outputs/                         # Результаты evaluation
│
├── README.md                        # Оригинальная документация v0.2
└── ADAPTATION.md                    # Текущий файл
```


---

## Custom Metrics (примеры)

### Для задач классификации (incident detection, malware analysis):

```python
def calculate_overall(self, results: List[TaskOutput]) -> dict:
    TP = FP = FN = TN = 0
    
    for r in results:
        pred = r.result.get("prediction")
        true = r.result.get("ground_truth")
        
        if pred and true: TP += 1
        elif pred and not true: FP += 1
        elif not pred and true: FN += 1
        else: TN += 1
    
    precision = TP / (TP + FP) if (TP + FP) > 0 else 0
    recall = TP / (TP + FN) if (TP + FN) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    return {
        "accuracy": (TP + TN) / len(results),
        "precision": precision,
        "recall": recall,
        "f1_score": f1
    }
```


### Для multi-turn задач (добавить efficiency):

```python
def calculate_overall(self, results: List[TaskOutput]) -> dict:
    # ... базовые метрики ...
    
    total_rounds = sum(r.result.get("rounds_used", 0) for r in results)
    avg_rounds = total_rounds / len(results)
    
    # Efficiency: правильные ответы с минимумом шагов
    efficiency = sum(
        1.0 if r.result.get("is_correct") and r.result.get("rounds_used", 0) <= 2 else 0.5
        for r in results
    ) / len(results)
    
    return {
        # ...
        "avg_rounds_per_sample": avg_rounds,
        "efficiency_score": efficiency
    }
```

---

## Дополнительные ресурсы

- **Оригинальная документация v0.2:** [README.md](README.md)
- **Extension Guide:** https://github.com/THUDM/AgentBench/blob/v0.2/docs/Extension_en.md
- **GigaChat API Docs:** https://developers.sber.ru/docs/ru/gigachat/api/overview

---
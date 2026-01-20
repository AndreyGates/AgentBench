import json
import re
from typing import List, Dict, Any
from src.server.task import Task, Session
from src.typings import TaskOutput, SampleStatus, AgentOutputStatus

SYSTEM_PROMPT = """You are a cybersecurity analyst investigating potential security incidents.

GOAL: Determine if the incident is real or a false positive. Be EFFICIENT - only request additional data if truly needed.

INCIDENT SUMMARY will provide initial context. Analyze it first before requesting more data.

LOG SCHEMA (for operations):
- time: timestamp (string)
- event: event type (string)  
- ip: source IP address (string)
- user: username (string, optional)
- path: URL path (string, optional)
- status: HTTP status (int, optional)
- payload: attack payload (string, optional)

AVAILABLE OPERATIONS (use sparingly):

1. filter_logs(field, value)
   Example: filter_logs("ip", "203.0.113.50")
   Use when: Need to examine specific IP/user/event pattern

2. get_user_history(username)
   Example: get_user_history("admin")
   Use when: Need full timeline of user actions

3. check_pattern(attack_type)
   Patterns: "brute_force", "sql_injection", "privilege_escalation"
   Example: check_pattern("sql_injection")
   Use when: Suspect specific attack but need confirmation

RESPONSE FORMAT:

If you need more data:
Action: Operation
Tool: <tool_name>
Arguments: {"arg1": "value1"}
Reasoning: <why you need this data>

If you can decide now:
Action: Answer
Is Incident: [true/false]
Severity: [low/medium/high/critical]
Explanation: [your reasoning based on available evidence]

DECISION HEURISTICS:
✓ If summary clearly indicates attack (e.g., "SQL injection attempt", "privilege escalation") → Answer immediately
✓ If ambiguous (e.g., "multiple API calls") → Request 1-2 operations max
✗ Don't request data you won't use for the decision

Be decisive. Security teams value speed AND accuracy.
"""


class IncidentAnalysis(Task):
    def __init__(self, data_file: str = "data/incident_analysis/logs.jsonl", max_round: int = 5, **kwargs):
        super().__init__(**kwargs)
        self.data_file = data_file
        self.max_round = max_round
        self.samples = []
        
        with open(data_file, "r", encoding="utf-8") as f:
            for line in f:
                self.samples.append(json.loads(line))
    
    def get_indices(self) -> List[int]:
        return list(range(len(self.samples)))
    
    async def start_sample(self, index: int, session: Session) -> TaskOutput:
        sample = self.samples[index]
        
        # 1. Inject system prompt
        session.inject({"role": "user", "content": SYSTEM_PROMPT})
        session.inject({"role": "agent", "content": "Ok."})
        
        # 2. Inject incident summary
        session.inject({
            "role": "user",
            "content": f"Incident Summary:\n{sample['incident_summary']}\nTimestamp: {sample['timestamp']}"
        })
        
        # 3. Multi-turn dialogue loop
        finish_reason = SampleStatus.COMPLETED
        final_answer = None
        rounds = 0
        
        try:
            response = await session.action()
            res = response.content or ""
            
            while rounds < self.max_round:
                # Проверяем тип действия
                action_match = re.search(r"Action: (.*?)\n", res)
                
                if not action_match:
                    finish_reason = SampleStatus.AGENT_VALIDATION_FAILED
                    break
                
                action_type = action_match.group(1).strip()
                
                # ФИНАЛЬНЫЙ ОТВЕТ
                if action_type == "Answer":
                    final_answer = self._parse_final_answer(res)
                    if not final_answer:
                        finish_reason = SampleStatus.AGENT_VALIDATION_FAILED
                    break
                
                # ОПЕРАЦИЯ (запрос данных)
                elif action_type == "Operation":
                    operation_result = self._execute_operation(res, sample)
                    
                    if operation_result.get("error"):
                        finish_reason = SampleStatus.AGENT_VALIDATION_FAILED
                        break
                    
                    # Возвращаем результат операции агенту
                    session.inject({
                        "role": "user",
                        "content": f"Operation Result:\n{operation_result['data']}"
                    })
                    
                    # Следующий шаг
                    response = await session.action()
                    
                    if response.status == AgentOutputStatus.AGENT_CONTEXT_LIMIT:
                        finish_reason = SampleStatus.AGENT_CONTEXT_LIMIT
                        break
                    
                    res = response.content
                    rounds += 1
                
                else:
                    finish_reason = SampleStatus.AGENT_VALIDATION_FAILED
                    break
            
            # Max rounds exceeded
            if rounds >= self.max_round and not final_answer:
                finish_reason = SampleStatus.TASK_LIMIT_REACHED
        
        except Exception as e:
            finish_reason = SampleStatus.UNKNOWN
            final_answer = {"error": str(e)}
        
        # Валидация финального ответа
        if final_answer and not final_answer.get("error"):
            is_correct = (final_answer["is_incident"] == sample["is_incident"])
        else:
            is_correct = False
        
        return TaskOutput(
            status=finish_reason,
            result={
                "is_correct": is_correct,
                "agent_answer": final_answer,
                "ground_truth": {
                    "is_incident": sample["is_incident"],
                    "explanation": sample["explanation"]
                },
                "rounds_used": rounds
            },
            history=session.history
        )
    
    def _execute_operation(self, agent_msg: str, sample: dict) -> dict:
        """Выполняет операцию агента (filter_logs, get_user_history и т.д.)"""
        # Парсим Tool и Arguments
        tool_match = re.search(r"Tool: (.*?)\n", agent_msg)
        args_match = re.search(r"Arguments: ({.*?})", agent_msg, re.DOTALL)
        
        if not tool_match or not args_match:
            return {"error": "Invalid operation format"}
        
        tool_name = tool_match.group(1).strip()
        try:
            arguments = json.loads(args_match.group(1))
        except:
            return {"error": "Invalid JSON in Arguments"}
        
        logs = sample["logs"]
        
        # filter_logs
        if tool_name == "filter_logs":
            field = arguments.get("field")
            value = arguments.get("value")
            
            filtered = [log for log in logs if log.get(field) == value]
            return {"data": json.dumps(filtered, indent=2)}
        
        # get_user_history
        elif tool_name == "get_user_history":
            username = arguments.get("username")
            user_logs = [log for log in logs if log.get("user") == username]
            return {"data": json.dumps(user_logs, indent=2)}
        
        # check_pattern
        elif tool_name == "check_pattern":
            attack_type = arguments.get("attack_type")
            
            # Простые правила детекции
            patterns = {
                "brute_force": lambda: len([l for l in logs if l.get("event") == "failed_login"]) > 5,
                "sql_injection": lambda: any("sql_injection" in l.get("event", "") for l in logs),
                "privilege_escalation": lambda: any(l.get("event") == "privilege_escalation" for l in logs)
            }
            
            match = patterns.get(attack_type, lambda: False)()
            return {"data": f"Pattern '{attack_type}': {'MATCH' if match else 'NO MATCH'}"}
        
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    
    def _parse_final_answer(self, agent_msg: str) -> dict:
        """Парсит финальный ответ агента"""
        
        # Извлекаем Is Incident
        incident_match = re.search(r"Is Incident:\s*(true|false)", agent_msg, re.IGNORECASE)
        if not incident_match:
            return None
        
        is_incident = incident_match.group(1).lower() == "true"
        
        # Опционально: severity
        severity_match = re.search(r"Severity:\s*(\w+)", agent_msg, re.IGNORECASE)
        severity = severity_match.group(1) if severity_match else "unknown"
        
        # Опционально: explanation
        expl_match = re.search(r"Explanation:\s*(.+)", agent_msg, re.DOTALL)
        explanation = expl_match.group(1).strip() if expl_match else ""
        
        return {
            "is_incident": is_incident,
            "severity": severity,
            "explanation": explanation
        }
    
    def calculate_overall(self, results: List[TaskOutput]) -> Dict[str, Any]:
        """Custom metrics для security analysis"""
        total = len(results)
        correct = 0
        
        # Для incident detection
        true_positives = 0
        false_positives = 0
        false_negatives = 0
        true_negatives = 0
        
        total_rounds = 0
        
        for task_output in results:
            if task_output.status != SampleStatus.COMPLETED:
                continue
            
            result = task_output.result
            
            if result.get("is_correct"):
                correct += 1
            
            total_rounds += result.get("rounds_used", 0)
            
            # Confusion matrix
            agent_ans = result.get("agent_answer", {})
            gt = result.get("ground_truth", {})
            
            pred = agent_ans.get("is_incident")
            true_val = gt.get("is_incident")
            
            if pred is True and true_val is True:
                true_positives += 1
            elif pred is True and true_val is False:
                false_positives += 1
            elif pred is False and true_val is True:
                false_negatives += 1
            elif pred is False and true_val is False:
                true_negatives += 1
        
        accuracy = correct / total if total > 0 else 0
        precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
        recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        
        avg_rounds = total_rounds / total if total > 0 else 0
        
        return {
            "accuracy": round(accuracy, 3),
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1_score": round(f1, 3),
            "avg_rounds_per_sample": round(avg_rounds, 2),
            "total_samples": total,
            "correct": correct,
            "confusion_matrix": {
                "TP": true_positives,
                "FP": false_positives,
                "FN": false_negatives,
                "TN": true_negatives
            }
        }

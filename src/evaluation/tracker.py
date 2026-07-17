from datetime import datetime
from typing import List, Dict, Optional
from collections import defaultdict
import threading

class WorkflowEvaluationTracker:
    """AI工作流评估追踪器 — 记录每次工作流运行的质量指标"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._records = []
                    cls._instance._lock = threading.Lock()
        return cls._instance
    
    def record(self, candidate_id: int, scores: Dict, decision: str, duration_ms: float):
        """记录一次工作流评估"""
        with self._lock:
            self._records.append({
                "candidate_id": candidate_id,
                "overall_score": scores.get("overall_score", 0),
                "skill_match_score": scores.get("skill_match_score", 0),
                "experience_match_score": scores.get("experience_match_score", 0),
                "education_match_score": scores.get("education_match_score", 0),
                "culture_match_score": scores.get("culture_match_score", 0),
                "decision": decision,
                "duration_ms": duration_ms,
                "timestamp": datetime.utcnow().isoformat()
            })
            # 保留最近1000条
            if len(self._records) > 1000:
                self._records = self._records[-1000:]
    
    def stats(self) -> Dict:
        """获取统计摘要"""
        with self._lock:
            if not self._records:
                return {"total": 0, "message": "暂无评估记录"}
            
            total = len(self._records)
            avg_overall = sum(r["overall_score"] for r in self._records) / total
            avg_skill = sum(r["skill_match_score"] for r in self._records) / total
            avg_duration = sum(r["duration_ms"] for r in self._records) / total
            
            decisions = defaultdict(int)
            for r in self._records:
                decisions[r["decision"]] += 1
            
            return {
                "total_evaluations": total,
                "avg_overall_score": round(avg_overall, 1),
                "avg_skill_match_score": round(avg_skill, 1),
                "avg_duration_ms": round(avg_duration, 0),
                "decision_distribution": dict(decisions),
                "latest_evaluation": self._records[-1] if self._records else None
            }
    
    def get_records(self, limit: int = 20) -> List[Dict]:
        """获取最近的评估记录"""
        with self._lock:
            return self._records[-limit:]

# 全局单例
evaluation_tracker = WorkflowEvaluationTracker()

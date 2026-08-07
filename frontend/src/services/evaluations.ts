import { apiRequest } from './api';
import type { Evaluation } from '../types';

/** 维度聚合统计（前端根据评估记录实时计算） */
export interface DimensionStat {
  name: string;
  avg: number;
  count: number;
}

/** 工作流评估追踪器统计（后端内存单例） */
export interface EvaluationStats {
  total_evaluations: number;
  avg_overall_score: number;
  avg_skill_match_score: number;
  avg_duration_ms: number;
  decision_distribution: Record<string, number>;
  latest_evaluation: Record<string, any> | null;
}

/** 单个候选人评估汇总 */
export interface EvaluationSummary {
  candidate_id: number;
  total_evaluations: number;
  average_score: number;
  dimensions: Record<string, { count: number; average: number; min: number; max: number }>;
}

export const getEvaluations = async (params?: {
  candidate_id?: number;
  dimension?: string;
}): Promise<Evaluation[]> => {
  return apiRequest<Evaluation[]>('/evaluations/', { params });
};

export const getEvaluationSummary = async (candidateId: number): Promise<EvaluationSummary> => {
  return apiRequest<EvaluationSummary>(`/evaluations/candidate/${candidateId}/summary`);
};

export const getEvaluationStats = async (): Promise<EvaluationStats> => {
  return apiRequest<EvaluationStats>('/evaluations/stats');
};

export const getEvaluationRecords = async (limit: number = 20): Promise<Record<string, any>[]> => {
  return apiRequest<Record<string, any>[]>(`/evaluations/stats/records`, { params: { limit } });
};

/** 根据评估记录按维度聚合计分 */
export const computeDimensionStats = (evaluations: Evaluation[]): DimensionStat[] => {
  const map = new Map<string, { total: number; count: number }>();
  for (const e of evaluations) {
    const cur = map.get(e.dimension) || { total: 0, count: 0 };
    cur.total += e.score;
    cur.count += 1;
    map.set(e.dimension, cur);
  }
  return Array.from(map.entries()).map(([name, { total, count }]) => ({
    name,
    avg: Math.round((total / count) * 10) / 10,
    count,
  }));
};

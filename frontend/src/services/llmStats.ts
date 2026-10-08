import { apiRequest } from './api';

export interface LlmSiteStat {
  call_site: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  avg_latency_ms: number;
  max_latency_ms: number;
  failed: number;
  degraded: number;
  usage_missing: number;
  degraded_rate: number;
}

export interface LlmBudgetState {
  enabled: boolean;
  action: string;
  period: string;
  limit_usd: number;
  pricing_configured: boolean;
}

export interface LlmStats {
  days: number;
  totals: {
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    avg_cost_per_call_usd: number;
    failed: number;
    degraded: number;
    failed_rate: number;
  };
  by_site: LlmSiteStat[];
  daily: { date: string; calls: number; cost_usd: number }[];
  budget: LlmBudgetState;
  log_retention_days: number;
}

export interface LlmCallRow {
  id: number;
  created_at: string | null;
  call_site: string;
  candidate_id: number | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  status: string;
  degraded: boolean;
  usage_missing: boolean;
  prompt_hash: string;
  error: string | null;
}

export const getLlmStats = async (days = 7): Promise<LlmStats> => {
  return apiRequest<LlmStats>('/llm-stats/summary', { params: { days } });
};

export const getRecentLlmCalls = async (limit = 50, callSite?: string): Promise<LlmCallRow[]> => {
  return apiRequest<LlmCallRow[]>('/llm-stats/calls', {
    params: { limit, call_site: callSite },
  });
};
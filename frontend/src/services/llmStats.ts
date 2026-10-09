import { apiRequest } from './api';

export interface LlmSiteStat {
  call_site: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  /** 当前计价币种下的花费 */
  cost: number;
  /** 未计入 cost 的金额（其它币种 + 币种未知的历史行） */
  excluded_cost: number;
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
  /** 上限金额，币种见 currency（等于模型配置里的计价币种，不固定为美元） */
  limit: number;
  currency: string;
  pricing_configured: boolean;
  model: string;
  config_source: string;
  is_peak_now: boolean;
}

export interface LlmStats {
  days: number;
  currency: string;
  totals: {
    calls: number;
    input_tokens: number;
    output_tokens: number;
    /** 只含 currency 对应的花费；跨币种金额绝不混算 */
    cost: number;
    avg_cost_per_call: number;
    /** 已知的其它币种花费（未计入 cost） */
    foreign_cost: number;
    /** 币种未知的历史行花费（未计入 cost） */
    unattributed_cost: number;
    /** 上面两项之和，便于界面一句提示 */
    excluded_cost: number;
    foreign_currencies: string[];
    failed: number;
    degraded: number;
    failed_rate: number;
  };
  by_site: LlmSiteStat[];
  daily: { date: string; calls: number; cost: number }[];
  budget: LlmBudgetState;
  log_retention_days: number;
}

export interface LlmCallRow {
  id: number;
  created_at: string | null;
  call_site: string;
  candidate_id: number | null;
  /** 所属工作流运行；非工作流链路（知识库问答/JD 解析）为 null */
  thread_id: string | null;
  model: string | null;
  /** 供应商实际服务的模型版本（可能不同于请求名） */
  model_served: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost: number | null;
  currency: string | null;
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

export const getRecentLlmCalls = async (
  limit = 50,
  callSite?: string,
  threadId?: string,
): Promise<LlmCallRow[]> => {
  return apiRequest<LlmCallRow[]>('/llm-stats/calls', {
    params: { limit, call_site: callSite, thread_id: threadId },
  });
};
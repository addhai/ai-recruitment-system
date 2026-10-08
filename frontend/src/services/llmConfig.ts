import { apiRequest } from './api';

export interface LLMConfigView {
  model: string;
  base_url: string;
  api_key_masked: string;
  api_key_configured: boolean;
  input_price: number;
  output_price: number;
  currency: string;
  peak_multiplier: number;
  temperature: number;
  timeout_seconds: number;
  source: 'database' | 'env';
  pricing_configured: boolean;
  is_peak_now: boolean;
}

export interface LLMPreset {
  label: string;
  base_url: string;
  input_price: number;
  output_price: number;
  currency: string;
  peak_multiplier: number;
  temperature: number;
  timeout_seconds: number;
  note?: string;
}

export interface LLMConfigUpdate {
  model?: string;
  base_url?: string;
  /** 不传=不修改；传空字符串=清空并回退 .env */
  api_key?: string;
  input_price?: number;
  output_price?: number;
  currency?: string;
  peak_multiplier?: number;
  temperature?: number;
  timeout_seconds?: number;
}

export interface LLMTestResult {
  ok: boolean;
  model?: string;
  error?: string;
  reply_preview?: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
}

export const getLLMConfig = async (): Promise<LLMConfigView> => {
  return apiRequest<LLMConfigView>('/llm-config');
};

export const updateLLMConfig = async (data: LLMConfigUpdate): Promise<LLMConfigView> => {
  return apiRequest<LLMConfigView>('/llm-config', { method: 'PUT', body: data });
};

export const getLLMPresets = async (): Promise<{ presets: Record<string, LLMPreset>; note: string }> => {
  return apiRequest('/llm-config/presets');
};

/** 发一次最小调用验证模型名/密钥/Base URL 是否可用 */
export const testLLMConfig = async (): Promise<LLMTestResult> => {
  return apiRequest<LLMTestResult>('/llm-config/test', { method: 'POST' });
};
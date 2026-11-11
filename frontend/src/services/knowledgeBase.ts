import { apiRequest } from './api';

export interface KnowledgeBaseDocument {
  title: string;
  content: string;
}

export interface QueryResult {
  answer: string;
  sources: { title: string; content?: string }[];
  // mode 如实反映**哪几路真正参与了检索**，而不是"向量库是否可用"：
  // hybrid_rag = 关键词与向量都进了上下文；bm25_llm = 只有关键词；
  // vector_llm = 只有向量；fallback = 走规则模板；blocked = 安全拦截。
  mode?: 'hybrid_rag' | 'bm25_llm' | 'vector_llm' | 'fallback' | 'blocked' | 'budget_exhausted';
}

export const queryKnowledgeBase = async (query: string): Promise<QueryResult> => {
  return apiRequest<QueryResult>('/knowledge-base/query', {
    method: 'POST',
    body: { query },
  });
};

export const getDocuments = async (): Promise<KnowledgeBaseDocument[]> => {
  return apiRequest<KnowledgeBaseDocument[]>('/knowledge-base/documents');
};

export const addDocument = async (title: string, content: string): Promise<void> => {
  return apiRequest<void>('/knowledge-base/documents', {
    method: 'POST',
    body: { title, content },
  });
};

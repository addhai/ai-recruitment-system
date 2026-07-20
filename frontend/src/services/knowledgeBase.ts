import { apiRequest } from './api';

export interface KnowledgeBaseDocument {
  title: string;
  content: string;
}

export interface QueryResult {
  answer: string;
  sources: { title: string; content?: string }[];
}

export const queryKnowledgeBase = async (query: string): Promise<QueryResult> => {
  return apiRequest<QueryResult>('/knowledge-base/query', {
    method: 'POST',
    params: { query },
  });
};

export const getDocuments = async (): Promise<KnowledgeBaseDocument[]> => {
  return apiRequest<KnowledgeBaseDocument[]>('/knowledge-base/documents');
};

export const addDocument = async (title: string, content: string): Promise<void> => {
  return apiRequest<void>('/knowledge-base/documents', {
    method: 'POST',
    params: { title, content },
  });
};

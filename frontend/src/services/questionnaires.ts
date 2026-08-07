import { apiRequest } from './api';

export interface Question {
  id: number;
  text: string;
  type: string;
  options?: string[];
}

export interface QuestionnaireListItem {
  id: number;
  name: string;
  type: string;
  question_count: number;
  created_at: string;
}

export interface QuestionnaireDetail extends QuestionnaireListItem {
  questions: Question[];
}

export const getQuestionnaires = async (): Promise<QuestionnaireListItem[]> => {
  return apiRequest<QuestionnaireListItem[]>('/questionnaires/');
};

export const getQuestionnaire = async (id: number): Promise<QuestionnaireDetail> => {
  return apiRequest<QuestionnaireDetail>(`/questionnaires/${id}`);
};

export const createQuestionnaire = async (data: {
  name: string;
  type: string;
  questions: Question[];
}): Promise<QuestionnaireDetail> => {
  return apiRequest<QuestionnaireDetail>('/questionnaires/', { method: 'POST', body: data });
};

export const deleteQuestionnaire = async (id: number): Promise<void> => {
  return apiRequest<void>(`/questionnaires/${id}`, { method: 'DELETE' });
};

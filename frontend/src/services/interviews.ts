import { apiRequest } from './api';
import type { Interview } from '../types';

export const getInterviews = async (params?: {
  skip?: number;
  limit?: number;
  candidate_id?: number;
  status?: string;
}): Promise<Interview[]> => {
  return apiRequest<Interview[]>('/interviews/', { params });
};

export const getInterview = async (id: number): Promise<Interview> => {
  return apiRequest<Interview>(`/interviews/${id}`);
};

export const createInterview = async (data: {
  candidate_id: number;
  position: string;
  round?: number;
  interviewer_id?: number;
  scheduled_at?: string;
}): Promise<Interview> => {
  return apiRequest<Interview>('/interviews/', {
    method: 'POST',
    body: data,
  });
};

export const updateInterview = async (
  id: number,
  data: Partial<Interview>
): Promise<Interview> => {
  return apiRequest<Interview>(`/interviews/${id}`, {
    method: 'PUT',
    body: data,
  });
};

export const completeInterview = async (
  id: number,
  data: { score: number; feedback: string; notes?: string }
): Promise<Interview> => {
  const params = new URLSearchParams();
  params.append('score', String(data.score));
  params.append('feedback', data.feedback);
  if (data.notes) params.append('notes', data.notes);
  
  return apiRequest<Interview>(`/interviews/${id}/complete?${params.toString()}`, {
    method: 'POST',
  });
};

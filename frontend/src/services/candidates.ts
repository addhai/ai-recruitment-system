import { apiRequest } from './api';
import type { Candidate } from '../types';

export const getCandidates = async (params?: {
  skip?: number;
  limit?: number;
  status?: string;
  position?: string;
  search?: string;
}): Promise<Candidate[]> => {
  return apiRequest<Candidate[]>('/candidates/', { params });
};

export const getCandidate = async (id: number): Promise<Candidate> => {
  return apiRequest<Candidate>(`/candidates/${id}`);
};

export const createCandidate = async (data: {
  name: string;
  email?: string;
  phone?: string;
  source?: string;
  position?: string;
}): Promise<Candidate> => {
  return apiRequest<Candidate>('/candidates/', {
    method: 'POST',
    body: data,
  });
};

export const updateCandidate = async (
  id: number,
  data: Partial<Candidate>
): Promise<Candidate> => {
  return apiRequest<Candidate>(`/candidates/${id}`, {
    method: 'PUT',
    body: data,
  });
};

export const deleteCandidate = async (id: number): Promise<void> => {
  return apiRequest<void>(`/candidates/${id}`, { method: 'DELETE' });
};

export const runWorkflow = async (id: number, positionRequirements: string): Promise<any> => {
  return apiRequest<any>(`/candidates/${id}/run-workflow?position_requirements=${encodeURIComponent(positionRequirements)}`, {
    method: 'POST',
  });
};

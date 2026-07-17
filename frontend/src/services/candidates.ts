import { apiRequest, API_BASE_URL } from './api';
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
  return apiRequest<any>(`/candidates/${id}/run-workflow`, {
    method: 'POST',
    body: { position_requirements: positionRequirements },
  });
};

export interface ResumeData {
  candidate_id: number;
  file_name: string;
  parsed_text: string;
  parsed_data: any;
  skills: string[] | null;
  experience: string | null;
  education: string | null;
}

export const getResume = async (id: number): Promise<any> => {
  return apiRequest<any>(`/candidates/${id}/resume`);
};

export const uploadResume = async (id: number, file: File): Promise<ResumeData> => {
  const token = localStorage.getItem('token');
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE_URL}/candidates/${id}/upload-resume`, {
    method: 'POST',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '简历上传失败' }));
    throw new Error(error.detail || `简历上传失败: ${response.status}`);
  }

  return response.json();
};

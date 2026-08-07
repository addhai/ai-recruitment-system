import { apiRequest } from './api';
import type { TalentPoolEntry } from '../types';

export const getTalentPool = async (params?: {
  status?: string;
  tag?: string;
}): Promise<TalentPoolEntry[]> => {
  return apiRequest<TalentPoolEntry[]>('/talent-pool/', { params });
};

export const addToTalentPool = async (data: {
  candidate_id: number;
  tags?: string[];
  notes?: string;
}): Promise<TalentPoolEntry> => {
  return apiRequest<TalentPoolEntry>('/talent-pool/', {
    method: 'POST',
    body: data,
  });
};

export const updateTalentPool = async (
  id: number,
  data: { status?: string; tags?: string[]; notes?: string }
): Promise<TalentPoolEntry> => {
  const params = new URLSearchParams();
  if (data.status) params.append('status', data.status);
  if (data.tags) data.tags.forEach((t) => params.append('tags', t));
  if (data.notes) params.append('notes', data.notes);
  return apiRequest<TalentPoolEntry>(`/talent-pool/${id}?${params.toString()}`, {
    method: 'PUT',
  });
};

export const removeFromTalentPool = async (id: number): Promise<void> => {
  return apiRequest<void>(`/talent-pool/${id}`, { method: 'DELETE' });
};

export const recordContact = async (id: number, notes?: string): Promise<TalentPoolEntry> => {
  const params = new URLSearchParams();
  if (notes) params.append('notes', notes);
  return apiRequest<TalentPoolEntry>(`/talent-pool/${id}/contact?${params.toString()}`, {
    method: 'POST',
  });
};

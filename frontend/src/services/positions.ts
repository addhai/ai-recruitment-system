import { apiRequest } from './api';
import type { Position } from '../types';

export const getPositions = async (params?: { status?: string }): Promise<Position[]> => {
  return apiRequest<Position[]>('/positions/', { params });
};

export const getPosition = async (id: number): Promise<Position> => {
  return apiRequest<Position>(`/positions/${id}`);
};

export const createPosition = async (data: {
  title: string;
  department?: string;
  location?: string;
  headcount?: number;
  description?: string;
}): Promise<Position> => {
  return apiRequest<Position>('/positions/', { method: 'POST', body: data });
};

export const updatePosition = async (
  id: number,
  data: Partial<Pick<Position, 'title' | 'department' | 'location' | 'headcount' | 'description' | 'status'>>
): Promise<Position> => {
  return apiRequest<Position>(`/positions/${id}`, { method: 'PUT', body: data });
};

/** 关闭招聘：岗位与其下 active JD 一并停用 */
export const closePosition = async (id: number): Promise<Position> => {
  return apiRequest<Position>(`/positions/${id}/close`, { method: 'POST' });
};

export const deletePosition = async (id: number): Promise<void> => {
  await apiRequest<void>(`/positions/${id}`, { method: 'DELETE' });
};
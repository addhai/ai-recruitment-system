import { apiRequest } from './api';
import type { DashboardStats, TrendData } from '../types';

export const getDashboardStats = async (): Promise<DashboardStats> => {
  return apiRequest<DashboardStats>('/dashboard/stats');
};

export const getRecentCandidates = async (limit = 10): Promise<any[]> => {
  return apiRequest<any[]>('/dashboard/recent-candidates', { params: { limit } });
};

export const getInterviewStats = async (): Promise<any> => {
  return apiRequest<any>('/dashboard/interview-stats');
};

export const getWeeklyTrend = async (): Promise<TrendData[]> => {
  return apiRequest<TrendData[]>('/dashboard/weekly-trend');
};

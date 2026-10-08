import { apiRequest, API_BASE_URL } from './api';
import type { JobDescription, JdProfile } from '../types';

export const getJobDescriptions = async (params?: {
  status?: string;
  position_id?: number;
}): Promise<JobDescription[]> => {
  return apiRequest<JobDescription[]>('/job_descriptions/', { params });
};

export const getJobDescription = async (id: number): Promise<JobDescription> => {
  return apiRequest<JobDescription>(`/job_descriptions/${id}`);
};

/** 在指定岗位下新建 JD：粘贴文本或上传文件（txt/pdf/docx/图片走 OCR） */
export const createJobDescription = async (data: {
  position_id: number;
  title: string;
  department?: string;
  raw_text?: string;
  file?: File | null;
}): Promise<JobDescription> => {
  const form = new FormData();
  form.append('position_id', String(data.position_id));
  form.append('title', data.title);
  if (data.department) form.append('department', data.department);
  if (data.raw_text) form.append('raw_text', data.raw_text);
  if (data.file) form.append('file', data.file);

  const token = localStorage.getItem('token');
  const response = await fetch(`${API_BASE_URL}/job_descriptions/`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: '创建失败' }));
    throw new Error(err.detail || `创建失败: ${response.status}`);
  }
  return response.json();
};

/** AI 解析 JD 原文 → 结构化画像（每项技能带原文依据） */
export const parseJobDescription = async (id: number): Promise<JobDescription> => {
  return apiRequest<JobDescription>(`/job_descriptions/${id}/parse`, { method: 'POST' });
};

/** 人工核对后保存（无原文依据的技能项会被服务端丢弃） */
export const updateJobDescription = async (
  id: number,
  data: { title?: string; department?: string; raw_text?: string; parsed_data?: JdProfile }
): Promise<JobDescription> => {
  return apiRequest<JobDescription>(`/job_descriptions/${id}`, { method: 'PUT', body: data });
};

export const activateJobDescription = async (id: number): Promise<JobDescription> => {
  return apiRequest<JobDescription>(`/job_descriptions/${id}/activate`, { method: 'POST' });
};

export const archiveJobDescription = async (id: number): Promise<JobDescription> => {
  return apiRequest<JobDescription>(`/job_descriptions/${id}/archive`, { method: 'POST' });
};

export const deleteJobDescription = async (id: number): Promise<void> => {
  await apiRequest<void>(`/job_descriptions/${id}`, { method: 'DELETE' });
};
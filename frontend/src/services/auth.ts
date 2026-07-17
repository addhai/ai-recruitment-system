import { apiRequest } from './api';
import type { User } from '../types';

export interface LoginData {
  username: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export const login = async (data: LoginData): Promise<TokenResponse> => {
  const formData = new FormData();
  formData.append('username', data.username);
  formData.append('password', data.password);
  
  const response = await fetch('http://localhost:8000/auth/login', {
    method: 'POST',
    body: formData,
  });
  
  if (!response.ok) {
    throw new Error('登录失败');
  }
  
  return response.json();
};

export const getCurrentUser = async (): Promise<User> => {
  return apiRequest<User>('/auth/users/me');
};

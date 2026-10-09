import { apiRequest, API_BASE_URL } from './api';
import type { User } from '../types';
import { LoginError } from '../lib/authErrors';

export interface LoginData {
  username: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

// 错误类型与用户可读文案住在 src/lib/authErrors.ts，那里有单测覆盖
export { LoginError } from '../lib/authErrors';

export const login = async (data: LoginData): Promise<TokenResponse> => {
  const formData = new FormData();
  formData.append('username', data.username);
  formData.append('password', data.password);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/auth/login`, {
      method: 'POST',
      body: formData,
    });
  } catch {
    // fetch 直接 reject = 请求没出去。绝不能报成凭据错误。
    throw new LoginError('无法连接到服务器', { offline: true });
  }

  if (!response.ok) {
    // 保留后端给的 detail（如 401 的 Incorrect username or password），
    // 而不是丢掉后自己编一句笼统的"登录失败"
    let detail = '';
    try {
      detail = (await response.json())?.detail ?? '';
    } catch {
      /* 非 JSON 错误体（如网关返回的 HTML） */
    }
    throw new LoginError(detail || `登录失败（HTTP ${response.status}）`, {
      status: response.status,
    });
  }

  return response.json();
};

export const getCurrentUser = async (): Promise<User> => {
  return apiRequest<User>('/auth/users/me');
};

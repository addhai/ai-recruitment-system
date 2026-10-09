import { apiRequest, API_BASE_URL } from './api';
import type { User } from '../types';

export interface LoginData {
  username: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

/**
 * 登录失败。区分"凭据不对"和"请求根本没到服务端"两种情况。
 *
 * 此前两者被合并成一句"登录失败，请检查用户名和密码"：后端没启动时
 * 前端会告诉用户密码错了，把排查方向直接带偏（实际踩过：账号正常，
 * 后端进程已被运行时限杀掉）。报错必须指向真实原因。
 */
export class LoginError extends Error {
  /** HTTP 状态码；请求没到达服务端时为 undefined */
  status?: number;
  /** true = 请求没能到达服务端（后端未启动 / 代理不通 / 网络故障） */
  offline: boolean;

  constructor(message: string, opts: { status?: number; offline?: boolean } = {}) {
    super(message);
    this.name = 'LoginError';
    this.status = opts.status;
    this.offline = opts.offline ?? false;
  }
}

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

import { apiRequest } from './api';

/** 与后端 UserResponse 对应 */
export interface ManagedUser {
  id: number;
  username: string;
  email: string;
  full_name: string | null;
  department: string | null;
  role: string;
  is_active: boolean;
  created_at: string;
}

export const ROLES = ['admin', 'hr', 'interviewer', 'viewer'] as const;
export type Role = (typeof ROLES)[number];

export const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  hr: 'HR人员',
  interviewer: '面试官',
  viewer: '查看者',
};

export const ROLE_DESCRIPTIONS: Record<string, string> = {
  admin: '全部权限，含用户管理与模型配置',
  hr: '候选人、岗位、复核、问卷、人才池、成本',
  interviewer: '查看候选人、填写评估、查看成本外的只读数据',
  viewer: '只读',
};

export interface CreateUserInput {
  username: string;
  email: string;
  password: string;
  full_name?: string;
  department?: string;
  role: Role;
}

/**
 * 用户列表单次拉取上限。
 *
 * 列表接口不返回总数，所以"拿到的条数等于上限"是**可能还有更多**的唯一信号——
 * 调用方必须据此给出提示，否则数据会被静默截断（这正是分页最容易埋的坑）。
 */
export const USER_PAGE_SIZE = 500;

/** 用户列表（含已停用账号）。仅管理员可调。 */
export const listUsers = async (): Promise<ManagedUser[]> => {
  return apiRequest<ManagedUser[]>('/auth/users', { params: { limit: USER_PAGE_SIZE } });
};

/** 管理员建号。走后端 /auth/register（该端点现在需要管理员权限）。 */
export const createUser = async (data: CreateUserInput): Promise<ManagedUser> => {
  return apiRequest<ManagedUser>('/auth/register', { method: 'POST', body: data });
};

export interface UpdateUserInput {
  email?: string;
  full_name?: string;
  department?: string;
  role?: Role;
  is_active?: boolean;
}

/** 修改用户。用户名不可改（它是 JWT 的 sub，也是各表的关联依据）。 */
export const updateUser = async (
  id: number,
  data: UpdateUserInput,
): Promise<ManagedUser> => {
  return apiRequest<ManagedUser>(`/auth/users/${id}`, { method: 'PUT', body: data });
};

/** 管理员为用户重置密码；该用户此前的登录状态会全部失效。 */
export const resetUserPassword = async (
  id: number,
  newPassword: string,
): Promise<{ message: string }> => {
  return apiRequest(`/auth/users/${id}/password`, {
    method: 'POST',
    body: { new_password: newPassword },
  });
};

/** 修改自己的密码；返回新令牌（当前会话继续可用，其他会话失效）。 */
export const changeOwnPassword = async (
  oldPassword: string,
  newPassword: string,
): Promise<{ access_token: string; token_type: string }> => {
  return apiRequest('/auth/users/me/password', {
    method: 'POST',
    body: { old_password: oldPassword, new_password: newPassword },
  });
};

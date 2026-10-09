/**
 * 登录失败的用户可读文案。抽成独立模块是为了可测：
 * 这里曾经把"服务不可用"报成"请检查用户名和密码"，把排查方向直接带偏
 * （实际踩过：账号正常，后端进程已停）。报错必须指向真实原因。
 */

/**
 * 登录失败。区分"凭据不对"和"请求根本没到服务端"两种情况。
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

/** 把登录异常翻成给用户看的一句话 */
export function describeLoginError(err: unknown): string {
  const e = err as Partial<LoginError> & { message?: string };
  if (e?.offline) {
    return '无法连接到服务器。请确认后端已启动（默认 127.0.0.1:8000）后重试。';
  }
  if (e?.status === 401) {
    return '用户名或密码错误';
  }
  return e?.message || '登录失败';
}

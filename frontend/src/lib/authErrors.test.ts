import { describe, expect, it } from 'vitest';

import { describeLoginError, LoginError } from './authErrors';

describe('describeLoginError', () => {
  it('请求没到服务端时指向连接问题，而不是凭据', () => {
    // 回归：这里曾经一律报"请检查用户名和密码"。
    // 实际踩过——账号完全正常，是后端进程被运行时限杀掉了，
    // 结果把排查方向带偏到账号上。
    const msg = describeLoginError(new LoginError('无法连接到服务器', { offline: true }));
    expect(msg).toContain('无法连接到服务器');
    expect(msg).toContain('后端已启动');
    expect(msg).not.toContain('密码');
  });

  it('401 才说凭据错误', () => {
    const msg = describeLoginError(
      new LoginError('Incorrect username or password', { status: 401 }));
    expect(msg).toBe('用户名或密码错误');
  });

  it('429 限流时展示后端原文而不是编造结论', () => {
    const msg = describeLoginError(
      new LoginError('尝试次数过多，请稍后再试', { status: 429 }));
    expect(msg).toBe('尝试次数过多，请稍后再试');
    expect(msg).not.toContain('密码');
  });

  it('其它异常回退到 message', () => {
    expect(describeLoginError(new Error('服务器内部错误'))).toBe('服务器内部错误');
  });

  it('没有 message 时给兜底文案', () => {
    expect(describeLoginError({})).toBe('登录失败');
    expect(describeLoginError(null)).toBe('登录失败');
  });

  it('offline 优先于 status 判断', () => {
    const e = new LoginError('x', { offline: true, status: 401 });
    expect(describeLoginError(e)).toContain('无法连接到服务器');
  });
});

describe('LoginError', () => {
  it('默认为非 offline 且无状态码', () => {
    const e = new LoginError('boom');
    expect(e.offline).toBe(false);
    expect(e.status).toBeUndefined();
    expect(e.name).toBe('LoginError');
  });

  it('是 Error 的子类，能被常规 catch 处理', () => {
    expect(new LoginError('x')).toBeInstanceOf(Error);
  });
});

import { describe, expect, it } from 'vitest';

import { parseSSEMessage, TRANSPORT_EVENT_TYPES } from './sse';

describe('传输层事件不产生通知', () => {
  it('建连握手被忽略', () => {
    // 回归：这条曾经被当成业务通知弹出来，内容还是原始 JSON
    // {"type":"connected"}，铃铛还带一个未读角标
    expect(parseSSEMessage('{"type": "connected"}')).toBeNull();
  });

  it.each([...TRANSPORT_EVENT_TYPES])('保活类事件 %s 也被忽略', (t) => {
    expect(parseSSEMessage(JSON.stringify({ type: t }))).toBeNull();
  });

  it('缺少 type 的消息不产生通知', () => {
    expect(parseSSEMessage('{"foo": "bar"}')).toBeNull();
  });

  it('非 JSON 不产生通知也不抛异常', () => {
    expect(parseSSEMessage('这不是 JSON')).toBeNull();
    expect(parseSSEMessage('')).toBeNull();
  });
});

describe('业务事件解析', () => {
  it('工作流进度带百分比与步骤', () => {
    const n = parseSSEMessage(JSON.stringify({
      type: 'workflow_progress', progress: 60, step: 'evaluate_skill_match',
    }));
    expect(n?.title).toBe('工作流进度');
    expect(n?.desc).toContain('60%');
    expect(n?.desc).toContain('evaluate_skill_match');
  });

  it('招聘决策带评分', () => {
    const n = parseSSEMessage(JSON.stringify({
      type: 'hiring_decision', decision: '录用', overall_score: 88,
    }));
    expect(n?.desc).toContain('录用');
    expect(n?.desc).toContain('88');
  });

  it('新候选人带姓名', () => {
    const n = parseSSEMessage(JSON.stringify({
      type: 'candidate_added', candidate_name: '张三',
    }));
    expect(n?.desc).toContain('张三');
  });

  it('状态变更是箭头形式', () => {
    const n = parseSSEMessage(JSON.stringify({
      type: 'candidate_status_changed', old_status: 'a', new_status: 'b',
    }));
    expect(n?.desc).toContain('a → b');
  });

  it('未知业务类型给一句人话，不回退成 JSON', () => {
    const n = parseSSEMessage(JSON.stringify({ type: 'brand_new_type', x: 1 }));
    expect(n).not.toBeNull();
    expect(n?.title).toBe('系统通知');
    expect(n?.desc).not.toContain('{');
    expect(n?.desc).not.toContain('"');
    expect(n?.desc).toBe('收到一条系统通知');
  });

  it('未知类型但有 message 时优先用它', () => {
    const n = parseSSEMessage(JSON.stringify({
      type: 'brand_new_type', message: '维护通知',
    }));
    expect(n?.desc).toBe('维护通知');
  });

  it('系统消息取 message 字段', () => {
    const n = parseSSEMessage(JSON.stringify({
      type: 'system_message', message: '今晚维护',
    }));
    expect(n?.title).toBe('系统消息');
    expect(n?.desc).toBe('今晚维护');
  });
});

describe('通知对象形状', () => {
  it('默认未读、时间为刚刚、id 可区分', () => {
    const a = parseSSEMessage('{"type": "system_message", "message": "x"}');
    const b = parseSSEMessage('{"type": "system_message", "message": "x"}');
    expect(a?.read).toBe(false);
    expect(a?.time).toBe('刚刚');
    expect(a?.id).not.toBe(b?.id);
  });
});

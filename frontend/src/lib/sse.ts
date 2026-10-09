/**
 * SSE 通知消息解析。抽成独立模块是为了可测：
 * 这里曾经把建连握手事件当成业务通知弹出来，内容还是原始 JSON
 * `{"type":"connected"}`——只在浏览器里肉眼看得到。
 */

export interface SSENotification {
  id: number;
  type: string;
  title: string;
  desc: string;
  time: string;
  read: boolean;
}

/** SSE 消息类型对应的通知标题 */
export const NOTIFICATION_TYPE_LABELS: Record<string, string> = {
  workflow_progress: '工作流进度',
  interview_scheduled: '面试安排',
  interview_completed: '面试完成',
  questionnaire_generated: '问卷生成',
  questionnaire_submitted: '问卷提交',
  hiring_decision: '招聘决策',
  candidate_added: '新候选人',
  candidate_status_changed: '状态变更',
  evaluation_added: '评估完成',
  system_message: '系统消息',
};

/**
 * 传输层事件：只表示链路状态，不是业务通知。
 * 后端建连时会发 {"type": "connected"} 确认链路已通（见 src/api/sse.py），
 * 它是握手而不是业务事件，不该弹给用户。
 */
export const TRANSPORT_EVENT_TYPES = new Set(['connected', 'ping', 'heartbeat']);

/** 将 SSE 原始消息解析为通知对象；传输层事件返回 null（不产生通知） */
export function parseSSEMessage(data: string): SSENotification | null {
  let msg: any;
  try {
    msg = JSON.parse(data);
  } catch {
    return null;
  }
  if (!msg?.type || TRANSPORT_EVENT_TYPES.has(msg.type)) return null;

  const title = NOTIFICATION_TYPE_LABELS[msg.type] || '系统通知';
  let desc = '';
  switch (msg.type) {
    case 'workflow_progress':
      desc = `进度: ${msg.progress}% - ${msg.step}`;
      break;
    case 'hiring_decision':
      desc = `决策: ${msg.decision}（评分: ${msg.overall_score}）`;
      break;
    case 'candidate_added':
      desc = `候选人: ${msg.candidate_name}`;
      break;
    case 'candidate_status_changed':
      desc = `状态: ${msg.old_status} → ${msg.new_status}`;
      break;
    case 'interview_scheduled':
      desc = `第${msg.round}轮面试已安排`;
      break;
    case 'interview_completed':
      desc = `面试评分: ${msg.score}`;
      break;
    case 'system_message':
      desc = msg.message || '';
      break;
    default:
      // 未知业务类型：绝不回退成 JSON.stringify——内部结构不该出现在界面上
      desc = msg.candidate_name || msg.message || '收到一条系统通知';
  }
  return {
    id: Date.now() + Math.random(),
    type: msg.type,
    title,
    desc,
    time: '刚刚',
    read: false,
  };
}

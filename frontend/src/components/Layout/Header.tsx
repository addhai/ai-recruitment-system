import React, { useState, useEffect, useCallback } from 'react';
import { Bell, Search, ChevronDown, X, Settings, LogOut } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useNavigate } from 'react-router-dom';
import { API_BASE_URL } from '../../services/api';

// SSE 通知类型
interface SSENotification {
  id: number;
  type: string;
  title: string;
  desc: string;
  time: string;
  read: boolean;
}

// Toast 状态
type ToastState = { title: string; desc: string; type: string } | null;

// SSE 消息类型对应的通知标题
const NOTIFICATION_TYPE_LABELS: Record<string, string> = {
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

// 传输层事件：只表示链路状态，不是业务通知。
// 后端建连时会发 {"type": "connected"} 确认链路已通（见 src/api/sse.py），
// 它是握手而不是业务事件，不该弹给用户。
const TRANSPORT_EVENT_TYPES = new Set(['connected', 'ping', 'heartbeat']);

// 将 SSE 原始消息解析为通知对象；传输层事件返回 null（不产生通知）
function parseSSEMessage(data: string): SSENotification | null {
  try {
    const msg = JSON.parse(data);
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
  } catch {
    return null;
  }
}

const Header: React.FC<{ title: string }> = ({ title }) => {
  const { user, logout } = useAuth();
  const [sseNotifications, setSseNotifications] = useState<SSENotification[]>([]);
  const [toast, setToast] = useState<ToastState>(null);
  const [showDropdown, setShowDropdown] = useState(false);
  const [showNotificationPanel, setShowNotificationPanel] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const navigate = useNavigate();

  const unreadCount = sseNotifications.filter((n) => !n.read).length;

  // 显示 toast 通知（4 秒后自动消失）
  const showNotification = useCallback((notification: SSENotification) => {
    setToast({ title: notification.title, desc: notification.desc, type: notification.type });
    setTimeout(() => setToast(null), 4000);
  }, []);

  // 连接 SSE 实时通知（使用 fetch + ReadableStream，因为 EventSource 不支持自定义 Header）
  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) return;

    const controller = new AbortController();
    let canceled = false;

    fetch(`${API_BASE_URL}/sse/notifications`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.body) return;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        const read = (): void => {
          reader
            .read()
            .then(({ done, value }) => {
              if (done || canceled) return;
              buffer += decoder.decode(value, { stream: true });
              // SSE 消息以空行分隔
              const chunks = buffer.split('\n\n');
              buffer = chunks.pop() || '';
              for (const chunk of chunks) {
                const dataLine = chunk
                  .split('\n')
                  .find((line) => line.startsWith('data: '));
                if (dataLine) {
                  const data = dataLine.slice(6);
                  const notification = parseSSEMessage(data);
                  if (notification) {
                    setSseNotifications((prev) => [notification, ...prev].slice(0, 50));
                    showNotification(notification);
                  }
                }
              }
              read();
            })
            .catch(() => {
              // 读取异常时静默退出（如网络中断）
            });
        };
        read();
      })
      .catch(() => {
        // 连接失败时静默处理
      });

    return () => {
      canceled = true;
      controller.abort();
    };
  }, [showNotification]);

  // 标记通知为已读
  const markAsRead = (id: number) => {
    setSseNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
  };

  // 标记全部已读
  const markAllAsRead = () => {
    setSseNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  };

  // 查看全部通知：标记已读并跳转到仪表盘
  const viewAllNotifications = () => {
    markAllAsRead();
    setShowNotificationPanel(false);
    navigate('/');
  };

  const handleSearch = () => {
    if (searchQuery.trim()) {
      navigate(`/candidates?search=${encodeURIComponent(searchQuery)}`);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };

  return (
    <header className="h-16 bg-white dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700 px-8 flex items-center justify-between relative">
      <div className="flex items-center gap-6">
        <h2 className="text-xl font-semibold text-slate-800 dark:text-slate-100">{title}</h2>
      </div>

      <div className="flex items-center gap-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
          <input
            type="text"
            placeholder="搜索候选人、职位..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyPress={handleKeyPress}
            className="pl-10 pr-4 py-2 w-64 bg-slate-50 dark:bg-slate-700 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all text-slate-800 dark:text-slate-100 placeholder-slate-400"
          />
        </div>

        <div className="relative">
          <button
            onClick={() => setShowNotificationPanel(!showNotificationPanel)}
            className="relative p-2 text-slate-500 dark:text-slate-300 hover:text-slate-700 dark:hover:text-slate-100 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors"
          >
            <Bell size={20} />
            {unreadCount > 0 && (
              <span className="absolute top-1 right-1 w-4 h-4 bg-red-500 text-white text-xs rounded-full flex items-center justify-center">
                {unreadCount}
              </span>
            )}
          </button>

          {showNotificationPanel && (
            <div className="absolute right-0 top-12 w-80 bg-white dark:bg-slate-800 rounded-xl shadow-xl border border-slate-200 dark:border-slate-700 overflow-hidden z-50">
              <div className="flex items-center justify-between p-4 border-b border-slate-100 dark:border-slate-700">
                <h3 className="font-semibold text-slate-800 dark:text-slate-100">通知中心</h3>
                <button onClick={() => setShowNotificationPanel(false)} className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300">
                  <X size={16} />
                </button>
              </div>
              <div className="max-h-80 overflow-y-auto">
                {sseNotifications.length === 0 ? (
                  <div className="p-8 text-center text-sm text-slate-400">
                    暂无实时通知
                  </div>
                ) : (
                  sseNotifications.map((notif) => (
                    <div
                      key={notif.id}
                      onClick={() => markAsRead(notif.id)}
                      className={`p-4 border-b border-slate-50 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors cursor-pointer ${
                        notif.read ? 'opacity-60' : ''
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${
                          notif.type === 'candidate_added' || notif.type === 'candidate_status_changed' ? 'bg-blue-100 text-blue-600' :
                          notif.type === 'interview_scheduled' || notif.type === 'interview_completed' ? 'bg-amber-100 text-amber-600' :
                          notif.type === 'hiring_decision' ? 'bg-green-100 text-green-600' : 'bg-slate-100 text-slate-600'
                        }`}>
                        <Bell size={14} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="font-medium text-slate-800 dark:text-slate-100 text-sm truncate">{notif.title}</p>
                        <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 truncate">{notif.desc}</p>
                        <p className="text-xs text-slate-400 mt-2">{notif.time}</p>
                      </div>
                    </div>
                  </div>
                  ))
                )}
              </div>
              <div className="p-4 border-t border-slate-100 dark:border-slate-700">
                {sseNotifications.length > 0 && (
                  <button
                    onClick={markAllAsRead}
                    className="w-full text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 font-medium mb-2"
                  >
                    标记全部已读
                  </button>
                )}
                <button
                  onClick={viewAllNotifications}
                  className="w-full text-sm text-blue-600 hover:text-blue-700 font-medium"
                >
                  查看全部通知
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="relative">
          <button
            onClick={() => setShowDropdown(!showDropdown)}
            className="flex items-center gap-2 p-1.5 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors"
          >
            <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-sm font-semibold text-white">
              {user?.full_name?.charAt(0) || user?.username?.charAt(0) || 'U'}
            </div>
            <ChevronDown size={16} className="text-slate-500 dark:text-slate-300" />
          </button>

          {showDropdown && (
            <div className="absolute right-0 top-12 w-48 bg-white dark:bg-slate-800 rounded-xl shadow-xl border border-slate-200 dark:border-slate-700 overflow-hidden z-50">
              <div className="p-3 border-b border-slate-100 dark:border-slate-700">
                <p className="font-medium text-slate-800 dark:text-slate-100 text-sm">{user?.full_name || user?.username}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {user?.role === 'admin' ? '管理员' : user?.role === 'hr' ? 'HR人员' : user?.role === 'interviewer' ? '面试官' : '查看者'}
                </p>
              </div>
              <div className="py-1">
                <button onClick={() => navigate('/settings')} className="w-full flex items-center gap-2 px-4 py-2 text-sm text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
                  <Settings size={16} />
                  <span>系统设置</span>
                </button>
                <button
                  onClick={() => {
                    logout();
                    setShowDropdown(false);
                  }}
                  className="w-full flex items-center gap-2 px-4 py-2 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors"
                >
                  <LogOut size={16} />
                  <span>退出登录</span>
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* SSE 实时通知 Toast */}
      {toast && (
        <div className="fixed top-20 right-8 z-[100] transition-all duration-300">
          <div className="flex items-start gap-3 bg-white dark:bg-slate-800 rounded-xl shadow-xl border border-slate-200 dark:border-slate-700 p-4 w-80">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${
              toast.type === 'candidate_added' || toast.type === 'candidate_status_changed' ? 'bg-blue-100 text-blue-600' :
              toast.type === 'interview_scheduled' || toast.type === 'interview_completed' ? 'bg-amber-100 text-amber-600' :
              toast.type === 'hiring_decision' ? 'bg-green-100 text-green-600' : 'bg-slate-100 text-slate-600'
            }`}>
              <Bell size={16} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="font-medium text-slate-800 dark:text-slate-100 text-sm">{toast.title}</p>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 break-words">{toast.desc}</p>
            </div>
            <button
              onClick={() => setToast(null)}
              className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 flex-shrink-0"
            >
              <X size={14} />
            </button>
          </div>
        </div>
      )}
    </header>
  );
};

export default Header;

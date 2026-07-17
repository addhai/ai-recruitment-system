import React, { useState } from 'react';
import { Bell, Search, ChevronDown, X, Settings, LogOut } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useNavigate } from 'react-router-dom';

const Header: React.FC<{ title: string }> = ({ title }) => {
  const { user, logout } = useAuth();
  const [notifications] = useState(3);
  const [showDropdown, setShowDropdown] = useState(false);
  const [showNotificationPanel, setShowNotificationPanel] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const navigate = useNavigate();

  const notificationList = [
    { id: 1, type: 'candidate', title: '新候选人申请', desc: '王芳申请了UI设计师职位', time: '5分钟前', read: false },
    { id: 2, type: 'interview', title: '面试安排变更', desc: '李华的面试时间调整为明天10:00', time: '30分钟前', read: false },
    { id: 3, type: 'evaluation', title: '评估已完成', desc: '张明的技术评估已完成，评分85分', time: '1小时前', read: true },
  ];

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
            {notifications > 0 && (
              <span className="absolute top-1 right-1 w-4 h-4 bg-red-500 text-white text-xs rounded-full flex items-center justify-center">
                {notifications}
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
                {notificationList.map((notif) => (
                  <div
                    key={notif.id}
                    className={`p-4 border-b border-slate-50 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors cursor-pointer ${
                      notif.read ? 'opacity-60' : ''
                    }`}
                  >
                    <div className="flex items-start gap-3">
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${
                        notif.type === 'candidate' ? 'bg-blue-100 text-blue-600' :
                        notif.type === 'interview' ? 'bg-amber-100 text-amber-600' : 'bg-green-100 text-green-600'
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
                ))}
              </div>
              <div className="p-4 border-t border-slate-100 dark:border-slate-700">
                <button className="w-full text-sm text-blue-600 hover:text-blue-700 font-medium">
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
    </header>
  );
};

export default Header;

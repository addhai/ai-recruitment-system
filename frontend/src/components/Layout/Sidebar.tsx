import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import {
  LayoutDashboard,
  Users,
  CalendarDays,
  FileQuestion,
  ClipboardList,
  Database,
  BookOpen,
  Settings,
  LogOut,
  Briefcase,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

const menuItems = [
  { path: '/', icon: LayoutDashboard, label: '数据仪表盘', roles: ['admin', 'hr', 'interviewer', 'viewer'] },
  { path: '/candidates', icon: Users, label: '候选人管理', roles: ['admin', 'hr', 'interviewer'] },
  { path: '/interviews', icon: CalendarDays, label: '面试管理', roles: ['admin', 'hr', 'interviewer'] },
  { path: '/questionnaires', icon: FileQuestion, label: '问卷管理', roles: ['admin', 'hr'] },
  { path: '/evaluations', icon: ClipboardList, label: '评估管理', roles: ['admin', 'hr', 'interviewer'] },
  { path: '/talent-pool', icon: Database, label: '人才池', roles: ['admin', 'hr'] },
  { path: '/knowledge-base', icon: BookOpen, label: '知识库', roles: ['admin', 'hr', 'interviewer', 'viewer'] },
  { path: '/settings', icon: Settings, label: '系统设置', roles: ['admin'] },
];

const Sidebar: React.FC = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const userRole = user?.role || 'viewer';

  return (
    <div className="h-screen w-64 bg-slate-900 text-white flex flex-col">
      <div className="p-6 border-b border-slate-700">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-lg flex items-center justify-center">
            <Briefcase size={20} />
          </div>
          <div>
            <h1 className="font-bold text-lg">AI招聘系统</h1>
            <p className="text-xs text-slate-400">智能招聘管理平台</p>
          </div>
        </div>
      </div>

      <nav className="flex-1 py-4 overflow-y-auto">
        <ul className="space-y-1 px-3">
          {menuItems.map((item) => {
            if (!item.roles.includes(userRole)) return null;
            return (
              <li key={item.path}>
                <NavLink
                  to={item.path}
                  end={item.path === '/'}
                  className={({ isActive }) =>
                    `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-200 ${
                      isActive
                        ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/30'
                        : 'text-slate-300 hover:bg-slate-800 hover:text-white'
                    }`
                  }
                >
                  <item.icon size={18} />
                  <span>{item.label}</span>
                </NavLink>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="p-4 border-t border-slate-700">
        <div className="flex items-center gap-3 mb-3 px-2">
          <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-sm font-semibold">
            {user?.full_name?.charAt(0) || user?.username?.charAt(0) || 'U'}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium truncate">{user?.full_name || user?.username}</p>
            <p className="text-xs text-slate-400 truncate">
              {user?.role === 'admin' ? '管理员' : user?.role === 'hr' ? 'HR人员' : user?.role === 'interviewer' ? '面试官' : '查看者'}
            </p>
          </div>
        </div>
        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-slate-300 hover:bg-slate-800 hover:text-white rounded-lg transition-colors"
        >
          <LogOut size={16} />
          <span>退出登录</span>
        </button>
      </div>
    </div>
  );
};

export default Sidebar;

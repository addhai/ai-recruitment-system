import React, { useState, useEffect } from 'react';
import { Users, Shield, Bell, Palette, Plus, X, Check, Edit2, Trash2 } from 'lucide-react';
import { useTheme } from '../context/ThemeContext';
import type { SkinPreset } from '../context/ThemeContext';
import { getCurrentUser } from '../services/auth';

interface User {
  id: number;
  username: string;
  name: string;
  email: string;
  role: string;
  department: string;
  status: string;
}

interface NotificationSetting {
  id: string;
  title: string;
  desc: string;
  enabled: boolean;
}

const Settings: React.FC = () => {
  const { skinId, skins, mode, setSkinId, setMode } = useTheme();
  const [activeTab, setActiveTab] = useState('users');
  const [showAddModal, setShowAddModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<number | null>(null);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [users, setUsers] = useState<User[]>([
    { id: 1, username: 'admin', name: '管理员', email: 'admin@example.com', role: 'admin', department: '人事部', status: 'active' },
    { id: 2, username: 'hr001', name: '李红', email: 'lihong@example.com', role: 'hr', department: '人事部', status: 'active' },
    { id: 3, username: 'tech001', name: '王工', email: 'wanggong@example.com', role: 'interviewer', department: '技术部', status: 'active' },
    { id: 4, username: 'view001', name: '张经理', email: 'zhang@example.com', role: 'viewer', department: '市场部', status: 'active' },
  ]);
  const [formData, setFormData] = useState({
    username: '',
    name: '',
    email: '',
    role: 'hr',
    department: '',
  });
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [notifications, setNotifications] = useState<NotificationSetting[]>([
    { id: 'new_candidate', title: '新候选人通知', desc: '有新候选人加入时通知', enabled: true },
    { id: 'interview_schedule', title: '面试安排通知', desc: '面试安排和变更时通知', enabled: true },
    { id: 'evaluation_complete', title: '评估完成通知', desc: '评估完成时通知', enabled: true },
    { id: 'system_message', title: '系统消息', desc: '系统公告和更新通知', enabled: false },
  ]);

  useEffect(() => {
    // 拉取当前登录用户（后端 /auth/users/me）
    getCurrentUser()
      .then((u) => setCurrentUser({ id: u.id, username: u.username, name: u.full_name || u.username, email: u.email, role: u.role, department: u.department || '', status: 'active' }))
      .catch(() => { /* 未登录或接口异常时忽略，保留本地配置 */ });
  }, []);

  const tabs = [
    { id: 'users', label: '用户管理', icon: Users },
    { id: 'roles', label: '角色权限', icon: Shield },
    { id: 'notifications', label: '通知设置', icon: Bell },
    { id: 'appearance', label: '外观设置', icon: Palette },
  ];

  const getRoleText = (role: string) => {
    const map: Record<string, string> = {
      admin: '管理员',
      hr: 'HR人员',
      interviewer: '面试官',
      viewer: '查看者',
    };
    return map[role] || role;
  };

  const getRoleColor = (role: string) => {
    switch (role) {
      case 'admin':
        return 'bg-red-100 text-red-700';
      case 'hr':
        return 'bg-blue-100 text-blue-700';
      case 'interviewer':
        return 'bg-green-100 text-green-700';
      default:
        return 'bg-slate-100 text-slate-700';
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value,
    });
  };

  const addUser = () => {
    if (!formData.username || !formData.name || !formData.email) return;
    const newUser: User = {
      id: Date.now(),
      username: formData.username,
      name: formData.name,
      email: formData.email,
      role: formData.role,
      department: formData.department,
      status: 'active',
    };
    setUsers([newUser, ...users]);
    setShowAddModal(false);
    setFormData({ username: '', name: '', email: '', role: 'hr', department: '' });
  };

  const editUser = (user: User) => {
    setSelectedUser(user);
    setFormData({
      username: user.username,
      name: user.name,
      email: user.email,
      role: user.role,
      department: user.department,
    });
    setShowEditModal(true);
  };

  const saveEdit = () => {
    if (!selectedUser) return;
    setUsers(users.map(u => 
      u.id === selectedUser!.id 
        ? { ...u, ...formData }
        : u
    ));
    setShowEditModal(false);
    setSelectedUser(null);
  };

  const deleteUser = (id: number) => {
    const user = users.find(u => u.id === id);
    if (user?.username === 'admin') {
      alert('不能删除管理员账号！');
      setShowDeleteConfirm(null);
      return;
    }
    setUsers(users.filter(u => u.id !== id));
    setShowDeleteConfirm(null);
  };

  const toggleNotification = (id: string) => {
    setNotifications(notifications.map(n => 
      n.id === id ? { ...n, enabled: !n.enabled } : n
    ));
  };

  // 删除已弃用的 themeColors

  return (
    <div className="space-y-6">
      <div className="bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 overflow-hidden">
        <div className="flex">
          <div className="w-56 border-r border-slate-100 dark:border-slate-700 p-4">
            <nav className="space-y-1">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                    activeTab === tab.id
                      ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 font-medium'
                      : 'text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700'
                  }`}
                >
                  <tab.icon size={18} />
                  <span>{tab.label}</span>
                </button>
              ))}
            </nav>
          </div>

          <div className="flex-1 p-6">
            {activeTab === 'users' && (
              <div>
                {currentUser && (
                  <div className="flex items-center gap-4 p-4 bg-gradient-to-r from-blue-50 to-purple-50 dark:from-blue-900/30 dark:to-purple-900/30 border border-blue-100 dark:border-blue-800 rounded-xl mb-6">
                    <div className="w-12 h-12 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white font-bold">
                      {(currentUser.name || currentUser.username).charAt(0)}
                    </div>
                    <div className="flex-1">
                      <p className="font-semibold text-slate-800 dark:text-slate-100">{currentUser.name || currentUser.username}</p>
                      <p className="text-sm text-slate-500 dark:text-slate-400">{currentUser.department || '—'} · 角色：{getRoleText(currentUser.role)}</p>
                    </div>
                    <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-green-100 text-green-700">当前登录</span>
                  </div>
                )}

                <div className="flex items-center justify-between mb-6">
                  <h3 className="text-lg font-semibold text-slate-800 dark:text-slate-100">用户管理</h3>
                  <button onClick={() => setShowAddModal(true)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm">
                    <Plus size={16} />
                    添加用户
                  </button>
                </div>

                <p className="text-xs text-slate-400 mb-4">
                  当前账号信息来自后端 <code className="text-xs bg-slate-100 dark:bg-slate-700 px-1 py-0.5 rounded">/auth/users/me</code>；用户增删改查接口将在后续版本提供，下方列表为本地演示数据。
                </p>

                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-sm text-slate-500 dark:text-slate-400 border-b border-slate-100 dark:border-slate-700">
                        <th className="pb-3 font-medium">用户名</th>
                        <th className="pb-3 font-medium">姓名</th>
                        <th className="pb-3 font-medium">邮箱</th>
                        <th className="pb-3 font-medium">部门</th>
                        <th className="pb-3 font-medium">角色</th>
                        <th className="pb-3 font-medium">状态</th>
                        <th className="pb-3 font-medium text-right">操作</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-50">
                      {users.map((user) => (
                        <tr key={user.id} className="hover:bg-slate-50">
                          <td className="py-3">
                            <div className="flex items-center gap-3">
                              <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-sm font-medium">
                                {user.name.charAt(0)}
                              </div>
                              <span className="font-medium text-slate-800">{user.username}</span>
                            </div>
                          </td>
                          <td className="py-3 text-slate-600">{user.name}</td>
                          <td className="py-3 text-slate-600">{user.email}</td>
                          <td className="py-3 text-slate-600">{user.department}</td>
                          <td className="py-3">
                            <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${getRoleColor(user.role)}`}>
                              {getRoleText(user.role)}
                            </span>
                          </td>
                          <td className="py-3">
                            <span className="inline-flex items-center gap-1.5 text-sm">
                              <span className="w-2 h-2 bg-green-500 rounded-full"></span>
                              <span className="text-green-600">正常</span>
                            </span>
                          </td>
                          <td className="py-3 text-right">
                            <div className="flex items-center justify-end gap-1">
                              <button onClick={() => editUser(user)} className="p-1.5 text-slate-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-lg transition-colors" title="编辑">
                                <Edit2 size={16} />
                              </button>
                              {showDeleteConfirm === user.id ? (
                                <div className="flex items-center gap-1">
                                  <button onClick={() => deleteUser(user.id)} className="p-1.5 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30 rounded-lg transition-colors">
                                    确认
                                  </button>
                                  <button onClick={() => setShowDeleteConfirm(null)} className="p-1.5 text-slate-600 hover:bg-slate-50 dark:hover:bg-slate-700 rounded-lg transition-colors">
                                    取消
                                  </button>
                                </div>
                              ) : (
                                <button onClick={() => setShowDeleteConfirm(user.id)} className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30 rounded-lg transition-colors" title="删除">
                                  <Trash2 size={16} />
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {activeTab === 'roles' && (
              <div>
                <h3 className="text-lg font-semibold text-slate-800 mb-6">角色权限</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {[
                    { role: 'admin', name: '超级管理员', desc: '拥有系统所有权限', count: 1 },
                    { role: 'hr', name: 'HR人员', desc: '候选人管理、面试安排、评估', count: 2 },
                    { role: 'interviewer', name: '面试官', desc: '查看候选人、填写评估', count: 5 },
                    { role: 'viewer', name: '查看者', desc: '只读查看数据', count: 3 },
                  ].map((item) => (
                    <div key={item.role} className="p-4 border border-slate-200 rounded-lg hover:border-blue-300 transition-colors">
                      <div className="flex items-start justify-between">
                        <div>
                          <h4 className="font-medium text-slate-800">{item.name}</h4>
                          <p className="text-sm text-slate-500 mt-1">{item.desc}</p>
                        </div>
                        <span className="text-sm text-slate-400">{item.count}人</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {activeTab === 'notifications' && (
              <div>
                <h3 className="text-lg font-semibold text-slate-800 mb-6">通知设置</h3>
                <div className="space-y-4">
                  {notifications.map((item) => (
                    <div key={item.id} className="flex items-center justify-between p-4 bg-slate-50 rounded-lg">
                      <div>
                        <h4 className="font-medium text-slate-800">{item.title}</h4>
                        <p className="text-sm text-slate-500 mt-1">{item.desc}</p>
                      </div>
                      <button
                        onClick={() => toggleNotification(item.id)}
                        className={`relative w-12 h-6 rounded-full transition-colors ${item.enabled ? 'bg-blue-600' : 'bg-slate-300'}`}
                      >
                        <div className={`absolute w-5 h-5 bg-white rounded-full shadow transition-transform ${item.enabled ? 'translate-x-6' : 'translate-x-0.5'}`}></div>
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {activeTab === 'appearance' && (
              <div>
                <h3 className="text-lg font-semibold text-slate-800 dark:text-slate-100 mb-6">外观设置</h3>
                <div className="space-y-8">
                  {/* 皮肤选择 */}
                  <div>
                    <h4 className="font-medium text-slate-700 dark:text-slate-200 mb-1">皮肤主题</h4>
                    <p className="text-sm text-slate-400 mb-4">选择一套皮肤，侧边栏、按钮、渐变色都会同步变化</p>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                      {skins.map((s: SkinPreset) => (
                        <button
                          key={s.id}
                          onClick={() => setSkinId(s.id)}
                          className={`relative p-4 rounded-xl border-2 transition-all text-left ${
                            skinId === s.id
                              ? 'border-slate-400 dark:border-slate-500 ring-2 ring-slate-200 dark:ring-slate-600'
                              : 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600'
                          }`}
                        >
                          {/* 预览条 */}
                          <div className="flex rounded-lg overflow-hidden mb-3 h-16">
                            <div className="flex-1 flex items-center justify-center" style={{ backgroundColor: s.sidebarBg }}>
                              <div className="w-6 h-6 rounded" style={{ backgroundColor: s.sidebarActiveBg }}></div>
                            </div>
                            <div className="flex-1 flex flex-col items-center justify-center gap-1 bg-white dark:bg-slate-800">
                              <div className="w-10 h-2.5 rounded-full" style={{ backgroundColor: s.primary }}></div>
                              <div className="w-8 h-2.5 rounded-full" style={{ backgroundColor: s.primaryLight }}></div>
                            </div>
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="font-medium text-sm text-slate-700 dark:text-slate-200">{s.name}</span>
                            {skinId === s.id && (
                              <Check size={16} style={{ color: s.primary }} />
                            )}
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* 深色/浅色模式 */}
                  <div>
                    <h4 className="font-medium text-slate-700 dark:text-slate-200 mb-3">显示模式</h4>
                    <div className="flex gap-3">
                      <button
                        onClick={() => setMode('light')}
                        className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                          mode === 'light'
                            ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 border border-blue-200'
                            : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-600 hover:bg-slate-200 dark:hover:bg-slate-600'
                        }`}
                      >
                        浅色模式
                      </button>
                      <button
                        onClick={() => setMode('dark')}
                        className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                          mode === 'dark'
                            ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 border border-blue-200'
                            : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-600 hover:bg-slate-200 dark:hover:bg-slate-600'
                        }`}
                      >
                        深色模式
                      </button>
                      <button
                        onClick={() => setMode('system')}
                        className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                          mode === 'system'
                            ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 border border-blue-200'
                            : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-600 hover:bg-slate-200 dark:hover:bg-slate-600'
                        }`}
                      >
                        跟随系统
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {showAddModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-slate-800 rounded-xl w-full max-w-md p-6 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-slate-800 dark:text-slate-100">添加用户</h3>
              <button onClick={() => setShowAddModal(false)} className="p-2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg">
                <X size={20} />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">用户名</label>
                <input
                  type="text"
                  name="username"
                  value={formData.username}
                  onChange={handleInputChange}
                  placeholder="请输入用户名"
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 placeholder-slate-400"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">姓名</label>
                <input
                  type="text"
                  name="name"
                  value={formData.name}
                  onChange={handleInputChange}
                  placeholder="请输入姓名"
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 placeholder-slate-400"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">邮箱</label>
                <input
                  type="email"
                  name="email"
                  value={formData.email}
                  onChange={handleInputChange}
                  placeholder="请输入邮箱"
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 placeholder-slate-400"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">部门</label>
                <input
                  type="text"
                  name="department"
                  value={formData.department}
                  onChange={handleInputChange}
                  placeholder="请输入部门"
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 placeholder-slate-400"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">角色</label>
                <select name="role" value={formData.role} onChange={handleInputChange} className="w-full px-3 py-2 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100">
                  <option value="admin">管理员</option>
                  <option value="hr">HR人员</option>
                  <option value="interviewer">面试官</option>
                  <option value="viewer">查看者</option>
                </select>
              </div>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowAddModal(false)}
                  className="flex-1 px-4 py-2 border border-slate-200 dark:border-slate-600 text-slate-600 dark:text-slate-300 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
                >
                  取消
                </button>
                <button onClick={addUser} className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                  添加
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showEditModal && selectedUser && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-slate-800 rounded-xl w-full max-w-md p-6 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-slate-800 dark:text-slate-100">编辑用户</h3>
              <button onClick={() => setShowEditModal(false)} className="p-2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg">
                <X size={20} />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">用户名</label>
                <input
                  type="text"
                  name="username"
                  value={formData.username}
                  onChange={handleInputChange}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">姓名</label>
                <input
                  type="text"
                  name="name"
                  value={formData.name}
                  onChange={handleInputChange}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">邮箱</label>
                <input
                  type="email"
                  name="email"
                  value={formData.email}
                  onChange={handleInputChange}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">部门</label>
                <input
                  type="text"
                  name="department"
                  value={formData.department}
                  onChange={handleInputChange}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">角色</label>
                <select name="role" value={formData.role} onChange={handleInputChange} className="w-full px-3 py-2 border border-slate-200 dark:border-slate-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100">
                  <option value="admin">管理员</option>
                  <option value="hr">HR人员</option>
                  <option value="interviewer">面试官</option>
                  <option value="viewer">查看者</option>
                </select>
              </div>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowEditModal(false)}
                  className="flex-1 px-4 py-2 border border-slate-200 dark:border-slate-600 text-slate-600 dark:text-slate-300 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
                >
                  取消
                </button>
                <button onClick={saveEdit} className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                  保存
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Settings;

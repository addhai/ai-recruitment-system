import React, { useState } from 'react';
import { Users, Bell, Palette, Check, Cpu } from 'lucide-react';
import { useTheme } from '../context/ThemeContext';
import type { SkinPreset } from '../context/ThemeContext';
import ModelConfigPanel from '../components/Settings/ModelConfigPanel';
import UserManagementPanel from '../components/Settings/UserManagementPanel';

interface NotificationSetting {
  id: string;
  title: string;
  desc: string;
  enabled: boolean;
}

/**
 * 系统设置。本页只负责 tab 外壳，各 tab 的内容各自成组件。
 *
 * 用户管理与角色权限曾经是**本地演示数据**（页面上标着"接口将在后续版本提供"），
 * 而 /auth/register 收紧到管理员之后，管理员就完全没有界面可以建号了。
 * 现在用户管理与真实的角色统计都在 UserManagementPanel 里，数据来自后端；
 * 原先写死的角色人数（1/2/5/3）也就此去掉——假数字比没有数字更误导。
 */
const Settings: React.FC = () => {
  const { skinId, skins, mode, setSkinId, setMode } = useTheme();
  const [activeTab, setActiveTab] = useState('users');
  const [notifications, setNotifications] = useState<NotificationSetting[]>([
    { id: 'new_candidate', title: '新候选人通知', desc: '有新候选人加入时通知', enabled: true },
    { id: 'interview_schedule', title: '面试安排通知', desc: '面试安排和变更时通知', enabled: true },
    { id: 'evaluation_complete', title: '评估完成通知', desc: '评估完成时通知', enabled: true },
    { id: 'system_message', title: '系统消息', desc: '系统公告和更新通知', enabled: false },
  ]);

  // 模型配置与用户管理都涉及密钥/权限，仅管理员可见（后端同口径 require_admin）
  const tabs = [
    { id: 'users', label: '用户管理', icon: Users },
    { id: 'model', label: '模型配置', icon: Cpu },
    { id: 'notifications', label: '通知设置', icon: Bell },
    { id: 'appearance', label: '外观设置', icon: Palette },
  ];

  const toggleNotification = (id: string) => {
    setNotifications(notifications.map(n =>
      n.id === id ? { ...n, enabled: !n.enabled } : n
    ));
  };

  return (
    <div className="space-y-6">
      <div className="bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 overflow-hidden">
        <div className="flex">
          {/* shrink-0：flex 子项默认 flex-shrink:1，右侧宽表格会把这一列挤扁，
              标签就竖排成单字（实测在 959px 宽下面板内挤到 68px）。
              让它保持 14rem，右侧内容自己横向滚动（表格已有 overflow-x-auto）。 */}
          <div className="w-56 shrink-0 border-r border-slate-100 dark:border-slate-700 p-4">
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

          <div className="flex-1 p-6 min-w-0">
            {activeTab === 'users' && <UserManagementPanel />}

            {activeTab === 'model' && <ModelConfigPanel />}

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
    </div>
  );
};

export default Settings;

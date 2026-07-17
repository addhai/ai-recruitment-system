import React from 'react';
import { useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import Header from './Header';

const pageTitles: Record<string, string> = {
  '/': '数据仪表盘',
  '/candidates': '候选人管理',
  '/interviews': '面试管理',
  '/questionnaires': '问卷管理',
  '/evaluations': '评估管理',
  '/talent-pool': '人才池',
  '/knowledge-base': '知识库',
  '/settings': '系统设置',
};

const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const location = useLocation();
  const currentPath = location.pathname.startsWith('/candidates/') ? '/candidates' : location.pathname;
  const title = pageTitles[currentPath] || 'AI招聘系统';

  return (
    <div className="flex h-screen bg-slate-50 dark:bg-slate-900">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header title={title} />
        <main className="flex-1 overflow-auto p-8 bg-slate-50 dark:bg-slate-900">
          {children}
        </main>
      </div>
    </div>
  );
};

export default Layout;

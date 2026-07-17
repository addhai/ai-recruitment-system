import React, { useState, useEffect } from 'react';
import {
  Users,
  UserCheck,
  CalendarCheck,
  Trophy,
  Clock,
  TrendingUp,
  Activity,
  ArrowUpRight,
  X,
} from 'lucide-react';
import StatCard from '../components/Dashboard/StatCard';
import TrendChart from '../components/Dashboard/TrendChart';
import { getDashboardStats, getWeeklyTrend, getRecentCandidates } from '../services/dashboard';
import type { DashboardStats, TrendData } from '../types';
import { useNavigate } from 'react-router-dom';

const Dashboard: React.FC = () => {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [trend, setTrend] = useState<TrendData[]>([]);
  const [recentCandidates, setRecentCandidates] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedCard, setExpandedCard] = useState<string | null>(null);
  const navigate = useNavigate();

  const candidateList = [
    { id: 1, name: '张明', position: '高级前端工程师', status: 'interviewed', email: 'zhangming@example.com' },
    { id: 2, name: '李华', position: '产品经理', status: 'pending', email: 'lihua@example.com' },
    { id: 3, name: '王芳', position: 'UI设计师', status: 'hired', email: 'wangfang@example.com' },
    { id: 4, name: '刘伟', position: '后端开发工程师', status: 'pending', email: 'liuwei@example.com' },
    { id: 5, name: '陈静', position: 'Java开发工程师', status: 'interviewed', email: 'chenjing@example.com' },
    { id: 6, name: '赵磊', position: '测试工程师', status: 'rejected', email: 'zhaolei@example.com' },
  ];

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [statsData, trendData, candidatesData] = await Promise.all([
          getDashboardStats().catch(() => ({
            total_candidates: 156,
            pending_candidates: 42,
            interviewed_candidates: 89,
            hired_candidates: 25,
            avg_interview_time: 45.5,
            avg_match_score: 78.3,
            this_month_candidates: 23,
            interview_progress: [
              { status: 'scheduled', count: 15 },
              { status: 'in_progress', count: 8 },
              { status: 'completed', count: 66 },
            ],
          })),
          getWeeklyTrend().catch(() => [
            { date: '2026-07-11', day: '周六', candidates: 5, interviews: 3 },
            { date: '2026-07-12', day: '周日', candidates: 3, interviews: 1 },
            { date: '2026-07-13', day: '周一', candidates: 8, interviews: 6 },
            { date: '2026-07-14', day: '周二', candidates: 12, interviews: 8 },
            { date: '2026-07-15', day: '周三', candidates: 10, interviews: 10 },
            { date: '2026-07-16', day: '周四', candidates: 15, interviews: 7 },
            { date: '2026-07-17', day: '周五', candidates: 8, interviews: 5 },
          ]),
          getRecentCandidates().catch(() => [
            { id: 1, name: '郑浩', position: '全栈工程师', status: 'interviewing', created_at: '2026-07-17T10:30:00' },
            { id: 2, name: '周杰', position: '架构师', status: 'pending', created_at: '2026-07-15T09:15:00' },
            { id: 3, name: '王芳', position: 'UI设计师', status: 'hired', created_at: '2026-07-13T16:45:00' },
            { id: 4, name: '刘伟', position: '后端开发', status: 'interviewing', created_at: '2026-07-12T14:20:00' },
            { id: 5, name: '陈静', position: '前端开发', status: 'pending', created_at: '2026-07-11T11:00:00' },
          ]),
        ]);
        setStats(statsData);
        setTrend(trendData);
        setRecentCandidates(candidatesData);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const getStatusColor = (status: string) => {
    switch (status) {
      case '已录用':
      case 'hired':
        return 'bg-green-100 text-green-700';
      case '面试中':
      case 'interviewed':
      case 'interviewing':
        return 'bg-blue-100 text-blue-700';
      case '待处理':
      case 'pending':
        return 'bg-amber-100 text-amber-700';
      case '已拒绝':
      case 'rejected':
        return 'bg-red-100 text-red-700';
      default:
        return 'bg-slate-100 text-slate-700';
    }
  };

  const getStatusText = (status: string) => {
    const map: Record<string, string> = {
      pending: '待处理',
      interviewed: '面试中',
      interviewing: '面试中',
      hired: '已录用',
      rejected: '已拒绝',
    };
    return map[status] || status;
  };

  const getFilteredCandidates = (type: string) => {
    switch (type) {
      case 'total':
        return candidateList;
      case 'pending':
        return candidateList.filter(c => c.status === 'pending');
      case 'interviewed':
        return candidateList.filter(c => c.status === 'interviewed' || c.status === 'interviewing');
      case 'hired':
        return candidateList.filter(c => c.status === 'hired');
      default:
        return [];
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-slate-500">加载中...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <div
          className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 cursor-pointer hover:shadow-md transition-all"
          onClick={() => setExpandedCard(expandedCard === 'total' ? null : 'total')}
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-sm text-slate-500">候选人总数</p>
              <p className="text-3xl font-bold text-slate-800 mt-1">{stats?.total_candidates || 0}</p>
              <p className="text-sm text-green-600 mt-1 flex items-center gap-1">
                <ArrowUpRight size={14} />
                <span>↑ 12% 较上月</span>
              </p>
            </div>
            <div className="p-3 bg-blue-50 rounded-xl">
              <Users className="text-blue-600" size={24} />
            </div>
          </div>
          {expandedCard === 'total' && (
            <div className="border-t border-slate-100 pt-4 mt-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-medium text-slate-700">人员名单</span>
                <button onClick={(e) => { e.stopPropagation(); setExpandedCard(null); }} className="text-slate-400 hover:text-slate-600">
                  <X size={16} />
                </button>
              </div>
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {getFilteredCandidates('total').map(c => (
                  <div key={c.id} className="flex items-center justify-between p-2 hover:bg-slate-50 rounded-lg cursor-pointer" onClick={() => navigate(`/candidates/${c.id}`)}>
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs font-medium">
                        {c.name.charAt(0)}
                      </div>
                      <span className="text-sm text-slate-700">{c.name}</span>
                    </div>
                    <span className={`px-2 py-0.5 rounded-full text-xs ${getStatusColor(c.status)}`}>
                      {getStatusText(c.status)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div
          className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 cursor-pointer hover:shadow-md transition-all"
          onClick={() => setExpandedCard(expandedCard === 'pending' ? null : 'pending')}
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-sm text-slate-500">待处理</p>
              <p className="text-3xl font-bold text-slate-800 mt-1">{stats?.pending_candidates || 0}</p>
              <p className="text-sm text-green-600 mt-1 flex items-center gap-1">
                <ArrowUpRight size={14} />
                <span>↑ 5% 较上月</span>
              </p>
            </div>
            <div className="p-3 bg-amber-50 rounded-xl">
              <Clock className="text-amber-600" size={24} />
            </div>
          </div>
          {expandedCard === 'pending' && (
            <div className="border-t border-slate-100 pt-4 mt-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-medium text-slate-700">待处理名单</span>
                <button onClick={(e) => { e.stopPropagation(); setExpandedCard(null); }} className="text-slate-400 hover:text-slate-600">
                  <X size={16} />
                </button>
              </div>
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {getFilteredCandidates('pending').map(c => (
                  <div key={c.id} className="flex items-center justify-between p-2 hover:bg-slate-50 rounded-lg cursor-pointer" onClick={() => navigate(`/candidates/${c.id}`)}>
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs font-medium">
                        {c.name.charAt(0)}
                      </div>
                      <span className="text-sm text-slate-700">{c.name}</span>
                    </div>
                    <span className={`px-2 py-0.5 rounded-full text-xs ${getStatusColor(c.status)}`}>
                      {getStatusText(c.status)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div
          className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 cursor-pointer hover:shadow-md transition-all"
          onClick={() => setExpandedCard(expandedCard === 'interviewed' ? null : 'interviewed')}
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-sm text-slate-500">已面试</p>
              <p className="text-3xl font-bold text-slate-800 mt-1">{stats?.interviewed_candidates || 0}</p>
              <p className="text-sm text-green-600 mt-1 flex items-center gap-1">
                <ArrowUpRight size={14} />
                <span>↑ 8% 较上月</span>
              </p>
            </div>
            <div className="p-3 bg-green-50 rounded-xl">
              <CalendarCheck className="text-green-600" size={24} />
            </div>
          </div>
          {expandedCard === 'interviewed' && (
            <div className="border-t border-slate-100 pt-4 mt-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-medium text-slate-700">面试中名单</span>
                <button onClick={(e) => { e.stopPropagation(); setExpandedCard(null); }} className="text-slate-400 hover:text-slate-600">
                  <X size={16} />
                </button>
              </div>
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {getFilteredCandidates('interviewed').map(c => (
                  <div key={c.id} className="flex items-center justify-between p-2 hover:bg-slate-50 rounded-lg cursor-pointer" onClick={() => navigate(`/candidates/${c.id}`)}>
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs font-medium">
                        {c.name.charAt(0)}
                      </div>
                      <span className="text-sm text-slate-700">{c.name}</span>
                    </div>
                    <span className={`px-2 py-0.5 rounded-full text-xs ${getStatusColor(c.status)}`}>
                      {getStatusText(c.status)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div
          className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 cursor-pointer hover:shadow-md transition-all"
          onClick={() => setExpandedCard(expandedCard === 'hired' ? null : 'hired')}
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-sm text-slate-500">已录用</p>
              <p className="text-3xl font-bold text-slate-800 mt-1">{stats?.hired_candidates || 0}</p>
              <p className="text-sm text-green-600 mt-1 flex items-center gap-1">
                <ArrowUpRight size={14} />
                <span>↑ 3% 较上月</span>
              </p>
            </div>
            <div className="p-3 bg-purple-50 rounded-xl">
              <Trophy className="text-purple-600" size={24} />
            </div>
          </div>
          {expandedCard === 'hired' && (
            <div className="border-t border-slate-100 pt-4 mt-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-medium text-slate-700">已录用名单</span>
                <button onClick={(e) => { e.stopPropagation(); setExpandedCard(null); }} className="text-slate-400 hover:text-slate-600">
                  <X size={16} />
                </button>
              </div>
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {getFilteredCandidates('hired').map(c => (
                  <div key={c.id} className="flex items-center justify-between p-2 hover:bg-slate-50 rounded-lg cursor-pointer" onClick={() => navigate(`/candidates/${c.id}`)}>
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-xs font-medium">
                        {c.name.charAt(0)}
                      </div>
                      <span className="text-sm text-slate-700">{c.name}</span>
                    </div>
                    <span className={`px-2 py-0.5 rounded-full text-xs ${getStatusColor(c.status)}`}>
                      {getStatusText(c.status)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <TrendChart data={trend} />
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h3 className="font-semibold text-slate-800 mb-4">面试进度</h3>
          <div className="space-y-4">
            {stats?.interview_progress?.map((item, index) => (
              <div key={index}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-slate-600">
                    {item.status === 'scheduled' && '待面试'}
                    {item.status === 'in_progress' && '进行中'}
                    {item.status === 'completed' && '已完成'}
                  </span>
                  <span className="font-medium text-slate-800">{item.count}</span>
                </div>
                <div className="w-full bg-slate-100 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full transition-all ${
                      item.status === 'scheduled'
                        ? 'bg-amber-500'
                        : item.status === 'in_progress'
                        ? 'bg-blue-500'
                        : 'bg-green-500'
                    }`}
                    style={{ width: `${(item.count / (stats?.total_candidates || 1)) * 100}%` }}
                  ></div>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-6 pt-6 border-t border-slate-100">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-blue-50 rounded-lg">
                <TrendingUp className="text-blue-600" size={20} />
              </div>
              <div>
                <p className="text-sm text-slate-500">平均匹配度</p>
                <p className="text-xl font-bold text-slate-800">{stats?.avg_match_score?.toFixed(1)}%</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-slate-800">最新候选人</h3>
          <button
            onClick={() => navigate('/candidates')}
            className="text-sm text-blue-600 hover:text-blue-700 font-medium flex items-center gap-1"
          >
            查看全部
            <ArrowUpRight size={14} />
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="text-left text-sm text-slate-500 border-b border-slate-100">
                <th className="pb-3 font-medium">候选人</th>
                <th className="pb-3 font-medium">职位</th>
                <th className="pb-3 font-medium">状态</th>
                <th className="pb-3 font-medium">创建时间</th>
              </tr>
            </thead>
            <tbody>
              {recentCandidates.map((candidate) => (
                <tr key={candidate.id} className="border-b border-slate-50 hover:bg-slate-50 transition-colors cursor-pointer" onClick={() => navigate(`/candidates/${candidate.id}`)}>
                  <td className="py-3">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-sm font-medium">
                        {candidate.name.charAt(0)}
                      </div>
                      <span className="font-medium text-slate-800">{candidate.name}</span>
                    </div>
                  </td>
                  <td className="py-3 text-slate-600">{candidate.position}</td>
                  <td className="py-3">
                    <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${getStatusColor(candidate.status)}`}>
                      {getStatusText(candidate.status)}
                    </span>
                  </td>
                  <td className="py-3 text-slate-500 text-sm">
                    {new Date(candidate.created_at).toLocaleDateString('zh-CN')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;

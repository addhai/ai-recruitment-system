import React, { useState } from 'react';
import { Plus, Calendar, User, Clock, CheckCircle, PlayCircle, XCircle } from 'lucide-react';

const Interviews: React.FC = () => {
  const [showModal, setShowModal] = useState(false);

  const candidates = [
    { id: 1, name: '张明', position: '高级前端工程师' },
    { id: 2, name: '李华', position: '产品经理' },
    { id: 3, name: '王芳', position: 'UI设计师' },
    { id: 4, name: '刘伟', position: '后端开发工程师' },
    { id: 5, name: '陈静', position: 'Java开发工程师' },
    { id: 6, name: '赵磊', position: '测试工程师' },
  ];

  const interviewers = ['李工', '王总', '陈总监', '赵工', '孙经理', '周工'];

  const interviews = [
    { id: 2, candidate: '李华', position: '产品经理', round: 1, status: 'scheduled', interviewer: '王总', scheduled_at: '2026-07-18 10:00', score: null },
    { id: 3, candidate: '王芳', position: 'UI设计师', round: 2, status: 'in_progress', interviewer: '陈总监', scheduled_at: '2026-07-17 15:00', score: null },
    { id: 4, candidate: '刘伟', position: '后端开发工程师', round: 1, status: 'scheduled', interviewer: '赵工', scheduled_at: '2026-07-19 09:30', score: null },
    { id: 5, candidate: '陈静', position: 'Java开发工程师', round: 2, status: 'completed', interviewer: '孙经理', scheduled_at: '2026-07-16 11:00', score: 78 },
    { id: 6, candidate: '赵磊', position: '测试工程师', round: 1, status: 'cancelled', interviewer: '周工', scheduled_at: '2026-07-14 14:00', score: null },
  ];

  const getStatusText = (status: string) => {
    const map: Record<string, string> = {
      scheduled: '待面试',
      in_progress: '进行中',
      completed: '已完成',
      cancelled: '已取消',
    };
    return map[status] || status;
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return 'bg-green-100 text-green-700';
      case 'in_progress':
        return 'bg-blue-100 text-blue-700';
      case 'scheduled':
        return 'bg-amber-100 text-amber-700';
      case 'cancelled':
        return 'bg-red-100 text-red-700';
      default:
        return 'bg-slate-100 text-slate-700';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle size={16} className="text-green-600" />;
      case 'in_progress':
        return <PlayCircle size={16} className="text-blue-600" />;
      case 'scheduled':
        return <Clock size={16} className="text-amber-600" />;
      case 'cancelled':
        return <XCircle size={16} className="text-red-600" />;
      default:
        return null;
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-slate-500">共 {interviews.length} 场面试</p>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors shadow-sm"
        >
          <Plus size={18} />
          <span>安排面试</span>
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[
          { label: '待面试', count: 2, color: 'amber' },
          { label: '进行中', count: 1, color: 'blue' },
          { label: '已完成', count: 2, color: 'green' },
          { label: '已取消', count: 1, color: 'red' },
        ].map((item) => (
          <div key={item.label} className="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-500">{item.label}</span>
              <span className={`text-2xl font-bold ${
                item.color === 'amber' ? 'text-amber-600' :
                item.color === 'blue' ? 'text-blue-600' :
                item.color === 'green' ? 'text-green-600' : 'text-red-600'
              }`}>{item.count}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-slate-50">
              <tr className="text-left text-sm text-slate-500">
                <th className="px-6 py-3 font-medium">候选人</th>
                <th className="px-6 py-3 font-medium">职位</th>
                <th className="px-6 py-3 font-medium">轮次</th>
                <th className="px-6 py-3 font-medium">面试官</th>
                <th className="px-6 py-3 font-medium">时间</th>
                <th className="px-6 py-3 font-medium">状态</th>
                <th className="px-6 py-3 font-medium">评分</th>
                <th className="px-6 py-3 font-medium text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {interviews.map((interview) => (
                <tr key={interview.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-sm font-medium">
                        {interview.candidate.charAt(0)}
                      </div>
                      <span className="font-medium text-slate-800">{interview.candidate}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-slate-600">{interview.position}</td>
                  <td className="px-6 py-4 text-slate-600">第{interview.round}轮</td>
                  <td className="px-6 py-4 text-slate-600">{interview.interviewer}</td>
                  <td className="px-6 py-4 text-slate-600">{interview.scheduled_at}</td>
                  <td className="px-6 py-4">
                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${getStatusColor(interview.status)}`}>
                      {getStatusIcon(interview.status)}
                      {getStatusText(interview.status)}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    {interview.score ? (
                      <span className="font-medium text-slate-800">{interview.score}分</span>
                    ) : (
                      <span className="text-slate-400">-</span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <button className="text-blue-600 hover:text-blue-700 text-sm font-medium">
                      查看详情
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-md p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">安排面试</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">候选人</label>
                <select className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="">选择候选人</option>
                  {candidates.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.name} - {candidate.position}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">面试轮次</label>
                <select className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="1">第一轮</option>
                  <option value="2">第二轮</option>
                  <option value="3">第三轮</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">面试官</label>
                <select className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="">选择面试官</option>
                  {interviewers.map((interviewer) => (
                    <option key={interviewer} value={interviewer}>
                      {interviewer}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">面试时间</label>
                <input
                  type="datetime-local"
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  onClick={() => setShowModal(false)}
                  className="flex-1 px-4 py-2 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors"
                >
                  取消
                </button>
                <button className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                  确认
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Interviews;

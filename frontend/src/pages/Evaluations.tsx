import React from 'react';
import { ClipboardList, User, Star, MessageSquare } from 'lucide-react';

const Evaluations: React.FC = () => {
  const evaluations = [
    { id: 1, candidate: '张明', dimension: '技术能力', score: 85, evaluator: '李工', comment: '技术基础扎实，对React和TypeScript有深入理解', created_at: '2026-07-15' },
    { id: 2, candidate: '张明', dimension: '沟通能力', score: 90, evaluator: '李工', comment: '沟通清晰，表达能力强', created_at: '2026-07-15' },
    { id: 3, candidate: '李华', dimension: '产品思维', score: 88, evaluator: '王总', comment: '产品思维敏锐，用户体验意识强', created_at: '2026-07-14' },
    { id: 4, candidate: '王芳', dimension: '设计能力', score: 92, evaluator: '陈总监', comment: '设计审美优秀，创意十足', created_at: '2026-07-13' },
    { id: 5, candidate: '陈静', dimension: 'Java技术', score: 78, evaluator: '孙经理', comment: '基础还可以，需要加强架构设计能力', created_at: '2026-07-12' },
  ];

  const dimensions = [
    { name: '技术能力', avg: 82, count: 15 },
    { name: '沟通能力', avg: 85, count: 12 },
    { name: '团队协作', avg: 80, count: 10 },
    { name: '学习能力', avg: 88, count: 8 },
    { name: '文化匹配', avg: 79, count: 15 },
  ];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {dimensions.map((dim) => (
          <div key={dim.name} className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-medium text-slate-700">{dim.name}</h3>
              <Star className="text-amber-500" size={20} />
            </div>
            <p className="text-3xl font-bold text-slate-800 mb-2">{dim.avg}分</p>
            <div className="w-full bg-slate-100 rounded-full h-2 mb-2">
              <div
                className="bg-gradient-to-r from-blue-500 to-indigo-600 h-2 rounded-full"
                style={{ width: `${dim.avg}%` }}
              ></div>
            </div>
            <p className="text-xs text-slate-400">{dim.count} 次评估</p>
          </div>
        ))}
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="p-4 border-b border-slate-100">
          <h3 className="font-semibold text-slate-800">评估记录</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-slate-50">
              <tr className="text-left text-sm text-slate-500">
                <th className="px-6 py-3 font-medium">候选人</th>
                <th className="px-6 py-3 font-medium">评估维度</th>
                <th className="px-6 py-3 font-medium">评分</th>
                <th className="px-6 py-3 font-medium">评估人</th>
                <th className="px-6 py-3 font-medium">评语</th>
                <th className="px-6 py-3 font-medium">时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {evaluations.map((e) => (
                <tr key={e.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-sm font-medium">
                        {e.candidate.charAt(0)}
                      </div>
                      <span className="font-medium text-slate-800">{e.candidate}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <span className="px-2.5 py-1 bg-blue-50 text-blue-700 rounded-full text-xs font-medium">
                      {e.dimension}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`font-bold ${e.score >= 85 ? 'text-green-600' : e.score >= 70 ? 'text-amber-600' : 'text-red-600'}`}>
                      {e.score}分
                    </span>
                  </td>
                  <td className="px-6 py-4 text-slate-600">{e.evaluator}</td>
                  <td className="px-6 py-4 text-slate-600 max-w-xs truncate">{e.comment}</td>
                  <td className="px-6 py-4 text-slate-500 text-sm">{e.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default Evaluations;

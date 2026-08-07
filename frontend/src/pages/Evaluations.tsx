import React, { useState, useEffect, useMemo } from 'react';
import { ClipboardList, Star, Loader2, AlertCircle, Inbox } from 'lucide-react';
import { getEvaluations, computeDimensionStats } from '../services/evaluations';
import { getCandidates } from '../services/candidates';
import type { Evaluation, Candidate } from '../types';

const Evaluations: React.FC = () => {
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const candidateMap = useMemo(() => {
    const map = new Map<number, string>();
    candidates.forEach((c) => map.set(c.id, c.name));
    return map;
  }, [candidates]);

  const dimensions = useMemo(() => computeDimensionStats(evaluations), [evaluations]);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const [evals, cands] = await Promise.all([
          getEvaluations(),
          getCandidates({ limit: 1000 }),
        ]);
        setEvaluations(evals);
        setCandidates(cands);
      } catch (err: any) {
        setError(err.message || '加载评估数据失败');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const scoreColor = (score: number) =>
    score >= 85 ? 'text-green-600' : score >= 70 ? 'text-amber-600' : 'text-red-600';

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 className="animate-spin mr-2" size={20} />
        加载中...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-red-500">
        <AlertCircle size={32} className="mb-3" />
        <p>{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {dimensions.length === 0 ? (
          <div className="md:col-span-3 bg-white rounded-xl shadow-sm border border-slate-200 p-6 text-center text-slate-400">
            <Inbox className="mx-auto mb-2" size={28} />
            暂无评估维度数据
          </div>
        ) : (
          dimensions.map((dim) => (
            <div key={dim.name} className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-medium text-slate-700">{dim.name}</h3>
                <Star className="text-amber-500" size={20} />
              </div>
              <p className="text-3xl font-bold text-slate-800 mb-2">{dim.avg}分</p>
              <div className="w-full bg-slate-100 rounded-full h-2 mb-2">
                <div className="bg-gradient-to-r from-blue-500 to-indigo-600 h-2 rounded-full" style={{ width: `${dim.avg}%` }} />
              </div>
              <p className="text-xs text-slate-400">{dim.count} 次评估</p>
            </div>
          ))
        )}
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="p-4 border-b border-slate-100">
          <h3 className="font-semibold text-slate-800">评估记录</h3>
        </div>
        {evaluations.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-slate-400">
            <ClipboardList size={32} className="mb-3" />
            <p>暂无评估记录</p>
          </div>
        ) : (
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
                {evaluations.map((e) => {
                  const cName = candidateMap.get(e.candidate_id) || `候选人#${e.candidate_id}`;
                  return (
                    <tr key={e.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-3">
                          <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-sm font-medium">
                            {cName.charAt(0)}
                          </div>
                          <span className="font-medium text-slate-800">{cName}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <span className="px-2.5 py-1 bg-blue-50 text-blue-700 rounded-full text-xs font-medium">{e.dimension}</span>
                      </td>
                      <td className="px-6 py-4">
                        <span className={`font-bold ${scoreColor(e.score)}`}>{e.score}分</span>
                      </td>
                      <td className="px-6 py-4 text-slate-600">评估人#{e.evaluator_id}</td>
                      <td className="px-6 py-4 text-slate-600 max-w-xs truncate">{e.comment || '-'}</td>
                      <td className="px-6 py-4 text-slate-500 text-sm">{new Date(e.created_at).toLocaleDateString()}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default Evaluations;

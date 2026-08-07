import React, { useState, useEffect, useMemo } from 'react';
import { Plus, Calendar, Clock, CheckCircle, PlayCircle, XCircle, Loader2, AlertCircle } from 'lucide-react';
import { getInterviews, createInterview, completeInterview } from '../services/interviews';
import { getCandidates } from '../services/candidates';
import type { Interview, Candidate } from '../types';

const statusMap: Record<string, { text: string; cls: string; icon: React.JSX.Element }> = {
  scheduled: { text: '待面试', cls: 'bg-amber-100 text-amber-700', icon: <Clock size={16} className="text-amber-600" /> },
  in_progress: { text: '进行中', cls: 'bg-blue-100 text-blue-700', icon: <PlayCircle size={16} className="text-blue-600" /> },
  completed: { text: '已完成', cls: 'bg-green-100 text-green-700', icon: <CheckCircle size={16} className="text-green-600" /> },
  cancelled: { text: '已取消', cls: 'bg-red-100 text-red-700', icon: <XCircle size={16} className="text-red-600" /> },
};

const Interviews: React.FC = () => {
  const [interviews, setInterviews] = useState<Interview[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [showComplete, setShowComplete] = useState<Interview | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 新建面试表单
  const [form, setForm] = useState({ candidate_id: '' as string | number, round: 1, scheduled_at: '' });
  // 完成面试表单
  const [completeForm, setCompleteForm] = useState({ score: 80, feedback: '', notes: '' });

  const candidateMap = useMemo(() => {
    const map = new Map<number, string>();
    candidates.forEach((c) => map.set(c.id, c.name));
    return map;
  }, [candidates]);

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = { scheduled: 0, in_progress: 0, completed: 0, cancelled: 0 };
    interviews.forEach((i) => { counts[i.status] = (counts[i.status] || 0) + 1; });
    return counts;
  }, [interviews]);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [ivs, cands] = await Promise.all([getInterviews(), getCandidates({ limit: 1000 })]);
      setInterviews(ivs);
      setCandidates(cands);
    } catch (err: any) {
      setError(err.message || '加载面试数据失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const openCreate = () => {
    setForm({ candidate_id: '', round: 1, scheduled_at: '' });
    setShowModal(true);
  };

  const submitCreate = async () => {
    if (!form.candidate_id || !form.scheduled_at) return;
    setSubmitting(true);
    try {
      const candidate = candidates.find((c) => c.id === Number(form.candidate_id));
      await createInterview({
        candidate_id: Number(form.candidate_id),
        position: candidate?.position || '未指定',
        round: form.round,
        scheduled_at: new Date(form.scheduled_at).toISOString(),
      });
      setShowModal(false);
      await load();
    } catch (err: any) {
      setError(err.message || '创建面试失败');
    } finally {
      setSubmitting(false);
    }
  };

  const openComplete = (iv: Interview) => {
    setCompleteForm({ score: iv.score || 80, feedback: iv.feedback || '', notes: iv.notes || '' });
    setShowComplete(iv);
  };

  const submitComplete = async () => {
    if (!showComplete) return;
    setSubmitting(true);
    try {
      await completeInterview(showComplete.id, {
        score: completeForm.score,
        feedback: completeForm.feedback,
        notes: completeForm.notes || undefined,
      });
      setShowComplete(null);
      await load();
    } catch (err: any) {
      setError(err.message || '完成面试失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 className="animate-spin mr-2" size={20} /> 加载中...
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
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">共 {interviews.length} 场面试</p>
        <button onClick={openCreate} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors shadow-sm">
          <Plus size={18} /> <span>安排面试</span>
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[
          { key: 'scheduled', label: '待面试', color: 'text-amber-600' },
          { key: 'in_progress', label: '进行中', color: 'text-blue-600' },
          { key: 'completed', label: '已完成', color: 'text-green-600' },
          { key: 'cancelled', label: '已取消', color: 'text-red-600' },
        ].map((item) => (
          <div key={item.key} className="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-500">{item.label}</span>
              <span className={`text-2xl font-bold ${item.color}`}>{statusCounts[item.key] || 0}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        {interviews.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-slate-400">
            <Calendar size={32} className="mb-3" />
            <p>暂无面试安排</p>
          </div>
        ) : (
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
                {interviews.map((iv) => {
                  const cName = candidateMap.get(iv.candidate_id) || `候选人#${iv.candidate_id}`;
                  const st = statusMap[iv.status] || { text: iv.status, cls: 'bg-slate-100 text-slate-700', icon: null };
                  return (
                    <tr key={iv.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-3">
                          <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-sm font-medium">{cName.charAt(0)}</div>
                          <span className="font-medium text-slate-800">{cName}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 text-slate-600">{iv.position}</td>
                      <td className="px-6 py-4 text-slate-600">第{iv.round}轮</td>
                      <td className="px-6 py-4 text-slate-600">{iv.interviewer_id ? `面试官#${iv.interviewer_id}` : '待定'}</td>
                      <td className="px-6 py-4 text-slate-600">{iv.scheduled_at ? new Date(iv.scheduled_at).toLocaleString() : '-'}</td>
                      <td className="px-6 py-4">
                        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${st.cls}`}>{st.icon}{st.text}</span>
                      </td>
                      <td className="px-6 py-4">
                        {iv.score ? <span className="font-medium text-slate-800">{iv.score}分</span> : <span className="text-slate-400">-</span>}
                      </td>
                      <td className="px-6 py-4 text-right">
                        {iv.status !== 'completed' && (
                          <button onClick={() => openComplete(iv)} className="text-blue-600 hover:text-blue-700 text-sm font-medium">完成面试</button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 新建面试弹窗 */}
      {showModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-md p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">安排面试</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">候选人</label>
                <select
                  value={form.candidate_id}
                  onChange={(e) => setForm({ ...form, candidate_id: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">选择候选人</option>
                  {candidates.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}{c.position ? ` - ${c.position}` : ''}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">面试轮次</label>
                <select
                  value={form.round}
                  onChange={(e) => setForm({ ...form, round: Number(e.target.value) })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value={1}>第一轮</option>
                  <option value={2}>第二轮</option>
                  <option value={3}>第三轮</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">面试时间</label>
                <input
                  type="datetime-local"
                  value={form.scheduled_at}
                  onChange={(e) => setForm({ ...form, scheduled_at: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={() => setShowModal(false)} className="flex-1 px-4 py-2 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors">取消</button>
                <button
                  onClick={submitCreate}
                  disabled={submitting || !form.candidate_id || !form.scheduled_at}
                  className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
                >
                  {submitting ? '提交中...' : '确认'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 完成面试弹窗 */}
      {showComplete && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-md p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">完成面试</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">评分（0-100）</label>
                <input
                  type="number" min={0} max={100}
                  value={completeForm.score}
                  onChange={(e) => setCompleteForm({ ...completeForm, score: Number(e.target.value) })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">反馈</label>
                <textarea
                  value={completeForm.feedback}
                  onChange={(e) => setCompleteForm({ ...completeForm, feedback: e.target.value })}
                  rows={3}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">备注</label>
                <input
                  value={completeForm.notes}
                  onChange={(e) => setCompleteForm({ ...completeForm, notes: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={() => setShowComplete(null)} className="flex-1 px-4 py-2 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors">取消</button>
                <button onClick={submitComplete} disabled={submitting} className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">
                  {submitting ? '提交中...' : '确认完成'}
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

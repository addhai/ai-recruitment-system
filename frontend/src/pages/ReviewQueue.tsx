import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ClipboardCheck, Loader2, Check, X, AlertTriangle } from 'lucide-react';
import type { ReviewItem } from '../types';
import { getPendingReviews, decideReview } from '../services/reviews';

function ScoreCell({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="text-center">
      <p className="text-lg font-semibold text-slate-800">{value ?? '-'}</p>
      <p className="text-xs text-slate-500">{label}</p>
    </div>
  );
}

const ReviewQueue: React.FC = () => {
  const navigate = useNavigate();
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await getPendingReviews());
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const decide = async (item: ReviewItem, decision: 'approve' | 'reject') => {
    const verb = decision === 'approve' ? '通过并录用' : '淘汰';
    if (!window.confirm(`确认将「${item.name}」${verb}？\n该操作直接改写终局结果，不会重新运行 AI 评估。`)) {
      return;
    }
    setBusy(item.candidate_id);
    try {
      await decideReview(item.candidate_id, decision, notes[item.candidate_id]);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-800 flex items-center gap-2">
          <ClipboardCheck className="w-6 h-6" />
          待人工复核
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          文化契合落在待复核区间的候选人不会被系统自动录用或淘汰，流程照常跑完后在这里由 HR 裁决。
        </p>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-800 text-sm">
          <AlertTriangle className="w-4 h-4" />
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-slate-400 text-sm">加载中…</p>
      ) : items.length === 0 ? (
        <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-slate-400 text-sm">
          当前没有待复核的候选人。
        </div>
      ) : (
        <div className="space-y-4">
          {items.map((item) => {
            const d = item.review_detail;
            return (
              <div key={item.candidate_id} className="bg-white rounded-xl border border-amber-200 p-5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <button
                      onClick={() => navigate(`/candidates/${item.candidate_id}`)}
                      className="text-lg font-semibold text-slate-800 hover:text-blue-600"
                    >
                      {item.name}
                    </button>
                    <p className="text-xs text-slate-500 mt-0.5">{item.position || '未填岗位'}</p>
                    <p className="text-xs text-amber-700 bg-amber-50 inline-block px-2 py-0.5 rounded mt-2">
                      {item.review_reason || '需要人工复核'}
                    </p>
                    {d?.degraded && (
                      <p className="text-xs text-red-600 mt-1">
                        注意：该维度分数为大模型不可用时的兜底值（70 分），可信度低
                      </p>
                    )}
                    {d && (
                      <p className="text-xs text-slate-500 mt-1">
                        触发区间 [{d.review_threshold}, {d.pass_threshold}) · 本岗位价值观：
                        {(d.culture_values || []).join('、') || 'JD 未写明'}
                      </p>
                    )}
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-2xl font-bold text-slate-800">{item.overall_score ?? '-'}</p>
                    <p className="text-xs text-slate-500">综合分</p>
                    {item.assessed_dimensions.length > 0 && (
                      <p className="text-[11px] text-slate-400 mt-1">
                        基于 {item.assessed_dimensions.length} 个维度
                      </p>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-6 gap-2 mt-4 py-3 border-y border-slate-100">
                  <ScoreCell label="技能" value={item.skill_match_score} />
                  <ScoreCell label="经验" value={item.experience_match_score} />
                  <ScoreCell label="教育" value={item.education_match_score} />
                  <ScoreCell label="文化" value={item.culture_match_score} />
                  <ScoreCell label="问卷" value={item.questionnaire_score} />
                  <div className="text-center">
                    <p className="text-lg font-semibold text-slate-800">
                      {item.interview_scores.length
                        ? Math.round(
                            item.interview_scores.reduce((s, x) => s + x.score, 0) /
                              item.interview_scores.length
                          )
                        : '-'}
                    </p>
                    <p className="text-xs text-slate-500">面试均分</p>
                  </div>
                </div>

                <div className="flex items-center gap-2 mt-4">
                  <input
                    className="flex-1 border border-slate-300 rounded px-3 py-2 text-sm"
                    placeholder="裁决备注（可选，会写入人才库备注与评估记录）"
                    value={notes[item.candidate_id] || ''}
                    onChange={(e) => setNotes((p) => ({ ...p, [item.candidate_id]: e.target.value }))}
                  />
                  <button
                    onClick={() => decide(item, 'approve')}
                    disabled={busy === item.candidate_id}
                    className="flex items-center gap-1 bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm disabled:opacity-50"
                  >
                    {busy === item.candidate_id ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Check className="w-4 h-4" />
                    )}
                    通过录用
                  </button>
                  <button
                    onClick={() => decide(item, 'reject')}
                    disabled={busy === item.candidate_id}
                    className="flex items-center gap-1 bg-red-600 text-white px-4 py-2 rounded-lg text-sm disabled:opacity-50"
                  >
                    <X className="w-4 h-4" />
                    淘汰
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default ReviewQueue;
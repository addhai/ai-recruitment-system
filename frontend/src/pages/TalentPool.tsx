import React, { useState, useEffect, useMemo } from 'react';
import { Plus, Tag, Phone, Clock, Filter, X, Save, Loader2, AlertCircle, Trash2 } from 'lucide-react';
import {
  getTalentPool,
  addToTalentPool,
  recordContact,
  removeFromTalentPool,
} from '../services/talentPool';
import { getCandidates } from '../services/candidates';
import type { Candidate, TalentPoolEntry } from '../types';

const statusText = (s: string) => (s === 'active' ? '活跃' : s === 'passive' ? '不活跃' : s || '未知');
const statusColor = (s: string) =>
  s === 'active' ? 'bg-green-100 text-green-700' : s === 'passive' ? 'bg-slate-100 text-slate-600' : 'bg-amber-100 text-amber-700';

const fmtDate = (d: string | null) => (d ? new Date(d).toLocaleDateString() : '—');

const TalentPool: React.FC = () => {
  const [pools, setPools] = useState<TalentPoolEntry[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [contactFor, setContactFor] = useState<number | null>(null);
  const [contactNotes, setContactNotes] = useState('');
  const [savingContact, setSavingContact] = useState(false);
  const [form, setForm] = useState({ candidate_id: 0, tags: '', notes: '' });

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, c] = await Promise.all([getTalentPool(), getCandidates({ limit: 200 })]);
      setPools(p);
      setCandidates(c);
    } catch (err: any) {
      setError(err.message || '加载人才库失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const candidateMap = useMemo(() => {
    const m: Record<number, Candidate> = {};
    candidates.forEach((c) => (m[c.id] = c));
    return m;
  }, [candidates]);

  const allTags = useMemo(() => {
    const s = new Set<string>();
    pools.forEach((p) => (p.tags || []).forEach((t) => s.add(t)));
    return Array.from(s);
  }, [pools]);

  const filtered = selectedTags.length
    ? pools.filter((p) => (p.tags || []).some((t) => selectedTags.includes(t)))
    : pools;

  const inPoolIds = useMemo(() => new Set(pools.map((p) => p.candidate_id)), [pools]);
  const availableCandidates = candidates.filter((c) => !inPoolIds.has(c.id));

  const toggleTag = (t: string) =>
    setSelectedTags((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));

  const addToPool = async () => {
    if (!form.candidate_id) return;
    try {
      const tags = form.tags.split(',').map((t) => t.trim()).filter(Boolean);
      await addToTalentPool({ candidate_id: form.candidate_id, tags, notes: form.notes || undefined });
      setShowAdd(false);
      setForm({ candidate_id: 0, tags: '', notes: '' });
      await load();
    } catch (err: any) {
      setError(err.message || '加入人才池失败');
    }
  };

  const saveContact = async (id: number) => {
    setSavingContact(true);
    try {
      await recordContact(id, contactNotes.trim() || undefined);
      setContactFor(null);
      setContactNotes('');
      await load();
    } catch (err: any) {
      setError(err.message || '记录联系失败');
    } finally {
      setSavingContact(false);
    }
  };

  const remove = async (id: number) => {
    if (!confirm('确认将该候选人移出人才池？')) return;
    try {
      await removeFromTalentPool(id);
      await load();
    } catch (err: any) {
      setError(err.message || '移除失败');
    }
  };

  if (loading) {
    return <div className="flex items-center justify-center py-20 text-slate-400"><Loader2 className="animate-spin mr-2" size={20} /> 加载中...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">
          共 {filtered.length} 位候选人{selectedTags.length > 0 && `（已筛选，总 ${pools.length}）`}
        </p>
        <button onClick={() => setShowAdd(true)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors shadow-sm">
          <Plus size={18} /> <span>加入人才池</span>
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-red-500 bg-red-50 border border-red-200 rounded-lg p-3 text-sm">
          <AlertCircle size={16} /> {error}
        </div>
      )}

      {allTags.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
          <div className="flex items-center gap-2 mb-3">
            <Filter size={18} className="text-slate-500" />
            <span className="text-sm font-medium text-slate-700">按标签筛选</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {allTags.map((tag) => (
              <button
                key={tag}
                onClick={() => toggleTag(tag)}
                className={`px-3 py-1.5 rounded-full text-sm transition-colors ${
                  selectedTags.includes(tag)
                    ? 'bg-blue-600 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {tag}
              </button>
            ))}
            {selectedTags.length > 0 && (
              <button onClick={() => setSelectedTags([])} className="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-700">
                清除筛选
              </button>
            )}
          </div>
        </div>
      )}

      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-slate-400">
          <Tag size={32} className="mb-3" />
          <p>人才池暂无数据，点击右上角加入候选人</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filtered.map((p) => {
            const c = candidateMap[p.candidate_id];
            return (
              <div key={p.id} className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-md transition-shadow">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="w-12 h-12 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white font-bold">
                      {c?.name?.charAt(0) || '?'}
                    </div>
                    <div>
                      <h3 className="font-semibold text-slate-800">{c?.name || `候选人#${p.candidate_id}`}</h3>
                      <p className="text-sm text-slate-500">{c?.position || '职位未填'}</p>
                    </div>
                  </div>
                  <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${statusColor(p.status)}`}>
                    {statusText(p.status)}
                  </span>
                </div>

                <div className="flex flex-wrap gap-1.5 mb-4">
                  {(p.tags || []).map((t) => (
                    <span key={t} className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded text-xs">{t}</span>
                  ))}
                </div>

                <div className="space-y-2 text-sm text-slate-500 mb-4">
                  <div className="flex items-center gap-2"><Phone size={14} /><span>上次联系: {fmtDate(p.last_contact)}</span></div>
                  {p.next_contact && (
                    <div className="flex items-center gap-2"><Clock size={14} /><span>下次联系: {fmtDate(p.next_contact)}</span></div>
                  )}
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => { setContactFor(p.id); setContactNotes(''); }}
                    className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-sm text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-lg transition-colors"
                  >
                    <Phone size={14} /> 记录联系
                  </button>
                  <button
                    onClick={() => remove(p.id)}
                    className="p-2 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                    title="移出人才池"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>

                {contactFor === p.id && (
                  <div className="mt-3 bg-slate-50 rounded-lg p-3">
                    <textarea
                      value={contactNotes}
                      onChange={(e) => setContactNotes(e.target.value)}
                      placeholder="请输入联系内容"
                      rows={2}
                      className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <div className="flex gap-2 mt-2">
                      <button
                        onClick={() => setContactFor(null)}
                        className="flex-1 px-3 py-2 text-sm text-slate-600 hover:bg-slate-200 rounded-lg transition-colors"
                      >
                        取消
                      </button>
                      <button
                        onClick={() => saveContact(p.id)}
                        disabled={savingContact}
                        className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors disabled:opacity-50"
                      >
                        <Save size={14} /> {savingContact ? '保存中' : '保存'}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {showAdd && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-lg p-6 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-slate-800">加入人才池</h3>
              <button onClick={() => setShowAdd(false)} className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg">
                <X size={20} />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">选择候选人</label>
                <select
                  value={form.candidate_id}
                  onChange={(e) => setForm({ ...form, candidate_id: Number(e.target.value) })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value={0}>请选择候选人</option>
                  {availableCandidates.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}（{c.position || '职位未填'}）</option>
                  ))}
                </select>
                {availableCandidates.length === 0 && (
                  <p className="text-xs text-slate-400 mt-1">所有候选人已存在于人才池</p>
                )}
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">标签（逗号分隔）</label>
                <input
                  type="text"
                  value={form.tags}
                  onChange={(e) => setForm({ ...form, tags: e.target.value })}
                  placeholder="例如：前端,React,Node.js"
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">备注</label>
                <textarea
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  placeholder="请输入备注信息"
                  rows={3}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                />
              </div>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowAdd(false)}
                  className="flex-1 px-4 py-2 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={addToPool}
                  disabled={!form.candidate_id}
                  className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
                >
                  确认加入
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default TalentPool;

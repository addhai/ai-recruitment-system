import React, { useState, useEffect } from 'react';
import { Plus, FileQuestion, Eye, Trash2, Sparkles, X, Loader2, AlertCircle } from 'lucide-react';
import {
  getQuestionnaires,
  getQuestionnaire,
  createQuestionnaire,
  deleteQuestionnaire,
  type Question,
  type QuestionnaireListItem,
  type QuestionnaireDetail,
} from '../services/questionnaires';

const typeColors: Record<string, string> = {
  technical: 'bg-blue-100 text-blue-700',
  技术类: 'bg-blue-100 text-blue-700',
  behavioral: 'bg-purple-100 text-purple-700',
  行为类: 'bg-purple-100 text-purple-700',
  comprehensive: 'bg-amber-100 text-amber-700',
  综合类: 'bg-amber-100 text-amber-700',
};
const typeColor = (t: string) => typeColors[t] || 'bg-slate-100 text-slate-700';
const typeLabel: Record<string, string> = { technical: '技术类', behavioral: '行为类', comprehensive: '综合类' };

/** 根据类型生成问卷模板（后端暂无独立生成接口，生成后通过 POST 持久化） */
const buildTemplateQuestions = (type: string, requirements: string): Question[] => {
  const req = requirements.trim();
  const header = req ? `（基于职位要求：${req.slice(0, 40)}${req.length > 40 ? '…' : ''}）` : '';
  if (type === 'behavioral') {
    return [
      { id: 1, text: `请描述一次你遇到的最大挑战，以及如何解决的。${header}`, type: '问答题' },
      { id: 2, text: '你如何处理团队中的冲突？', type: '问答题' },
      { id: 3, text: '分享一次你主导失败的经历，你的复盘是什么？', type: '问答题' },
      { id: 4, text: '你如何对多个并行任务进行优先级排序？', type: '问答题' },
      { id: 5, text: '描述一次你主动推动改进的案例。', type: '问答题' },
    ];
  }
  if (type === 'comprehensive') {
    return [
      { id: 1, text: `请简述你的项目经验。${header}`, type: '问答题' },
      { id: 2, text: '你最擅长的技术栈是什么？', type: '多选题', options: ['前端', '后端', '移动端', '全栈'] },
      { id: 3, text: '你如何进行需求分析与拆解？', type: '问答题' },
      { id: 4, text: '你如何学习新技术？', type: '问答题' },
      { id: 5, text: '你的职业规划是什么？', type: '问答题' },
    ];
  }
  return [
    { id: 1, text: `请描述你最熟悉的技术方向及其原理。${header}`, type: '问答题' },
    { id: 2, text: '如何定位与优化一个性能瓶颈？', type: '问答题' },
    { id: 3, text: '你更倾向哪种协作方式？', type: '多选题', options: ['异步沟通', '即时会议', '文档驱动', '代码评审'] },
    { id: 4, text: '描述一次你解决复杂技术问题的经历。', type: '问答题' },
    { id: 5, text: '你如何保证代码质量？', type: '问答题' },
  ];
};

const Questionnaires: React.FC = () => {
  const [list, setList] = useState<QuestionnaireListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const [detail, setDetail] = useState<QuestionnaireDetail | null>(null);
  const [form, setForm] = useState({ name: '', type: 'technical', requirements: '' });

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setList(await getQuestionnaires());
    } catch (err: any) {
      setError(err.message || '加载问卷失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const openDetail = async (id: number) => {
    try {
      setDetail(await getQuestionnaire(id));
      setShowDetail(true);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const generate = async () => {
    if (!form.name.trim()) return;
    setGenerating(true);
    try {
      await createQuestionnaire({ name: form.name, type: form.type, questions: buildTemplateQuestions(form.type, form.requirements) });
      setShowModal(false);
      setForm({ name: '', type: 'technical', requirements: '' });
      await load();
    } catch (err: any) {
      setError(err.message || '生成问卷失败');
    } finally {
      setGenerating(false);
    }
  };

  const remove = async (id: number) => {
    if (!confirm('确认删除该问卷？')) return;
    try {
      await deleteQuestionnaire(id);
      await load();
    } catch (err: any) {
      setError(err.message);
    }
  };

  if (loading) {
    return <div className="flex items-center justify-center py-20 text-slate-400"><Loader2 className="animate-spin mr-2" size={20} /> 加载中...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">共 {list.length} 份问卷</p>
        <button onClick={() => setShowModal(true)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors shadow-sm">
          <Plus size={18} /> <span>创建问卷</span>
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-red-500 bg-red-50 border border-red-200 rounded-lg p-3 text-sm">
          <AlertCircle size={16} /> {error}
        </div>
      )}

      {list.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-slate-400">
          <FileQuestion size={32} className="mb-3" />
          <p>暂无问卷，点击右上角创建</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {list.map((q) => (
            <div key={q.id} className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-md transition-shadow">
              <div className="flex items-start justify-between mb-4">
                <div className="p-3 bg-blue-50 rounded-xl"><FileQuestion className="text-blue-600" size={24} /></div>
                <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${typeColor(q.type)}`}>{typeLabel[q.type] || q.type}</span>
              </div>
              <h3 className="font-semibold text-slate-800 mb-2">{q.name}</h3>
              <p className="text-sm text-slate-500 mb-4">{q.question_count} 道题目</p>
              <div className="flex items-center justify-between pt-4 border-t border-slate-100">
                <span className="text-xs text-slate-400">创建于 {new Date(q.created_at).toLocaleDateString()}</span>
                <div className="flex items-center gap-1">
                  <button onClick={() => openDetail(q.id)} className="p-1.5 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors" title="查看"><Eye size={16} /></button>
                  <button onClick={() => remove(q.id)} className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors" title="删除"><Trash2 size={16} /></button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-lg p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">创建问卷</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">问卷名称</label>
                <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="请输入问卷名称" className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">问卷类型</label>
                <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })} className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="technical">技术类</option>
                  <option value="behavioral">行为类</option>
                  <option value="comprehensive">综合类</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">职位要求（用于生成上下文）</label>
                <textarea value={form.requirements} onChange={(e) => setForm({ ...form, requirements: e.target.value })} placeholder="请输入职位要求，将融入问卷生成" rows={4} className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none" />
              </div>
              <button onClick={generate} disabled={generating || !form.name.trim()} className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-purple-600 to-blue-600 text-white rounded-lg hover:from-purple-700 hover:to-blue-700 transition-all disabled:opacity-50">
                <Sparkles size={18} /> {generating ? '生成中...' : 'AI智能生成问卷'}
              </button>
              <button onClick={() => setShowModal(false)} className="w-full px-4 py-2 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors">取消</button>
            </div>
          </div>
        </div>
      )}

      {showDetail && detail && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-2xl p-6 shadow-xl max-h-[80vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-slate-800">{detail.name}</h3>
              <button onClick={() => setShowDetail(false)} className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg"><X size={20} /></button>
            </div>
            <div className="flex items-center gap-4 mb-6">
              <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${typeColor(detail.type)}`}>{typeLabel[detail.type] || detail.type}</span>
              <span className="text-sm text-slate-500">{detail.question_count} 道题目</span>
              <span className="text-sm text-slate-400">创建于 {new Date(detail.created_at).toLocaleDateString()}</span>
            </div>
            <div className="space-y-4">
              {(detail.questions || []).map((q, index) => (
                <div key={q.id} className="p-4 bg-slate-50 rounded-lg">
                  <div className="flex items-start gap-3">
                    <span className="flex-shrink-0 w-7 h-7 bg-blue-100 text-blue-600 rounded-full flex items-center justify-center text-sm font-medium">{index + 1}</span>
                    <div className="flex-1">
                      <p className="font-medium text-slate-800">{q.text}</p>
                      <span className="inline-block mt-1 px-2 py-0.5 bg-slate-200 text-slate-600 rounded text-xs">{q.type}</span>
                      {q.options && (
                        <ul className="mt-2 space-y-1">
                          {q.options.map((opt, i) => (
                            <li key={i} className="text-sm text-slate-600 flex items-center gap-2">
                              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full"></span>{opt}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Questionnaires;

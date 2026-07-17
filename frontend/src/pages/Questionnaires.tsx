import React, { useState } from 'react';
import { Plus, FileQuestion, Edit, Trash2, Eye, Sparkles, X, Save } from 'lucide-react';

interface Questionnaire {
  id: number;
  name: string;
  type: string;
  question_count: number;
  created_at: string;
  questions?: Array<{ id: number; text: string; type: string; options?: string[] }>;
}

const Questionnaires: React.FC = () => {
  const [showModal, setShowModal] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [selectedQuestionnaire, setSelectedQuestionnaire] = useState<Questionnaire | null>(null);
  const [questionnaires, setQuestionnaires] = useState<Questionnaire[]>([
    { id: 1, name: '前端工程师技术问卷', type: '技术类', question_count: 8, created_at: '2026-07-10', questions: [
      { id: 1, text: 'React中的useEffect钩子在什么情况下会执行？', type: '多选题', options: ['组件挂载时', '依赖项变化时', '每次渲染时', '组件卸载时'] },
      { id: 2, text: 'TypeScript中interface和type的区别是什么？', type: '问答题' },
      { id: 3, text: '解释一下闭包的概念及其应用场景', type: '问答题' },
    ]},
    { id: 2, name: '产品经理综合问卷', type: '综合类', question_count: 10, created_at: '2026-07-08', questions: [
      { id: 1, text: '你如何进行用户需求分析？', type: '问答题' },
      { id: 2, text: '产品开发过程中遇到需求变更如何处理？', type: '问答题' },
    ]},
    { id: 3, name: '行为面试问卷', type: '行为类', question_count: 5, created_at: '2026-07-05', questions: [
      { id: 1, text: '描述一次你遇到的最大挑战，以及如何解决的？', type: '问答题' },
      { id: 2, text: '你如何处理团队中的冲突？', type: '问答题' },
    ]},
    { id: 4, name: 'Java开发问卷', type: '技术类', question_count: 12, created_at: '2026-07-01', questions: [
      { id: 1, text: 'Java中HashMap和ConcurrentHashMap的区别？', type: '多选题', options: ['线程安全', '性能', '锁机制', '数据结构'] },
      { id: 2, text: '什么是JVM内存模型？', type: '问答题' },
    ]},
  ]);
  const [formData, setFormData] = useState({
    name: '',
    type: '技术类',
    requirements: '',
  });

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value,
    });
  };

  const generateQuestionnaire = () => {
    setGenerating(true);
    setTimeout(() => {
      const newQuestionnaire: Questionnaire = {
        id: Date.now(),
        name: formData.name || 'AI生成的问卷',
        type: formData.type,
        question_count: 6,
        created_at: new Date().toISOString().split('T')[0],
        questions: [
          { id: 1, text: '请描述你的项目经验', type: '问答题' },
          { id: 2, text: '你最擅长的技术栈是什么？', type: '多选题', options: ['前端', '后端', '移动端', '全栈'] },
          { id: 3, text: '你如何学习新技术？', type: '问答题' },
          { id: 4, text: '描述一次成功的项目经历', type: '问答题' },
          { id: 5, text: '你对加班的看法？', type: '多选题', options: ['接受', '不接受', '视情况而定'] },
          { id: 6, text: '你的职业规划是什么？', type: '问答题' },
        ],
      };
      setQuestionnaires([newQuestionnaire, ...questionnaires]);
      setGenerating(false);
      setShowModal(false);
      setFormData({ name: '', type: '技术类', requirements: '' });
    }, 2000);
  };

  const manualCreate = () => {
    if (!formData.name) return;
    const newQuestionnaire: Questionnaire = {
      id: Date.now(),
      name: formData.name,
      type: formData.type,
      question_count: 0,
      created_at: new Date().toISOString().split('T')[0],
      questions: [],
    };
    setQuestionnaires([newQuestionnaire, ...questionnaires]);
    setShowModal(false);
    setFormData({ name: '', type: '技术类', requirements: '' });
  };

  const viewQuestionnaire = (q: Questionnaire) => {
    setSelectedQuestionnaire(q);
    setShowDetailModal(true);
  };

  const editQuestionnaire = (q: Questionnaire) => {
    setSelectedQuestionnaire(q);
    setShowEditModal(true);
  };

  const deleteQuestionnaire = (id: number) => {
    setQuestionnaires(questionnaires.filter(q => q.id !== id));
  };

  const saveEdit = () => {
    if (!selectedQuestionnaire) return;
    setQuestionnaires(questionnaires.map(q => 
      q.id === selectedQuestionnaire!.id ? selectedQuestionnaire! : q
    ));
    setShowEditModal(false);
    setSelectedQuestionnaire(null);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-slate-500">共 {questionnaires.length} 份问卷</p>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors shadow-sm"
        >
          <Plus size={18} />
          <span>创建问卷</span>
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {questionnaires.map((q) => (
          <div key={q.id} className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-md transition-shadow">
            <div className="flex items-start justify-between mb-4">
              <div className="p-3 bg-blue-50 rounded-xl">
                <FileQuestion className="text-blue-600" size={24} />
              </div>
              <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${
                q.type === '技术类' ? 'bg-blue-100 text-blue-700' :
                q.type === '行为类' ? 'bg-purple-100 text-purple-700' : 'bg-amber-100 text-amber-700'
              }`}>
                {q.type}
              </span>
            </div>
            <h3 className="font-semibold text-slate-800 mb-2">{q.name}</h3>
            <p className="text-sm text-slate-500 mb-4">{q.question_count} 道题目</p>
            <div className="flex items-center justify-between pt-4 border-t border-slate-100">
              <span className="text-xs text-slate-400">创建于 {q.created_at}</span>
              <div className="flex items-center gap-1">
                <button onClick={() => viewQuestionnaire(q)} className="p-1.5 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors" title="查看">
                  <Eye size={16} />
                </button>
                <button onClick={() => editQuestionnaire(q)} className="p-1.5 text-slate-400 hover:text-amber-600 hover:bg-amber-50 rounded-lg transition-colors" title="编辑">
                  <Edit size={16} />
                </button>
                <button onClick={() => deleteQuestionnaire(q.id)} className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors" title="删除">
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-lg p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">创建问卷</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">问卷名称</label>
                <input
                  type="text"
                  name="name"
                  value={formData.name}
                  onChange={handleInputChange}
                  placeholder="请输入问卷名称"
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">问卷类型</label>
                <select name="type" value={formData.type} onChange={handleInputChange} className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="技术类">技术类</option>
                  <option value="行为类">行为类</option>
                  <option value="综合类">综合类</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">职位要求（AI生成用）</label>
                <textarea
                  name="requirements"
                  value={formData.requirements}
                  onChange={handleInputChange}
                  placeholder="请输入职位要求，AI将根据此生成问卷"
                  rows={4}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                />
              </div>
              <button
                onClick={generateQuestionnaire}
                disabled={generating}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-purple-600 to-blue-600 text-white rounded-lg hover:from-purple-700 hover:to-blue-700 transition-all disabled:opacity-50"
              >
                <Sparkles size={18} />
                {generating ? 'AI生成中...' : 'AI智能生成问卷'}
              </button>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowModal(false)}
                  className="flex-1 px-4 py-2 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors"
                >
                  取消
                </button>
                <button onClick={manualCreate} className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                  手动创建
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showDetailModal && selectedQuestionnaire && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-2xl p-6 shadow-xl max-h-[80vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-slate-800">{selectedQuestionnaire.name}</h3>
              <button onClick={() => setShowDetailModal(false)} className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg">
                <X size={20} />
              </button>
            </div>
            <div className="flex items-center gap-4 mb-6">
              <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${
                selectedQuestionnaire.type === '技术类' ? 'bg-blue-100 text-blue-700' :
                selectedQuestionnaire.type === '行为类' ? 'bg-purple-100 text-purple-700' : 'bg-amber-100 text-amber-700'
              }`}>
                {selectedQuestionnaire.type}
              </span>
              <span className="text-sm text-slate-500">{selectedQuestionnaire.question_count} 道题目</span>
              <span className="text-sm text-slate-400">创建于 {selectedQuestionnaire.created_at}</span>
            </div>
            <div className="space-y-4">
              {selectedQuestionnaire.questions?.map((q, index) => (
                <div key={q.id} className="p-4 bg-slate-50 rounded-lg">
                  <div className="flex items-start gap-3">
                    <span className="flex-shrink-0 w-7 h-7 bg-blue-100 text-blue-600 rounded-full flex items-center justify-center text-sm font-medium">
                      {index + 1}
                    </span>
                    <div className="flex-1">
                      <p className="font-medium text-slate-800">{q.text}</p>
                      <span className="inline-block mt-1 px-2 py-0.5 bg-slate-200 text-slate-600 rounded text-xs">
                        {q.type}
                      </span>
                      {q.options && (
                        <ul className="mt-2 space-y-1">
                          {q.options.map((option, optIndex) => (
                            <li key={optIndex} className="text-sm text-slate-600 flex items-center gap-2">
                              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full"></span>
                              {option}
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

      {showEditModal && selectedQuestionnaire && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-lg p-6 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-slate-800">编辑问卷</h3>
              <button onClick={() => setShowEditModal(false)} className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg">
                <X size={20} />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">问卷名称</label>
                <input
                  type="text"
                  value={selectedQuestionnaire.name}
                  onChange={(e) => setSelectedQuestionnaire({ ...selectedQuestionnaire, name: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">问卷类型</label>
                <select value={selectedQuestionnaire.type} onChange={(e) => setSelectedQuestionnaire({ ...selectedQuestionnaire, type: e.target.value })} className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="技术类">技术类</option>
                  <option value="行为类">行为类</option>
                  <option value="综合类">综合类</option>
                </select>
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  onClick={() => setShowEditModal(false)}
                  className="flex-1 px-4 py-2 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors"
                >
                  取消
                </button>
                <button onClick={saveEdit} className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                  <Save size={16} />
                  保存
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Questionnaires;

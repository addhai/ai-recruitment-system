import React, { useState } from 'react';
import { Plus, Tag, Phone, Calendar, Clock, Filter, MoreVertical, X, Save } from 'lucide-react';

interface ContactRecord {
  id: number;
  date: string;
  type: string;
  content: string;
}

interface Candidate {
  id: number;
  name: string;
  position: string;
  tags: string[];
  status: string;
  last_contact: string;
  next_contact: string | null;
  notes: string;
  contactRecords: ContactRecord[];
}

const TalentPool: React.FC = () => {
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [showAddModal, setShowAddModal] = useState(false);
  const [expandedContacts, setExpandedContacts] = useState<number[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([
    { id: 1, name: '张小明', position: '全栈工程师', tags: ['前端', '后端', 'React', 'Node.js'], status: 'active', last_contact: '2026-07-10', next_contact: '2026-07-20', notes: '沟通能力强，薪资期望略高', contactRecords: [
      { id: 1, date: '2026-07-10', type: '电话', content: '初步沟通，介绍公司情况' },
      { id: 2, date: '2026-07-05', type: '微信', content: '发送职位介绍资料' },
    ]},
    { id: 2, name: '李小红', position: '产品经理', tags: ['B端产品', '数据分析'], status: 'active', last_contact: '2026-07-08', next_contact: '2026-07-18', notes: '有大厂经验，可内推', contactRecords: [
      { id: 1, date: '2026-07-08', type: '邮件', content: '发送面试邀请' },
    ]},
    { id: 3, name: '王大伟', position: '架构师', tags: ['架构设计', '微服务', '高并发'], status: 'active', last_contact: '2026-07-05', next_contact: '2026-08-05', notes: '技术深度足够，等待机会', contactRecords: [] },
    { id: 4, name: '陈美丽', position: 'UI设计师', tags: ['UI设计', '交互设计', 'Figma'], status: 'passive', last_contact: '2026-06-20', next_contact: null, notes: '暂时不考虑机会', contactRecords: [
      { id: 1, date: '2026-06-20', type: '电话', content: '了解当前状态，暂不考虑' },
    ]},
    { id: 5, name: '刘建国', position: '技术总监', tags: ['团队管理', '技术规划'], status: 'active', last_contact: '2026-07-12', next_contact: '2026-07-25', notes: '有创业经验，潜力大', contactRecords: [] },
  ]);
  const [formData, setFormData] = useState({
    name: '',
    position: '',
    tags: '',
    notes: '',
  });
  const [newContact, setNewContact] = useState<{ candidateId: number; type: string; content: string } | null>(null);

  const allTags = ['前端', '后端', 'React', 'Node.js', 'B端产品', '架构设计', 'UI设计', '团队管理'];

  const filteredCandidates = selectedTags.length > 0
    ? candidates.filter(c => c.tags.some(tag => selectedTags.includes(tag)))
    : candidates;

  const toggleTag = (tag: string) => {
    setSelectedTags(prev => 
      prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]
    );
  };

  const toggleContactRecords = (id: number) => {
    setExpandedContacts(prev => 
      prev.includes(id) ? prev.filter(cid => cid !== id) : [...prev, id]
    );
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value,
    });
  };

  const addCandidate = () => {
    if (!formData.name || !formData.position) return;
    const tagArray = formData.tags.split(',').map(t => t.trim()).filter(t => t);
    const newCandidate: Candidate = {
      id: Date.now(),
      name: formData.name,
      position: formData.position,
      tags: tagArray.length > 0 ? tagArray : ['待定'],
      status: 'active',
      last_contact: new Date().toISOString().split('T')[0],
      next_contact: null,
      notes: formData.notes,
      contactRecords: [],
    };
    setCandidates([newCandidate, ...candidates]);
    setShowAddModal(false);
    setFormData({ name: '', position: '', tags: '', notes: '' });
  };

  const addContactRecord = (candidateId: number) => {
    if (!newContact?.content.trim()) return;
    const record: ContactRecord = {
      id: Date.now(),
      date: new Date().toISOString().split('T')[0],
      type: newContact.type,
      content: newContact.content,
    };
    setCandidates(candidates.map(c => 
      c.id === candidateId 
        ? { ...c, contactRecords: [record, ...c.contactRecords], last_contact: record.date }
        : c
    ));
    setNewContact(null);
  };

  const getStatusText = (status: string) => {
    return status === 'active' ? '活跃' : '不活跃';
  };

  const getStatusColor = (status: string) => {
    return status === 'active' 
      ? 'bg-green-100 text-green-700' 
      : 'bg-slate-100 text-slate-600';
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-slate-500">共 {filteredCandidates.length} 位候选人{selectedTags.length > 0 && `（已筛选）`}</p>
        </div>
        <button onClick={() => setShowAddModal(true)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors shadow-sm">
          <Plus size={18} />
          <span>加入人才池</span>
        </button>
      </div>

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
            <button
              onClick={() => setSelectedTags([])}
              className="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-700"
            >
              清除筛选
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filteredCandidates.map((c) => (
          <div key={c.id} className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-md transition-shadow">
            <div className="flex items-start justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white font-bold">
                  {c.name.charAt(0)}
                </div>
                <div>
                  <h3 className="font-semibold text-slate-800">{c.name}</h3>
                  <p className="text-sm text-slate-500">{c.position}</p>
                </div>
              </div>
              <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${getStatusColor(c.status)}`}>
                {getStatusText(c.status)}
              </span>
            </div>

            <div className="flex flex-wrap gap-1.5 mb-4">
              {c.tags.map((tag) => (
                <span
                  key={tag}
                  className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded text-xs"
                >
                  {tag}
                </span>
              ))}
            </div>

            <div className="space-y-2 text-sm text-slate-500 mb-4">
              <div className="flex items-center gap-2">
                <Phone size={14} />
                <span>上次联系: {c.last_contact}</span>
              </div>
              {c.next_contact && (
                <div className="flex items-center gap-2">
                  <Clock size={14} />
                  <span>下次联系: {c.next_contact}</span>
                </div>
              )}
            </div>

            {c.notes && (
              <p className="text-sm text-slate-600 bg-slate-50 rounded-lg p-3 mb-4">
                {c.notes}
              </p>
            )}

            <button
              onClick={() => toggleContactRecords(c.id)}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 text-sm text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-lg transition-colors mb-2"
            >
              <Phone size={14} />
              记录联系
              <span className="text-xs text-slate-400">({c.contactRecords.length})</span>
            </button>

            {expandedContacts.includes(c.id) && (
              <div className="bg-slate-50 rounded-lg p-3 mb-3">
                {c.contactRecords.length > 0 && (
                  <div className="space-y-2 mb-3">
                    {c.contactRecords.map((record) => (
                      <div key={record.id} className="p-2 bg-white rounded border border-slate-100">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs font-medium text-slate-600">{record.date}</span>
                          <span className="px-1.5 py-0.5 bg-blue-100 text-blue-600 rounded text-xs">{record.type}</span>
                        </div>
                        <p className="text-sm text-slate-600">{record.content}</p>
                      </div>
                    ))}
                  </div>
                )}
                {!newContact || newContact.candidateId !== c.id ? (
                  <button onClick={() => setNewContact({ candidateId: c.id, type: '电话', content: '' })} className="w-full flex items-center justify-center gap-2 px-3 py-2 text-sm text-blue-600 hover:bg-blue-100 rounded-lg transition-colors">
                    <Plus size={14} />
                    添加联系记录
                  </button>
                ) : (
                  <div className="space-y-2">
                    <select value={newContact.type} onChange={(e) => setNewContact({ ...newContact, type: e.target.value })} className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm">
                      <option value="电话">电话</option>
                      <option value="微信">微信</option>
                      <option value="邮件">邮件</option>
                      <option value="面谈">面谈</option>
                    </select>
                    <textarea
                      value={newContact.content}
                      onChange={(e) => setNewContact({ ...newContact, content: e.target.value })}
                      placeholder="请输入联系内容"
                      rows={2}
                      className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm resize-none"
                    />
                    <div className="flex gap-2">
                      <button onClick={() => setNewContact(null)} className="flex-1 px-3 py-2 text-sm text-slate-600 hover:bg-slate-200 rounded-lg transition-colors">
                        取消
                      </button>
                      <button onClick={() => addContactRecord(c.id)} className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors">
                        <Save size={14} />
                        保存
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            <button className="w-full p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors">
              <MoreVertical size={18} />
            </button>
          </div>
        ))}
      </div>

      {showAddModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-lg p-6 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-slate-800">加入人才池</h3>
              <button onClick={() => setShowAddModal(false)} className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg">
                <X size={20} />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">姓名</label>
                <input
                  type="text"
                  name="name"
                  value={formData.name}
                  onChange={handleInputChange}
                  placeholder="请输入姓名"
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">职位</label>
                <input
                  type="text"
                  name="position"
                  value={formData.position}
                  onChange={handleInputChange}
                  placeholder="请输入职位"
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">标签（逗号分隔）</label>
                <input
                  type="text"
                  name="tags"
                  value={formData.tags}
                  onChange={handleInputChange}
                  placeholder="例如：前端,React,Node.js"
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">备注</label>
                <textarea
                  name="notes"
                  value={formData.notes}
                  onChange={handleInputChange}
                  placeholder="请输入备注信息"
                  rows={3}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                />
              </div>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowAddModal(false)}
                  className="flex-1 px-4 py-2 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors"
                >
                  取消
                </button>
                <button onClick={addCandidate} className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
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

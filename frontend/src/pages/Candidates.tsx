import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Search, Filter, MoreVertical, FileText, Eye, Trash2 } from 'lucide-react';
import { getCandidates, createCandidate, deleteCandidate } from '../services/candidates';
import type { Candidate } from '../types';

const Candidates: React.FC = () => {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [newCandidate, setNewCandidate] = useState({
    name: '',
    email: '',
    phone: '',
    position: '',
    source: '',
  });
  const navigate = useNavigate();

  useEffect(() => {
    const mockData: Candidate[] = [
      { id: 1, name: '张明', email: 'zhangming@example.com', phone: '13800138001', resume_file: 'resume1.pdf', status: 'pending', source: '拉勾网', position: '高级前端工程师', created_at: '2026-07-17T10:30:00', updated_at: '2026-07-17T10:30:00' },
      { id: 2, name: '李华', email: 'lihua@example.com', phone: '13800138002', resume_file: 'resume2.pdf', status: 'interviewed', source: 'BOSS直聘', position: '产品经理', created_at: '2026-07-16T14:20:00', updated_at: '2026-07-16T14:20:00' },
      { id: 3, name: '王芳', email: 'wangfang@example.com', phone: '13800138003', resume_file: 'resume3.pdf', status: 'hired', source: '内部推荐', position: 'UI设计师', created_at: '2026-07-15T09:15:00', updated_at: '2026-07-15T09:15:00' },
      { id: 4, name: '刘伟', email: 'liuwei@example.com', phone: '13800138004', resume_file: 'resume4.pdf', status: 'pending', source: '智联招聘', position: '后端开发工程师', created_at: '2026-07-14T16:45:00', updated_at: '2026-07-14T16:45:00' },
      { id: 5, name: '陈静', email: 'chenjing@example.com', phone: '13800138005', resume_file: 'resume5.pdf', status: 'interviewed', source: '猎头推荐', position: 'Java开发工程师', created_at: '2026-07-13T11:00:00', updated_at: '2026-07-13T11:00:00' },
      { id: 6, name: '赵磊', email: 'zhaolei@example.com', phone: '13800138006', resume_file: 'resume6.pdf', status: 'pending', source: '拉勾网', position: '测试工程师', created_at: '2026-07-12T08:30:00', updated_at: '2026-07-12T08:30:00' },
    ];
    
    const fetchData = async () => {
      try {
        const data = await getCandidates({ search }).catch(() => mockData);
        setCandidates(data);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [search]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createCandidate(newCandidate).catch(() => {
        const newId = Math.max(...candidates.map(c => c.id)) + 1;
        const newItem: Candidate = {
          id: newId,
          ...newCandidate,
          resume_file: null,
          status: 'pending',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        setCandidates([newItem, ...candidates]);
        return newItem;
      });
      setShowModal(false);
      setNewCandidate({ name: '', email: '', phone: '', position: '', source: '' });
    } catch (err) {
      console.error(err);
    }
  };

  const handleDelete = async (id: number) => {
    if (confirm('确定要删除该候选人吗？')) {
      try {
        await deleteCandidate(id).catch(() => {});
        setCandidates(candidates.filter(c => c.id !== id));
      } catch (err) {
        console.error(err);
      }
    }
  };

  const getStatusText = (status: string) => {
    const map: Record<string, string> = {
      pending: '待处理',
      interviewed: '面试中',
      hired: '已录用',
      rejected: '已拒绝',
    };
    return map[status] || status;
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'hired':
        return 'bg-green-100 text-green-700';
      case 'interviewed':
        return 'bg-blue-100 text-blue-700';
      case 'pending':
        return 'bg-amber-100 text-amber-700';
      case 'rejected':
        return 'bg-red-100 text-red-700';
      default:
        return 'bg-slate-100 text-slate-700';
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-slate-500">共 {candidates.length} 位候选人</p>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors shadow-sm"
        >
          <Plus size={18} />
          <span>新增候选人</span>
        </button>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200">
        <div className="p-4 border-b border-slate-100 flex items-center gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
            <input
              type="text"
              placeholder="搜索候选人姓名、邮箱..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-10 pr-4 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <button className="flex items-center gap-2 px-4 py-2 border border-slate-200 rounded-lg text-sm text-slate-600 hover:bg-slate-50 transition-colors">
            <Filter size={16} />
            <span>筛选</span>
          </button>
        </div>

        {loading ? (
          <div className="p-8 text-center text-slate-500">加载中...</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-50">
                <tr className="text-left text-sm text-slate-500">
                  <th className="px-6 py-3 font-medium">候选人</th>
                  <th className="px-6 py-3 font-medium">职位</th>
                  <th className="px-6 py-3 font-medium">来源</th>
                  <th className="px-6 py-3 font-medium">状态</th>
                  <th className="px-6 py-3 font-medium">创建时间</th>
                  <th className="px-6 py-3 font-medium text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {candidates.map((candidate) => (
                  <tr key={candidate.id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white font-medium">
                          {candidate.name.charAt(0)}
                        </div>
                        <div>
                          <p className="font-medium text-slate-800">{candidate.name}</p>
                          <p className="text-sm text-slate-500">{candidate.email}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-slate-600">{candidate.position}</td>
                    <td className="px-6 py-4 text-slate-600">{candidate.source}</td>
                    <td className="px-6 py-4">
                      <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${getStatusColor(candidate.status)}`}>
                        {getStatusText(candidate.status)}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-slate-500 text-sm">
                      {new Date(candidate.created_at).toLocaleDateString('zh-CN')}
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => navigate(`/candidates/${candidate.id}`)}
                          className="p-1.5 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
                          title="查看详情"
                        >
                          <Eye size={16} />
                        </button>
                        <button
                          className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
                          title="简历"
                        >
                          <FileText size={16} />
                        </button>
                        <button
                          onClick={() => handleDelete(candidate.id)}
                          className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                          title="删除"
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl w-full max-w-md p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">新增候选人</h3>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">姓名 *</label>
                <input
                  type="text"
                  value={newCandidate.name}
                  onChange={(e) => setNewCandidate({ ...newCandidate, name: e.target.value })}
                  required
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">邮箱</label>
                <input
                  type="email"
                  value={newCandidate.email}
                  onChange={(e) => setNewCandidate({ ...newCandidate, email: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">电话</label>
                <input
                  type="text"
                  value={newCandidate.phone}
                  onChange={(e) => setNewCandidate({ ...newCandidate, phone: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">应聘职位</label>
                <input
                  type="text"
                  value={newCandidate.position}
                  onChange={(e) => setNewCandidate({ ...newCandidate, position: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">来源</label>
                <input
                  type="text"
                  value={newCandidate.source}
                  onChange={(e) => setNewCandidate({ ...newCandidate, source: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="flex-1 px-4 py-2 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                >
                  创建
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default Candidates;

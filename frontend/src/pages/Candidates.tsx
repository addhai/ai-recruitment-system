import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Plus, Search, Filter, MoreVertical, FileText, Eye, Trash2, UploadCloud, Loader2 } from 'lucide-react';
import { getCandidates, createCandidate, deleteCandidate, uploadResume } from '../services/candidates';
import type { Candidate } from '../types';

const Candidates: React.FC = () => {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [newCandidate, setNewCandidate] = useState({
    name: '',
    email: '',
    phone: '',
    position: '',
    source: '',
  });
  // 新增：上传简历相关状态
  const [createdCandidateId, setCreatedCandidateId] = useState<number | null>(null);
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [resumeDragOver, setResumeDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<any>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // 从 URL 参数初始化搜索词（支持 Header 全局搜索跳转）
  useEffect(() => {
    const urlSearch = searchParams.get('search');
    if (urlSearch) {
      setSearch(urlSearch);
    }
  }, [searchParams]);

  // 搜索防抖：输入停止 300ms 后才真正触发请求
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const data = await getCandidates({ search: debouncedSearch });
        setCandidates(data);
      } catch (err) {
        console.error('加载候选人失败', err);
        setCandidates([]);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [debouncedSearch]);

  const resetModal = () => {
    setShowModal(false);
    setNewCandidate({ name: '', email: '', phone: '', position: '', source: '' });
    setCreatedCandidateId(null);
    setResumeFile(null);
    setUploadResult(null);
    setUploadError(null);
    setResumeDragOver(false);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const created = await createCandidate(newCandidate);
      setCreatedCandidateId(created.id);
      // 刷新列表
      const data = await getCandidates({ search: debouncedSearch });
      setCandidates(data);
    } catch (err) {
      console.error('创建候选人失败', err);
      alert('创建候选人失败，请检查后端服务是否启动');
    }
  };

  const handleFileSelect = (file: File) => {
    const allowedExts = ['.txt', '.pdf', '.docx'];
    const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
    if (!allowedExts.includes(ext)) {
      setUploadError('仅支持 .txt、.pdf、.docx 文件');
      return;
    }
    setResumeFile(file);
    setUploadError(null);
  };

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFileSelect(file);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setResumeDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFileSelect(file);
  };

  const handleUploadResume = async () => {
    if (!resumeFile || !createdCandidateId) return;
    setUploading(true);
    setUploadError(null);
    try {
      const result = await uploadResume(createdCandidateId, resumeFile);
      setUploadResult(result);
      // 刷新列表
      const data = await getCandidates({ search: debouncedSearch });
      setCandidates(data);
    } catch (err: any) {
      setUploadError(err.message || '简历上传失败');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (confirm('确定要删除该候选人吗？')) {
      try {
        await deleteCandidate(id);
        setCandidates(candidates.filter(c => c.id !== id));
      } catch (err) {
        console.error(err);
        alert('删除失败');
      }
    }
  };

  const getStatusText = (status: string) => {
    const map: Record<string, string> = {
      pending: '待处理',
      interviewed: '面试中',
      interviewing: '面试中',
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
      case 'interviewing':
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
                          onClick={() => navigate(`/candidates/${candidate.id}`)}
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
          <div className="bg-white rounded-xl w-full max-w-md p-6 shadow-xl max-h-[90vh] overflow-y-auto">
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

              {/* 简历上传区域：仅在候选人创建成功后显示 */}
              {createdCandidateId && (
                <div className="space-y-3 p-4 bg-blue-50/50 border border-blue-100 rounded-lg">
                  <p className="text-sm font-medium text-slate-700">
                    候选人已创建（ID: {createdCandidateId}），可上传简历
                  </p>
                  <div
                    onDragOver={(e) => { e.preventDefault(); setResumeDragOver(true); }}
                    onDragLeave={() => setResumeDragOver(false)}
                    onDrop={handleDrop}
                    onClick={() => fileInputRef.current?.click()}
                    className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors ${
                      resumeDragOver ? 'border-blue-500 bg-blue-50' : 'border-slate-300 hover:border-blue-400'
                    }`}
                  >
                    <UploadCloud className="mx-auto text-slate-400 mb-2" size={28} />
                    {resumeFile ? (
                      <p className="text-sm text-slate-700 font-medium">{resumeFile.name}</p>
                    ) : (
                      <>
                        <p className="text-sm text-slate-600">点击或拖拽简历文件到此处</p>
                        <p className="text-xs text-slate-400 mt-1">支持 .txt / .pdf / .docx</p>
                      </>
                    )}
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".txt,.pdf,.docx"
                      onChange={handleFileInput}
                      className="hidden"
                    />
                  </div>

                  {uploadError && (
                    <p className="text-xs text-red-600">{uploadError}</p>
                  )}

                  {resumeFile && !uploadResult && (
                    <button
                      type="button"
                      onClick={handleUploadResume}
                      disabled={uploading}
                      className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm disabled:opacity-50"
                    >
                      {uploading ? <Loader2 className="animate-spin" size={16} /> : <UploadCloud size={16} />}
                      {uploading ? '上传解析中...' : '上传并解析简历'}
                    </button>
                  )}

                  {uploadResult && (
                    <div className="bg-white border border-green-200 rounded-lg p-3 text-xs space-y-1">
                      <p className="text-green-700 font-medium">✓ 简历上传解析成功</p>
                      {uploadResult.skills && uploadResult.skills.length > 0 && (
                        <p className="text-slate-600">技能: {Array.isArray(uploadResult.skills) ? uploadResult.skills.join('、') : String(uploadResult.skills)}</p>
                      )}
                      {uploadResult.experience && (
                        <p className="text-slate-600">经验: {uploadResult.experience}</p>
                      )}
                      {uploadResult.education && (
                        <p className="text-slate-600">教育: {uploadResult.education}</p>
                      )}
                    </div>
                  )}
                </div>
              )}

              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={resetModal}
                  className="flex-1 px-4 py-2 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors"
                >
                  {createdCandidateId ? '完成' : '取消'}
                </button>
                {!createdCandidateId && (
                  <button
                    type="submit"
                    className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                  >
                    创建
                  </button>
                )}
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default Candidates;

import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Calendar, FileText, Award, Target, Users, Play, Loader2, AlertCircle } from 'lucide-react';
import { getCandidate, getResume, runWorkflow } from '../services/candidates';
import { apiRequest, API_BASE_URL } from '../services/api';
import type { Candidate } from '../types';

interface InterviewItem {
  id: number;
  candidate_id: number;
  position: string;
  round: number;
  status: string;
  interviewer_id: number | null;
  scheduled_at: string | null;
  completed_at: string | null;
  score: number | null;
  feedback: string | null;
  notes: string | null;
  created_at: string;
}

interface EvaluationItem {
  id: number;
  candidate_id: number;
  evaluator_id: number;
  dimension: string;
  score: number;
  comment: string | null;
  created_at: string;
}

interface WorkflowResult {
  candidate_id: number;
  candidate_name: string;
  position: string | null;
  final_decision: string;
  overall_score: number;
  skill_match_score: number;
  experience_match_score: number;
  education_match_score: number;
  culture_match_score: number;
  workflow_progress: number;
  current_step: string;
  analysis: {
    skills_analysis: string;
    experience_analysis: string;
    education_analysis: string;
    culture_analysis: string;
    recommendation: string;
    [key: string]: any;
  };
  completed_at: string;
}

const CandidateDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('overview');
  const [runningWorkflow, setRunningWorkflow] = useState(false);
  const [workflowProgress, setWorkflowProgress] = useState(0);

  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [resume, setResume] = useState<any>(null);
  const [interviews, setInterviews] = useState<InterviewItem[]>([]);
  const [evaluations, setEvaluations] = useState<EvaluationItem[]>([]);
  const [workflowResult, setWorkflowResult] = useState<WorkflowResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [workflowError, setWorkflowError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    const fetchData = async () => {
      setLoading(true);
      setError(null);
      try {
        const candId = Number(id);
        // 并行获取候选人基本信息、简历、面试记录、评估记录
        const [cand, ints, evals] = await Promise.all([
          getCandidate(candId),
          apiRequest<InterviewItem[]>(`/interviews/?candidate_id=${candId}`).catch(() => []),
          apiRequest<EvaluationItem[]>(`/evaluations/?candidate_id=${candId}`).catch(() => []),
        ]);
        setCandidate(cand);
        setInterviews(ints);
        setEvaluations(evals);

        // 加载历史工作流结果（真实数据，避免回到空进度）
        try {
          const wf = await apiRequest<any>(`/candidates/${candId}/workflow`);
          if (wf?.status === 'completed' && wf?.results) {
            setWorkflowResult(wf.results);
            setWorkflowProgress(100);
          } else if (wf?.status === 'running') {
            setWorkflowProgress(wf?.progress ?? 0);
          }
        } catch {
          // 该候选人暂无工作流运行记录，忽略
        }

        // 简历获取失败不阻塞页面
        try {
          const res = await getResume(candId);
          setResume(res);
        } catch {
          setResume(null);
        }
      } catch (err: any) {
        setError(err.message || '加载候选人详情失败');
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [id]);

  const runWorkflowHandler = async () => {
    if (!candidate) return;
    setRunningWorkflow(true);
    setWorkflowProgress(0);
    setWorkflowError(null);
    setWorkflowResult(null);

    const token = localStorage.getItem('token');
    let sseClosed = false;
    const controller = new AbortController();

    // 订阅 SSE 真实工作流进度（后端逐节点推送，不再前端伪造）
    const connectProgressSSE = () => {
      if (!token) return;
      fetch(`${API_BASE_URL}/sse/notifications`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      })
        .then((response) => {
          if (!response.body) return;
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          const read = (): void => {
            reader
              .read()
              .then(({ done, value }) => {
                if (done || sseClosed) return;
                buffer += decoder.decode(value, { stream: true });
                const chunks = buffer.split('\n\n');
                buffer = chunks.pop() || '';
                for (const chunk of chunks) {
                  const dataLine = chunk.split('\n').find((line) => line.startsWith('data: '));
                  if (!dataLine) continue;
                  try {
                    const msg = JSON.parse(dataLine.slice(6));
                    if (msg.type === 'workflow_progress' && msg.candidate_id === candidate.id) {
                      setWorkflowProgress(Number(msg.progress) || 0);
                    }
                  } catch {
                    // 忽略无法解析的消息
                  }
                }
                read();
              })
              .catch(() => {
                // 读取中断时静默退出
              });
          };
          read();
        })
        .catch(() => {
          // 连接失败时降级为仅依赖最终 API 返回
        });
    };
    connectProgressSSE();

    try {
      const positionRequirements = candidate.position || '';
      const result = await runWorkflow(candidate.id, positionRequirements);
      setWorkflowResult(result);
      setWorkflowProgress(100);
    } catch (err: any) {
      setWorkflowError(err.message || 'AI 评估失败，请检查后端服务是否启动');
    } finally {
      sseClosed = true;
      controller.abort();
      setRunningWorkflow(false);
    }
  };

  const tabs = [
    { id: 'overview', label: '概览' },
    { id: 'resume', label: '简历' },
    { id: 'interviews', label: '面试记录' },
    { id: 'evaluations', label: '评估' },
    { id: 'workflow', label: 'AI工作流' },
  ];

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
      case 'hired': return 'bg-green-100 text-green-700';
      case 'interviewed':
      case 'interviewing': return 'bg-blue-100 text-blue-700';
      case 'pending': return 'bg-amber-100 text-amber-700';
      case 'rejected': return 'bg-red-100 text-red-700';
      default: return 'bg-slate-100 text-slate-700';
    }
  };

  // 从简历解析数据中提取展示信息
  const parsedData = resume?.parsed_data || (resume as any)?.parsed_data || null;
  const skills: string[] = (() => {
    if (resume?.skills && Array.isArray(resume.skills)) return resume.skills;
    if (parsedData?.skills_technical && Array.isArray(parsedData.skills_technical)) return parsedData.skills_technical;
    if (parsedData?.skills && Array.isArray(parsedData.skills)) return parsedData.skills;
    if (parsedData?.skills && typeof parsedData.skills === 'object') {
      const all = Object.values(parsedData.skills).flat();
      return all.filter((s: any) => typeof s === 'string');
    }
    return [];
  })();
  const experience = resume?.experience || parsedData?.experience_text || '';
  const education = resume?.education || parsedData?.education_text || '';
  const experienceList: any[] = Array.isArray(parsedData?.experience) ? parsedData.experience : [];
  const educationList: any[] = Array.isArray(parsedData?.education) ? parsedData.education : [];
  const projectList: any[] = Array.isArray(parsedData?.projects) ? parsedData.projects : [];

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-12 text-center">
          <Loader2 className="animate-spin mx-auto text-blue-500 mb-3" size={32} />
          <p className="text-slate-500">加载候选人详情...</p>
        </div>
      </div>
    );
  }

  if (error || !candidate) {
    return (
      <div className="space-y-6">
        <button
          onClick={() => navigate('/candidates')}
          className="flex items-center gap-2 text-slate-500 hover:text-slate-700 transition-colors"
        >
          <ArrowLeft size={18} />
          <span>返回列表</span>
        </button>
        <div className="bg-white rounded-xl shadow-sm border border-red-200 p-12 text-center">
          <AlertCircle className="mx-auto text-red-500 mb-3" size={32} />
          <p className="text-red-600">{error || '未找到候选人'}</p>
        </div>
      </div>
    );
  }

  // 工作流评分数据
  const skillScore = workflowResult?.skill_match_score ?? 0;
  const expScore = workflowResult?.experience_match_score ?? 0;
  const cultureScore = workflowResult?.culture_match_score ?? 0;
  const overallScore = workflowResult?.overall_score ?? 0;

  return (
    <div className="space-y-6">
      <button
        onClick={() => navigate('/candidates')}
        className="flex items-center gap-2 text-slate-500 hover:text-slate-700 transition-colors"
      >
        <ArrowLeft size={18} />
        <span>返回列表</span>
      </button>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-4">
            <div className="w-16 h-16 bg-gradient-to-br from-blue-500 to-purple-600 rounded-xl flex items-center justify-center text-white text-2xl font-bold">
              {candidate.name.charAt(0)}
            </div>
            <div>
              <div className="flex items-center gap-3">
                <h2 className="text-xl font-bold text-slate-800">{candidate.name}</h2>
                <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${getStatusColor(candidate.status)}`}>
                  {getStatusText(candidate.status)}
                </span>
              </div>
              <p className="text-slate-600 mt-1">{candidate.position || '未填写职位'}</p>
              <div className="flex items-center gap-4 mt-2 text-sm text-slate-500">
                <span>📧 {candidate.email || '未填写'}</span>
                <span>📱 {candidate.phone || '未填写'}</span>
                <span>📍 来源: {candidate.source || '未填写'}</span>
              </div>
            </div>
          </div>
          <button
            onClick={runWorkflowHandler}
            disabled={runningWorkflow}
            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-lg hover:from-blue-700 hover:to-indigo-700 transition-all shadow-sm disabled:opacity-50"
          >
            {runningWorkflow ? <Loader2 className="animate-spin" size={16} /> : <Play size={16} />}
            {runningWorkflow ? '分析中...' : '启动AI评估'}
          </button>
        </div>
      </div>

      {runningWorkflow && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-medium text-slate-700">AI评估进度</span>
            <span className="text-sm text-blue-600 font-medium">{workflowProgress}%</span>
          </div>
          <div className="w-full bg-slate-100 rounded-full h-2">
            <div
              className="bg-gradient-to-r from-blue-500 to-indigo-600 h-2 rounded-full transition-all duration-500"
              style={{ width: `${workflowProgress}%` }}
            ></div>
          </div>
        </div>
      )}

      {workflowError && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle className="text-red-500 mt-0.5" size={18} />
          <div className="flex-1">
            <p className="text-sm text-red-700 font-medium">AI评估失败</p>
            <p className="text-xs text-red-600 mt-1">{workflowError}</p>
          </div>
        </div>
      )}

      <div className="bg-white rounded-xl shadow-sm border border-slate-200">
        <div className="border-b border-slate-100">
          <nav className="flex gap-1 px-4">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab.id
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-slate-500 hover:text-slate-700'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </div>

        <div className="p-6">
          {activeTab === 'overview' && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="md:col-span-2 space-y-6">
                <div>
                  <h3 className="font-semibold text-slate-800 mb-3 flex items-center gap-2">
                    <Target className="text-blue-600" size={18} />
                    基本信息
                  </h3>
                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div className="p-3 bg-slate-50 rounded-lg">
                      <p className="text-slate-500">工作经验</p>
                      <p className="font-medium text-slate-800 mt-1">{experience || '—'}</p>
                    </div>
                    <div className="p-3 bg-slate-50 rounded-lg">
                      <p className="text-slate-500">学历</p>
                      <p className="font-medium text-slate-800 mt-1">{education || '—'}</p>
                    </div>
                    <div className="p-3 bg-slate-50 rounded-lg col-span-2">
                      <p className="text-slate-500">简历文件</p>
                      <p className="font-medium text-slate-800 mt-1">{resume?.file_name || candidate.resume_file || '未上传'}</p>
                    </div>
                  </div>
                </div>

                <div>
                  <h3 className="font-semibold text-slate-800 mb-3 flex items-center gap-2">
                    <Award className="text-blue-600" size={18} />
                    技能标签
                  </h3>
                  {skills.length > 0 ? (
                    <div className="flex flex-wrap gap-2">
                      {skills.map((skill, idx) => (
                        <span
                          key={`${skill}-${idx}`}
                          className="px-3 py-1.5 bg-blue-50 text-blue-700 rounded-full text-sm"
                        >
                          {typeof skill === 'string' ? skill : JSON.stringify(skill)}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-slate-400">暂无技能数据，请上传简历后查看</p>
                  )}
                </div>
              </div>

              <div className="space-y-6">
                <div className="p-4 bg-gradient-to-br from-blue-50 to-indigo-50 rounded-xl">
                  <h3 className="font-semibold text-slate-800 mb-3">匹配度评分</h3>
                  {workflowResult ? (
                    <div className="space-y-3">
                      <div>
                        <div className="flex justify-between text-sm mb-1">
                          <span className="text-slate-600">技能匹配</span>
                          <span className="font-medium text-slate-800">{skillScore}%</span>
                        </div>
                        <div className="w-full bg-white/50 rounded-full h-2">
                          <div className="bg-blue-500 h-2 rounded-full" style={{ width: `${skillScore}%` }}></div>
                        </div>
                      </div>
                      <div>
                        <div className="flex justify-between text-sm mb-1">
                          <span className="text-slate-600">经验匹配</span>
                          <span className="font-medium text-slate-800">{expScore}%</span>
                        </div>
                        <div className="w-full bg-white/50 rounded-full h-2">
                          <div className="bg-green-500 h-2 rounded-full" style={{ width: `${expScore}%` }}></div>
                        </div>
                      </div>
                      <div>
                        <div className="flex justify-between text-sm mb-1">
                          <span className="text-slate-600">文化匹配</span>
                          <span className="font-medium text-slate-800">{cultureScore}%</span>
                        </div>
                        <div className="w-full bg-white/50 rounded-full h-2">
                          <div className="bg-purple-500 h-2 rounded-full" style={{ width: `${cultureScore}%` }}></div>
                        </div>
                      </div>
                      <div className="pt-3 border-t border-white/50">
                        <div className="flex justify-between">
                          <span className="text-slate-600 font-medium">综合评分</span>
                          <span className="text-xl font-bold text-blue-600">{overallScore}%</span>
                        </div>
                        <div className="mt-2 text-sm text-slate-700">
                          决策：<span className="font-medium">{workflowResult.final_decision}</span>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <p className="text-sm text-slate-500">点击右上角"启动AI评估"获取匹配度评分</p>
                  )}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'resume' && (
            <div className="prose max-w-none">
              <h3 className="font-semibold text-slate-800 mb-4">简历内容</h3>
              {resume ? (
                <div className="bg-slate-50 rounded-lg p-6 text-slate-600 space-y-4">
                  <div>
                    <p className="text-xs text-slate-400 mb-1">简历文件: {resume.file_name}</p>
                  </div>

                  {educationList.length > 0 && (
                    <>
                      <h4 className="font-medium text-slate-800">教育背景</h4>
                      <ul className="list-disc pl-5 space-y-1">
                        {educationList.map((edu, idx) => (
                          <li key={idx}>
                            {typeof edu === 'string' ? edu : Object.entries(edu).map(([k, v]) => `${k}: ${v}`).join(' | ')}
                          </li>
                        ))}
                      </ul>
                    </>
                  )}

                  {experienceList.length > 0 && (
                    <>
                      <h4 className="font-medium text-slate-800">工作经历</h4>
                      <ul className="list-disc pl-5 space-y-1">
                        {experienceList.map((exp, idx) => (
                          <li key={idx}>
                            {typeof exp === 'string' ? exp : Object.entries(exp).map(([k, v]) => `${k}: ${v}`).join(' | ')}
                          </li>
                        ))}
                      </ul>
                    </>
                  )}

                  {projectList.length > 0 && (
                    <>
                      <h4 className="font-medium text-slate-800">项目经验</h4>
                      <ul className="list-disc pl-5 space-y-1">
                        {projectList.map((proj, idx) => (
                          <li key={idx}>
                            {typeof proj === 'string' ? proj : Object.entries(proj).map(([k, v]) => `${k}: ${v}`).join(' | ')}
                          </li>
                        ))}
                      </ul>
                    </>
                  )}

                  {parsedData?.basic_info && (
                    <>
                      <h4 className="font-medium text-slate-800">基本信息</h4>
                      <p className="text-sm">
                        {typeof parsedData.basic_info === 'string'
                          ? parsedData.basic_info
                          : Object.entries(parsedData.basic_info).map(([k, v]) => `${k}: ${v}`).join(' | ')}
                      </p>
                    </>
                  )}

                  {resume.parsed_text && (
                    <>
                      <h4 className="font-medium text-slate-800">简历原文</h4>
                      <pre className="text-xs whitespace-pre-wrap bg-white border border-slate-200 rounded p-3 max-h-96 overflow-y-auto">
                        {resume.parsed_text}
                      </pre>
                    </>
                  )}

                  {educationList.length === 0 && experienceList.length === 0 && projectList.length === 0 && !resume.parsed_text && (
                    <p className="text-slate-400">暂无详细解析数据</p>
                  )}
                </div>
              ) : (
                <div className="bg-slate-50 rounded-lg p-6 text-center">
                  <FileText className="mx-auto text-slate-400 mb-2" size={32} />
                  <p className="text-slate-500">该候选人尚未上传简历</p>
                  <button
                    onClick={() => navigate('/candidates')}
                    className="mt-3 text-sm text-blue-600 hover:text-blue-700"
                  >
                    去候选人列表上传
                  </button>
                </div>
              )}
            </div>
          )}

          {activeTab === 'interviews' && (
            <div className="space-y-4">
              {interviews.length === 0 ? (
                <p className="text-center text-slate-400 py-8">暂无面试记录</p>
              ) : (
                interviews.map((iv) => {
                  const isCompleted = iv.status === 'completed';
                  const iconBg = isCompleted ? 'bg-green-100' : 'bg-blue-100';
                  const iconColor = isCompleted ? 'text-green-600' : 'text-blue-600';
                  const Icon = isCompleted ? Calendar : Users;
                  const tagBg = isCompleted ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700';
                  const tagText = isCompleted ? '已通过' : '待面试';
                  return (
                    <div key={iv.id} className="flex items-center gap-2 p-4 bg-slate-50 rounded-lg">
                      <div className={`w-10 h-10 ${iconBg} rounded-full flex items-center justify-center`}>
                        <Icon className={iconColor} size={18} />
                      </div>
                      <div className="flex-1">
                        <p className="font-medium text-slate-800">第{iv.round}轮面试 - {iv.position}</p>
                        <p className="text-sm text-slate-500">
                          {iv.scheduled_at ? new Date(iv.scheduled_at).toLocaleString('zh-CN') : '时间未定'} ·
                          面试官ID: {iv.interviewer_id || '待定'}
                          {iv.score ? ` · 分数: ${iv.score}` : ''}
                        </p>
                        {iv.feedback && (
                          <p className="text-xs text-slate-500 mt-1">反馈: {iv.feedback}</p>
                        )}
                      </div>
                      <span className={`px-2.5 py-1 ${tagBg} rounded-full text-xs font-medium`}>{tagText}</span>
                    </div>
                  );
                })
              )}
            </div>
          )}

          {activeTab === 'evaluations' && (
            <div className="space-y-4">
              {evaluations.length === 0 ? (
                <p className="text-center text-slate-400 py-8">暂无评估记录</p>
              ) : (
                evaluations.map((ev) => (
                  <div key={ev.id} className="p-4 border border-slate-200 rounded-lg">
                    <div className="flex justify-between items-start mb-2">
                      <h4 className="font-medium text-slate-800">{ev.dimension}</h4>
                      <span className="text-lg font-bold text-blue-600">{ev.score}分</span>
                    </div>
                    <p className="text-sm text-slate-500">评估人ID: {ev.evaluator_id} · {new Date(ev.created_at).toLocaleDateString('zh-CN')}</p>
                    {ev.comment && (
                      <p className="text-sm text-slate-600 mt-2">{ev.comment}</p>
                    )}
                  </div>
                ))
              )}
            </div>
          )}

          {activeTab === 'workflow' && (
            <div className="space-y-4">
              {workflowResult ? (
                <>
                  <div className="p-4 bg-gradient-to-br from-blue-50 to-indigo-50 rounded-xl">
                    <div className="flex items-center justify-between mb-3">
                      <h4 className="font-semibold text-slate-800">最终决策</h4>
                      <span className="text-2xl font-bold text-blue-600">{workflowResult.overall_score}%</span>
                    </div>
                    <p className="text-slate-700 font-medium">{workflowResult.final_decision}</p>
                    <p className="text-xs text-slate-500 mt-1">完成时间: {new Date(workflowResult.completed_at).toLocaleString('zh-CN')}</p>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    {[
                      { label: '简历解析', icon: FileText, done: true },
                      { label: '技能匹配评估', icon: Target, done: true, score: workflowResult.skill_match_score },
                      { label: '文化匹配评估', icon: Users, done: true, score: workflowResult.culture_match_score },
                      { label: '经验匹配评估', icon: Award, done: true, score: workflowResult.experience_match_score },
                    ].map((item, idx) => (
                      <div key={idx} className="flex items-center gap-4 p-4 border border-slate-200 rounded-lg">
                        <div className={`w-10 h-10 rounded-full flex items-center justify-center ${item.done ? 'bg-green-100' : 'bg-blue-100'}`}>
                          <item.icon className={item.done ? 'text-green-600' : 'text-blue-600'} size={18} />
                        </div>
                        <div className="flex-1">
                          <p className="font-medium text-slate-800">{item.label}</p>
                          <p className="text-sm text-slate-500">
                            {item.done ? (item.score !== undefined ? `匹配度 ${item.score}%` : '已完成') : '进行中'}
                          </p>
                        </div>
                        <span className={`text-sm ${item.done ? 'text-green-600' : 'text-blue-600'}`}>
                          {item.done ? '✓' : '...'}
                        </span>
                      </div>
                    ))}
                  </div>

                  {workflowResult.analysis && (
                    <div className="p-4 border border-slate-200 rounded-lg space-y-2">
                      <h4 className="font-medium text-slate-800">分析详情</h4>
                      <p className="text-sm text-slate-600">{workflowResult.analysis.skills_analysis}</p>
                      <p className="text-sm text-slate-600">{workflowResult.analysis.experience_analysis}</p>
                      <p className="text-sm text-slate-600">{workflowResult.analysis.education_analysis}</p>
                      <p className="text-sm text-slate-600">{workflowResult.analysis.culture_analysis}</p>
                      <p className="text-sm text-slate-700 font-medium pt-2 border-t border-slate-100">建议: {workflowResult.analysis.recommendation}</p>
                    </div>
                  )}
                </>
              ) : (
                <div className="text-center py-12">
                  <Play className="mx-auto text-slate-400 mb-3" size={32} />
                  <p className="text-slate-500">尚未运行AI评估工作流</p>
                  <button
                    onClick={runWorkflowHandler}
                    disabled={runningWorkflow}
                    className="mt-4 px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
                  >
                    {runningWorkflow ? '分析中...' : '启动AI评估'}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default CandidateDetail;

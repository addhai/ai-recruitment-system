import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Calendar, FileText, Award, Target, Users, Play } from 'lucide-react';

const CandidateDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('overview');
  const [runningWorkflow, setRunningWorkflow] = useState(false);
  const [workflowProgress, setWorkflowProgress] = useState(0);

  const candidates = [
    { id: 1, name: '张明', email: 'zhangming@example.com', phone: '13800138001', position: '高级前端工程师', status: 'interviewed', source: '拉勾网', experience: '5年', education: '本科', school: '北京邮电大学', major: '计算机科学与技术', skills: ['React', 'TypeScript', 'Node.js', 'Vue', 'Webpack', 'Git'] },
    { id: 2, name: '李华', email: 'lihua@example.com', phone: '13800138002', position: '产品经理', status: 'pending', source: 'BOSS直聘', experience: '3年', education: '硕士', school: '复旦大学', major: '工商管理', skills: ['需求分析', '产品设计', '数据分析', '项目管理'] },
    { id: 3, name: '王芳', email: 'wangfang@example.com', phone: '13800138003', position: 'UI设计师', status: 'hired', source: '内部推荐', experience: '4年', education: '本科', school: '中央美术学院', major: '视觉传达', skills: ['Figma', 'Sketch', 'UI设计', '交互设计'] },
    { id: 4, name: '刘伟', email: 'liuwei@example.com', phone: '13800138004', position: '后端开发工程师', status: 'pending', source: '智联招聘', experience: '6年', education: '本科', school: '上海交通大学', major: '软件工程', skills: ['Java', 'Spring Boot', 'MySQL', 'Redis', '微服务'] },
    { id: 5, name: '陈静', email: 'chenjing@example.com', phone: '13800138005', position: 'Java开发工程师', status: 'interviewed', source: '猎头推荐', experience: '4年', education: '本科', school: '浙江大学', major: '计算机科学', skills: ['Java', 'JVM', '分布式', 'Docker', 'Kubernetes'] },
    { id: 6, name: '赵磊', email: 'zhaolei@example.com', phone: '13800138006', position: '测试工程师', status: 'rejected', source: '拉勾网', experience: '3年', education: '本科', school: '华中科技大学', major: '软件工程', skills: ['自动化测试', '性能测试', 'Selenium', 'JUnit'] },
  ];

  const candidate = candidates.find(c => c.id === Number(id)) || candidates[0];

  const runWorkflow = () => {
    setRunningWorkflow(true);
    setWorkflowProgress(0);
    
    const steps = [
      { progress: 10, step: '简历解析中...' },
      { progress: 25, step: '技能匹配分析...' },
      { progress: 40, step: '文化匹配评估...' },
      { progress: 55, step: '生成测评问卷...' },
      { progress: 70, step: '面试问题生成...' },
      { progress: 85, step: '综合评估中...' },
      { progress: 100, step: '评估完成' },
    ];
    
    steps.forEach((item, index) => {
      setTimeout(() => {
        setWorkflowProgress(item.progress);
        if (index === steps.length - 1) {
          setTimeout(() => setRunningWorkflow(false), 500);
        }
      }, (index + 1) * 800);
    });
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
      hired: '已录用',
      rejected: '已拒绝',
    };
    return map[status] || status;
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'hired': return 'bg-green-100 text-green-700';
      case 'interviewed': return 'bg-blue-100 text-blue-700';
      case 'pending': return 'bg-amber-100 text-amber-700';
      case 'rejected': return 'bg-red-100 text-red-700';
      default: return 'bg-slate-100 text-slate-700';
    }
  };

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
              <p className="text-slate-600 mt-1">{candidate.position}</p>
              <div className="flex items-center gap-4 mt-2 text-sm text-slate-500">
                <span>📧 {candidate.email}</span>
                <span>📱 {candidate.phone}</span>
                <span>📍 来源: {candidate.source}</span>
              </div>
            </div>
          </div>
          <button
            onClick={runWorkflow}
            disabled={runningWorkflow}
            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-lg hover:from-blue-700 hover:to-indigo-700 transition-all shadow-sm disabled:opacity-50"
          >
            <Play size={16} />
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
                      <p className="font-medium text-slate-800 mt-1">{candidate.experience}</p>
                    </div>
                    <div className="p-3 bg-slate-50 rounded-lg">
                      <p className="text-slate-500">学历</p>
                      <p className="font-medium text-slate-800 mt-1">{candidate.education}</p>
                    </div>
                    <div className="p-3 bg-slate-50 rounded-lg">
                      <p className="text-slate-500">毕业院校</p>
                      <p className="font-medium text-slate-800 mt-1">{candidate.school}</p>
                    </div>
                    <div className="p-3 bg-slate-50 rounded-lg">
                      <p className="text-slate-500">专业</p>
                      <p className="font-medium text-slate-800 mt-1">{candidate.major}</p>
                    </div>
                  </div>
                </div>

                <div>
                  <h3 className="font-semibold text-slate-800 mb-3 flex items-center gap-2">
                    <Award className="text-blue-600" size={18} />
                    技能标签
                  </h3>
                  <div className="flex flex-wrap gap-2">
                    {candidate.skills.map((skill) => (
                      <span
                        key={skill}
                        className="px-3 py-1.5 bg-blue-50 text-blue-700 rounded-full text-sm"
                      >
                        {skill}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              <div className="space-y-6">
                <div className="p-4 bg-gradient-to-br from-blue-50 to-indigo-50 rounded-xl">
                  <h3 className="font-semibold text-slate-800 mb-3">匹配度评分</h3>
                  <div className="space-y-3">
                    <div>
                      <div className="flex justify-between text-sm mb-1">
                        <span className="text-slate-600">技能匹配</span>
                        <span className="font-medium text-slate-800">85%</span>
                      </div>
                      <div className="w-full bg-white/50 rounded-full h-2">
                        <div className="bg-blue-500 h-2 rounded-full" style={{ width: '85%' }}></div>
                      </div>
                    </div>
                    <div>
                      <div className="flex justify-between text-sm mb-1">
                        <span className="text-slate-600">经验匹配</span>
                        <span className="font-medium text-slate-800">80%</span>
                      </div>
                      <div className="w-full bg-white/50 rounded-full h-2">
                        <div className="bg-green-500 h-2 rounded-full" style={{ width: '80%' }}></div>
                      </div>
                    </div>
                    <div>
                      <div className="flex justify-between text-sm mb-1">
                        <span className="text-slate-600">文化匹配</span>
                        <span className="font-medium text-slate-800">75%</span>
                      </div>
                      <div className="w-full bg-white/50 rounded-full h-2">
                        <div className="bg-purple-500 h-2 rounded-full" style={{ width: '75%' }}></div>
                      </div>
                    </div>
                    <div className="pt-3 border-t border-white/50">
                      <div className="flex justify-between">
                        <span className="text-slate-600 font-medium">综合评分</span>
                        <span className="text-xl font-bold text-blue-600">80%</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'resume' && (
            <div className="prose max-w-none">
              <h3 className="font-semibold text-slate-800 mb-4">简历内容</h3>
              <div className="bg-slate-50 rounded-lg p-6 text-slate-600">
                <h4 className="font-medium text-slate-800 mb-2">工作经历</h4>
                <ul className="list-disc pl-5 space-y-2 mb-4">
                  <li>{parseInt(candidate.experience) - 2} - {parseInt(candidate.experience)}年 某科技公司 {candidate.position}</li>
                  <li>{parseInt(candidate.experience) - 4} - {parseInt(candidate.experience) - 2}年 某互联网公司 开发工程师</li>
                  <li>{parseInt(candidate.experience) - 6} - {parseInt(candidate.experience) - 4}年 某软件公司 初级开发</li>
                </ul>
                <h4 className="font-medium text-slate-800 mb-2">项目经验</h4>
                <ul className="list-disc pl-5 space-y-2">
                  <li>企业管理系统架构设计与开发</li>
                  <li>电商平台性能优化</li>
                  <li>组件库建设与维护</li>
                </ul>
              </div>
            </div>
          )}

          {activeTab === 'interviews' && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 p-4 bg-slate-50 rounded-lg">
                <div className="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center">
                  <Calendar className="text-blue-600" size={18} />
                </div>
                <div className="flex-1">
                  <p className="font-medium text-slate-800">第一轮技术面试</p>
                  <p className="text-sm text-slate-500">2026-07-15 14:00 · 面试官：李工</p>
                </div>
                <span className="px-2.5 py-1 bg-green-100 text-green-700 rounded-full text-xs font-medium">
                  已通过
                </span>
              </div>
              <div className="flex items-center gap-2 p-4 bg-slate-50 rounded-lg">
                <div className="w-10 h-10 bg-amber-100 rounded-full flex items-center justify-center">
                  <Users className="text-amber-600" size={18} />
                </div>
                <div className="flex-1">
                  <p className="font-medium text-slate-800">第二轮综合面试</p>
                  <p className="text-sm text-slate-500">2026-07-18 10:00 · 面试官：王总监</p>
                </div>
                <span className="px-2.5 py-1 bg-blue-100 text-blue-700 rounded-full text-xs font-medium">
                  待面试
                </span>
              </div>
            </div>
          )}

          {activeTab === 'evaluations' && (
            <div className="space-y-4">
              <div className="p-4 border border-slate-200 rounded-lg">
                <div className="flex justify-between items-start mb-2">
                  <h4 className="font-medium text-slate-800">技术能力评估</h4>
                  <span className="text-lg font-bold text-blue-600">85分</span>
                </div>
                <p className="text-sm text-slate-500">评估人：李工 · 2026-07-15</p>
                <p className="text-sm text-slate-600 mt-2">候选人技术基础扎实，对{String(candidate.skills[0])}和{String(candidate.skills[1])}有深入理解，项目经验丰富。</p>
              </div>
            </div>
          )}

          {activeTab === 'workflow' && (
            <div className="space-y-4">
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-green-100 rounded-full flex items-center justify-center">
                  <FileText className="text-green-600" size={18} />
                </div>
                <div className="flex-1">
                  <p className="font-medium text-slate-800">简历解析</p>
                  <p className="text-sm text-slate-500">已完成</p>
                </div>
                <span className="text-green-600 text-sm">✓</span>
              </div>
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-green-100 rounded-full flex items-center justify-center">
                  <Target className="text-green-600" size={18} />
                </div>
                <div className="flex-1">
                  <p className="font-medium text-slate-800">技能匹配评估</p>
                  <p className="text-sm text-slate-500">匹配度 85%</p>
                </div>
                <span className="text-green-600 text-sm">✓</span>
              </div>
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-green-100 rounded-full flex items-center justify-center">
                  <Users className="text-green-600" size={18} />
                </div>
                <div className="flex-1">
                  <p className="font-medium text-slate-800">文化匹配评估</p>
                  <p className="text-sm text-slate-500">匹配度 75%</p>
                </div>
                <span className="text-green-600 text-sm">✓</span>
              </div>
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center">
                  <Award className="text-blue-600" size={18} />
                </div>
                <div className="flex-1">
                  <p className="font-medium text-slate-800">最终决策</p>
                  <p className="text-sm text-slate-500">推荐录用</p>
                </div>
                <span className="text-blue-600 text-sm">进行中</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default CandidateDetail;

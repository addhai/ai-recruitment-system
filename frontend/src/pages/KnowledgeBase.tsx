import React, { useState } from 'react';
import { Search, BookOpen, FileText, Send, Bot, User } from 'lucide-react';

const KnowledgeBase: React.FC = () => {
  const [query, setQuery] = useState('');
  const [messages, setMessages] = useState<{ role: string; content: string; sources?: any[] }[]>([
    { role: 'assistant', content: '您好！我是企业人事制度知识库助手，有什么可以帮助您的吗？您可以询问关于入职流程、福利制度、绩效考核、培训等方面的问题。' }
  ]);
  const [loading, setLoading] = useState(false);

  const documents = [
    { title: '员工入职流程', category: '入职管理' },
    { title: '员工福利制度', category: '薪酬福利' },
    { title: '绩效考核制度', category: '绩效管理' },
    { title: '员工培训制度', category: '培训发展' },
    { title: '招聘流程规范', category: '招聘管理' },
    { title: '薪酬管理制度', category: '薪酬福利' },
  ];

  const quickQuestions = [
    '员工入职流程是怎样的？',
    '年假有多少天？',
    '绩效考核怎么算？',
    '试用期多久？',
  ];

  const handleSend = (q?: string) => {
    const question = q || query;
    if (!question.trim()) return;

    setMessages(prev => [...prev, { role: 'user', content: question }]);
    setQuery('');
    setLoading(true);

    setTimeout(() => {
      const answer = getAnswer(question);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: answer.content,
        sources: answer.sources
      }]);
      setLoading(false);
    }, 1000);
  };

  const getAnswer = (question: string) => {
    if (question.includes('入职')) {
      return {
        content: '员工入职流程主要包括以下几个步骤：\n\n1. **入职前准备**：HR发送录用通知书，收集入职材料（身份证复印件、学历证明、离职证明等），安排办公座位和设备，创建员工邮箱和账号。\n\n2. **入职当天**：\n   - 9:00-9:30 报到登记，领取工牌\n   - 9:30-10:30 HR入职培训（公司制度、企业文化）\n   - 10:30-11:30 IT部门设备配置\n   - 11:30-12:00 部门负责人接待\n   - 14:00-17:00 部门内部培训\n\n3. **试用期**：1-3个月，根据岗位性质确定。',
        sources: [{ title: '员工入职流程' }]
      };
    }
    if (question.includes('年假') || question.includes('休假')) {
      return {
        content: '关于带薪休假的规定如下：\n\n- **年假**：入职满1年享受5天，每增加1年增加1天，最多15天\n- **病假**：每年15天带薪病假\n- **婚假**：3天，晚婚额外增加7天\n- **产假**：98天，符合条件可延长\n\n此外还有年度体检、餐补、交通补贴等福利。',
        sources: [{ title: '员工福利制度' }]
      };
    }
    if (question.includes('绩效') || question.includes('考核')) {
      return {
        content: '绩效考核制度如下：\n\n**考核周期**：\n- 月度考核：每月进行一次\n- 季度考核：每季度进行一次\n- 年度考核：每年进行一次\n\n**考核维度**：\n- 工作业绩（40%）\n- 工作态度（20%）\n- 团队协作（20%）\n- 创新能力（10%）\n- 职业素养（10%）\n\n**考核等级**：S级（卓越）、A级（优秀）、B级（良好）、C级（合格）、D级（不合格）',
        sources: [{ title: '绩效考核制度' }]
      };
    }
    if (question.includes('试用')) {
      return {
        content: '试用期规定如下：\n\n- 试用期一般为1-3个月，根据岗位性质确定\n- 试用期工资为正式工资的80%\n- 试用期考核通过后转为正式员工\n- 入职前需完成体检\n- 需签订劳动合同和保密协议',
        sources: [{ title: '员工入职流程' }]
      };
    }
    return {
      content: '感谢您的提问。您可以尝试询问以下问题：\n- 员工入职流程是怎样的？\n- 年假有多少天？\n- 绩效考核怎么算？\n- 试用期多久？\n- 公司有哪些福利？',
      sources: []
    };
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 h-[calc(100vh-200px)]">
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-4 overflow-y-auto">
          <h3 className="font-semibold text-slate-800 mb-4 flex items-center gap-2">
            <BookOpen size={20} className="text-blue-600" />
            知识库文档
          </h3>
          <div className="space-y-2">
            {documents.map((doc, index) => (
              <div
                key={index}
                className="p-3 bg-slate-50 hover:bg-slate-100 rounded-lg cursor-pointer transition-colors"
              >
                <div className="flex items-center gap-2 mb-1">
                  <FileText size={16} className="text-slate-400" />
                  <span className="text-sm font-medium text-slate-700">{doc.title}</span>
                </div>
                <span className="text-xs text-slate-400 ml-6">{doc.category}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="lg:col-span-3 bg-white rounded-xl shadow-sm border border-slate-200 flex flex-col overflow-hidden">
          <div className="p-4 border-b border-slate-100">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="搜索知识库..."
                className="w-full pl-10 pr-24 py-2.5 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                onClick={() => handleSend()}
                className="absolute right-2 top-1/2 -translate-y-1/2 px-4 py-1.5 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700 transition-colors"
              >
                搜索
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.map((msg, index) => (
              <div key={index} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
                <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                  msg.role === 'user' ? 'bg-blue-600' : 'bg-gradient-to-br from-purple-500 to-pink-500'
                }`}>
                  {msg.role === 'user' ? (
                    <User size={16} className="text-white" />
                  ) : (
                    <Bot size={16} className="text-white" />
                  )}
                </div>
                <div className={`max-w-[80%] ${msg.role === 'user' ? 'text-right' : ''}`}>
                  <div className={`p-3 rounded-2xl text-sm ${
                    msg.role === 'user'
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-100 text-slate-700'
                  }`} style={{ whiteSpace: 'pre-wrap' }}>
                    {msg.content}
                  </div>
                  {msg.sources && msg.sources.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {msg.sources.map((src, i) => (
                        <span key={i} className="text-xs text-slate-400 bg-slate-50 px-2 py-1 rounded">
                          来源: {src.title}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex gap-3">
                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center flex-shrink-0">
                  <Bot size={16} className="text-white" />
                </div>
                <div className="bg-slate-100 p-3 rounded-2xl">
                  <div className="flex gap-1">
                    <div className="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
                    <div className="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
                    <div className="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="p-4 border-t border-slate-100">
            <p className="text-xs text-slate-400 mb-2">快捷提问：</p>
            <div className="flex flex-wrap gap-2">
              {quickQuestions.map((q, index) => (
                <button
                  key={index}
                  onClick={() => handleSend(q)}
                  className="px-3 py-1.5 text-xs bg-slate-100 text-slate-600 rounded-full hover:bg-slate-200 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default KnowledgeBase;

import React, { useState, useEffect } from 'react';
import { Search, BookOpen, FileText, Bot, User } from 'lucide-react';
import { queryKnowledgeBase, getDocuments } from '../services/knowledgeBase';
import type { KnowledgeBaseDocument } from '../services/knowledgeBase';

const KnowledgeBase: React.FC = () => {
  const [query, setQuery] = useState('');
  const [messages, setMessages] = useState<{ role: string; content: string; sources?: any[]; mode?: string }[]>([
    { role: 'assistant', content: '您好！我是企业人事制度知识库助手，有什么可以帮助您的吗？您可以询问关于入职流程、福利制度、绩效考核、培训等方面的问题。' }
  ]);

  const modeLabel: Record<string, { text: string; cls: string }> = {
    hybrid_rag: { text: '混合检索（向量+关键词）', cls: 'bg-green-50 text-green-600' },
    bm25_llm: { text: '关键词检索 + AI 生成', cls: 'bg-blue-50 text-blue-600' },
    fallback: { text: '规则模板回答（AI 未启用）', cls: 'bg-amber-50 text-amber-600' },
    blocked: { text: '安全拦截', cls: 'bg-red-50 text-red-600' },
  };
  const [loading, setLoading] = useState(false);
  const [documents, setDocuments] = useState<KnowledgeBaseDocument[]>([]);

  useEffect(() => {
    const loadDocs = async () => {
      try {
        const docs = await getDocuments();
        setDocuments(docs);
      } catch (e) {
        console.error('加载知识库文档失败:', e);
      }
    };
    loadDocs();
  }, []);

  const quickQuestions = [
    '员工入职流程是怎样的？',
    '年假有多少天？',
    '绩效考核怎么算？',
    '试用期多久？',
  ];

  const handleSend = async (q?: string) => {
    const question = q || query;
    if (!question.trim()) return;

    setMessages(prev => [...prev, { role: 'user', content: question }]);
    setQuery('');
    setLoading(true);

    try {
      const result = await queryKnowledgeBase(question);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: result.answer,
        sources: result.sources,
        mode: result.mode,
      }]);
    } catch (e: any) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `抱歉，查询失败：${e.message || '未知错误'}`,
        sources: []
      }]);
    } finally {
      setLoading(false);
    }
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
              </div>
            ))}
            {documents.length === 0 && (
              <p className="text-sm text-slate-400 text-center py-4">加载中...</p>
            )}
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
                  {msg.mode && modeLabel[msg.mode] && (
                    <span className={`mt-2 inline-block text-[11px] px-2 py-0.5 rounded ${modeLabel[msg.mode].cls}`}>
                      {modeLabel[msg.mode].text}
                    </span>
                  )}
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

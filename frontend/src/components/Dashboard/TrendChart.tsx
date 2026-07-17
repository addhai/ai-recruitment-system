import React from 'react';
import type { TrendData } from '../../types';

const TrendChart: React.FC<{ data: TrendData[] }> = ({ data }) => {
  const maxCandidates = Math.max(...data.map((d) => d.candidates), 1);
  const maxInterviews = Math.max(...data.map((d) => d.interviews), 1);
  const maxVal = Math.max(maxCandidates, maxInterviews);

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
      <div className="flex items-center justify-between mb-6">
        <h3 className="font-semibold text-slate-800">本周招聘趋势</h3>
        <div className="flex gap-4 text-sm">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-blue-500"></div>
            <span className="text-slate-500">候选人</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-green-500"></div>
            <span className="text-slate-500">面试</span>
          </div>
        </div>
      </div>

      <div className="flex items-end justify-between gap-3 h-48">
        {data.map((item, index) => (
          <div key={index} className="flex-1 flex flex-col items-center gap-2">
            <div className="w-full flex flex-col items-center gap-1 flex-1 justify-end">
              <div
                className="w-full bg-blue-500 rounded-t-md transition-all duration-500 hover:bg-blue-600"
                style={{ height: `${(item.candidates / maxVal) * 100}%`, minHeight: '4px' }}
                title={`候选人: ${item.candidates}`}
              ></div>
              <div
                className="w-full bg-green-500 rounded-t-md transition-all duration-500 hover:bg-green-600"
                style={{ height: `${(item.interviews / maxVal) * 100}%`, minHeight: '4px' }}
                title={`面试: ${item.interviews}`}
              ></div>
            </div>
            <span className="text-xs text-slate-500">{item.day}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

export default TrendChart;

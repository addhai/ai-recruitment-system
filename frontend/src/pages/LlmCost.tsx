import React, { useCallback, useEffect, useState } from 'react';
import { Coins, RefreshCw, AlertTriangle, Loader2, X } from 'lucide-react';
import type { LlmStats, LlmCallRow } from '../services/llmStats';
import { getLlmStats, getRecentLlmCalls } from '../services/llmStats';
import { fmtCost, fmtNum } from '../lib/format';

const LlmCost: React.FC = () => {
  const [days, setDays] = useState(7);
  const [stats, setStats] = useState<LlmStats | null>(null);
  const [calls, setCalls] = useState<LlmCallRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 按工作流运行过滤：点明细里的「运行」即只看那一次简历评估发出的调用。
  // 走服务端口过滤而不是只筛本地 50 条，保证看到的是该运行的全部调用。
  const [threadFilter, setThreadFilter] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, c] = await Promise.all([
        getLlmStats(days),
        getRecentLlmCalls(50, undefined, threadFilter ?? undefined),
      ]);
      setStats(s);
      setCalls(c);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [days, threadFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const t = stats?.totals;
  const budget = stats?.budget;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-800 flex items-center gap-2">
            <Coins className="w-6 h-6" />
            AI 成本
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            每次 LLM 调用的 token、耗时、成本与降级情况。降级率偏高说明模型在抖动，
            此时的评分应视为兜底值而非真实评估结果。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="border border-slate-300 rounded-lg px-3 py-2 text-sm"
          >
            <option value={1}>最近 1 天</option>
            <option value={7}>最近 7 天</option>
            <option value={30}>最近 30 天</option>
            <option value={90}>最近 90 天</option>
          </select>
          <button
            onClick={load}
            className="flex items-center gap-1.5 border border-slate-300 px-3 py-2 rounded-lg text-sm"
          >
            <RefreshCw className="w-4 h-4" />
            刷新
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-800 text-sm">
          <AlertTriangle className="w-4 h-4" />
          {error}
        </div>
      )}

      {/* 单价未配置时预算保护其实没有生效，必须显式提示，避免误以为有保护 */}
      {budget && !budget.pricing_configured && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 text-amber-800 text-sm">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            未配置模型单价（<code>LLM_INPUT_PRICE_PER_MILLION</code> /
            <code> LLM_OUTPUT_PRICE_PER_MILLION</code>），因此成本恒为 0，
            <strong>预算上限不会生效</strong>。请在 <code>.env</code> 中填入供应商报价后重启。
          </span>
        </div>
      )}

      {loading && !stats ? (
        <p className="text-slate-400 text-sm flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" /> 加载中…
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-white rounded-xl border border-slate-200 p-4">
              <p className="text-xs text-slate-500">调用次数</p>
              <p className="text-2xl font-bold text-slate-800">{fmtNum(t?.calls)}</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-4">
              <p className="text-xs text-slate-500">总成本</p>
              <p className="text-2xl font-bold text-slate-800">{fmtCost(t?.cost, stats?.currency)}</p>
              <p className="text-xs text-slate-400 mt-1">
                单次均价 {fmtCost(t?.avg_cost_per_call, stats?.currency)}
              </p>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-4">
              <p className="text-xs text-slate-500">Token（入 / 出）</p>
              <p className="text-lg font-bold text-slate-800">
                {fmtNum(t?.input_tokens)} / {fmtNum(t?.output_tokens)}
              </p>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-4">
              <p className="text-xs text-slate-500">失败 / 降级</p>
              <p className="text-2xl font-bold text-slate-800">
                {fmtNum(t?.failed)} / {fmtNum(t?.degraded)}
              </p>
              <p className="text-xs text-slate-400 mt-1">
                失败率 {((t?.failed_rate ?? 0) * 100).toFixed(1)}%
              </p>
            </div>
          </div>

          {/* 跨币种金额绝不混算，但也不能让用户以为这就是全部花费 */}
          {(t?.excluded_cost ?? 0) > 0 && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 text-amber-800 text-sm">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>
                另有 <strong>{(t?.excluded_cost ?? 0).toFixed(4)}</strong> 的花费未计入上方合计：
                {t?.foreign_currencies && t.foreign_currencies.length > 0 && (
                  <>其它币种 {t.foreign_currencies.join(' / ')} 计 {(t?.foreign_cost ?? 0).toFixed(4)}；</>
                )}
                {(t?.unattributed_cost ?? 0) > 0 && (
                  <>币种未知（多币种改造前的历史记录）计 {(t?.unattributed_cost ?? 0).toFixed(4)}。</>
                )}
                不同币种金额不可相加，故不并入合计。
              </span>
            </div>
          )}

          {budget && (
            <div className="bg-white rounded-xl border border-slate-200 p-4 text-sm">
              <p className="text-slate-600">
                预算保护：
                <span className={budget.enabled ? 'text-emerald-700' : 'text-slate-500'}>
                  {budget.enabled ? '已启用' : '已关闭'}
                </span>
                ，{budget.period === 'monthly' ? '每月' : '每日'}上限{' '}
                <strong>{fmtCost(budget.limit, budget.currency)}</strong>，
                超限动作：
                <span className="font-mono">
                  {budget.action === 'halt' ? 'halt（停止调用并转人工）' : 'warn（仅告警）'}
                </span>
                {' · '}日志保留 {stats?.log_retention_days} 天
              </p>
            </div>
          )}

          <div>
            <h2 className="text-lg font-semibold text-slate-800 mb-3">按调用环节分布</h2>
            {!stats?.by_site.length ? (
              <p className="text-slate-400 text-sm">该时间窗口内暂无调用记录。</p>
            ) : (
              <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-slate-600">
                    <tr>
                      <th className="text-left px-4 py-2 font-medium">调用环节</th>
                      <th className="text-right px-3 py-2 font-medium">次数</th>
                      <th className="text-right px-3 py-2 font-medium">Token</th>
                      <th className="text-right px-3 py-2 font-medium">成本</th>
                      <th className="text-right px-3 py-2 font-medium">平均耗时</th>
                      <th className="text-right px-3 py-2 font-medium">降级率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.by_site.map((s) => (
                      <tr key={s.call_site} className="border-t border-slate-100">
                        <td className="px-4 py-2 font-mono text-xs text-slate-700">
                          {s.call_site}
                        </td>
                        <td className="px-3 py-2 text-right">{fmtNum(s.calls)}</td>
                        <td className="px-3 py-2 text-right text-slate-500 text-xs">
                          {fmtNum(s.input_tokens)} / {fmtNum(s.output_tokens)}
                        </td>
                        <td className="px-3 py-2 text-right">
                          {fmtCost(s.cost, stats?.currency)}
                          {s.excluded_cost > 0 && (
                            <span
                              className="ml-1 text-amber-600"
                              title={`另有 ${s.excluded_cost.toFixed(4)} 未计入（其它币种或币种未知）`}
                            >
                              *
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-500">
                          {s.avg_latency_ms} ms
                        </td>
                        <td className="px-3 py-2 text-right">
                          <span className={s.degraded_rate > 0.2 ? 'text-red-600' : 'text-slate-600'}>
                            {(s.degraded_rate * 100).toFixed(1)}%
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-semibold text-slate-800">最近调用</h2>
              {threadFilter && (
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-slate-500">
                    只看运行 <code className="bg-slate-100 px-1.5 py-0.5 rounded font-mono">
                      {threadFilter.slice(0, 12)}…
                    </code>（{calls.length} 次调用）
                  </span>
                  <button
                    onClick={() => setThreadFilter(null)}
                    className="flex items-center gap-1 text-blue-600 hover:text-blue-700"
                  >
                    <X size={12} /> 清除
                  </button>
                </div>
              )}
            </div>
            {!calls.length ? (
              <p className="text-slate-400 text-sm">暂无记录。</p>
            ) : (
              <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50 text-slate-600">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium">时间</th>
                      <th className="text-left px-3 py-2 font-medium">环节</th>
                      <th className="text-left px-3 py-2 font-medium">运行</th>
                      <th className="text-left px-3 py-2 font-medium">模型</th>
                      <th className="text-left px-3 py-2 font-medium">候选人</th>
                      <th className="text-right px-3 py-2 font-medium">Token</th>
                      <th className="text-right px-3 py-2 font-medium">成本</th>
                      <th className="text-right px-3 py-2 font-medium">耗时</th>
                      <th className="text-left px-3 py-2 font-medium">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {calls.map((c) => (
                      <tr key={c.id} className="border-t border-slate-100">
                        <td className="px-3 py-1.5 text-slate-500">
                          {c.created_at?.slice(5, 19).replace('T', ' ')}
                        </td>
                        <td className="px-3 py-1.5 font-mono text-slate-700">{c.call_site}</td>
                        <td className="px-3 py-1.5">
                          {/* 点一下只看该次运行的全部调用：一次简历评估会发 4~8 次调用，
                              只按候选人分组时重跑会混在一起 */}
                          {c.thread_id ? (
                            <button
                              onClick={() => setThreadFilter(c.thread_id)}
                              title={`只看运行 ${c.thread_id} 的全部调用`}
                              className="font-mono text-blue-600 hover:underline"
                            >
                              {c.thread_id.slice(0, 8)}
                            </button>
                          ) : (
                            <span className="text-slate-300" title="非工作流链路（知识库问答 / JD 解析）">—</span>
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-slate-500 font-mono">
                          {/* 显示供应商实际服务的版本：同一请求名可能被路由到不同底层版本，
                              分数对不上时要能看出是不是换了版本 */}
                          {c.model_served || c.model || '-'}
                          {c.model_served && c.model && c.model_served !== c.model && (
                            <span className="ml-1 text-slate-400">(请求 {c.model})</span>
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-slate-500">{c.candidate_id ?? '-'}</td>
                        <td className="px-3 py-1.5 text-right text-slate-500">
                          {c.usage_missing ? '未知' : `${fmtNum(c.input_tokens)}/${fmtNum(c.output_tokens)}`}
                        </td>
                        <td className="px-3 py-1.5 text-right">{fmtCost(c.cost, c.currency)}</td>
                        <td className="px-3 py-1.5 text-right text-slate-500">{c.latency_ms} ms</td>
                        <td className="px-3 py-1.5">
                          <span className={
                            c.status === 'ok' && !c.degraded
                              ? 'text-emerald-700'
                              : 'text-red-600'
                          }>
                            {c.degraded ? '降级' : c.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default LlmCost;
import React, { useEffect, useState } from 'react';
import { Cpu, Loader2, Save, Plug, CheckCircle2, AlertTriangle, KeyRound } from 'lucide-react';
import type { LLMConfigView, LLMPreset } from '../../services/llmConfig';
import { getLLMConfig, updateLLMConfig, getLLMPresets, testLLMConfig } from '../../services/llmConfig';

/**
 * 模型接入配置面板。
 *
 * 本项目不是模型中转站，不预置固定供应商接入：模型名、API Key、Base URL、
 * 单价都由使用者填写。填完点「测试连接」当场验证，不用等跑候选人评估才发现填错。
 */
const ModelConfigPanel: React.FC = () => {
  const [cfg, setCfg] = useState<LLMConfigView | null>(null);
  const [presets, setPresets] = useState<Record<string, LLMPreset>>({});
  const [presetNote, setPresetNote] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  const [form, setForm] = useState({
    model: '',
    base_url: '',
    api_key: '',
    input_price: '',
    output_price: '',
    currency: 'CNY',
    peak_multiplier: '',
    temperature: '',
    timeout_seconds: '',
  });

  const applyView = (v: LLMConfigView) => {
    setCfg(v);
    setForm({
      model: v.model || '',
      base_url: v.base_url || '',
      api_key: '',                    // 留空表示不修改
      input_price: String(v.input_price ?? ''),
      output_price: String(v.output_price ?? ''),
      currency: v.currency || 'CNY',
      peak_multiplier: String(v.peak_multiplier ?? ''),
      temperature: String(v.temperature ?? ''),
      timeout_seconds: String(v.timeout_seconds ?? ''),
    });
  };

  useEffect(() => {
    (async () => {
      try {
        applyView(await getLLMConfig());
        const p = await getLLMPresets();
        setPresets(p.presets);
        setPresetNote(p.note);
      } catch (e: any) {
        setMsg({ kind: 'err', text: e.message });
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const applyPreset = (key: string) => {
    const p = presets[key];
    if (!p) return;
    setForm((f) => ({
      ...f,
      model: key,
      base_url: p.base_url,
      input_price: String(p.input_price),
      output_price: String(p.output_price),
      currency: p.currency,
      peak_multiplier: String(p.peak_multiplier),
      temperature: String(p.temperature),
      timeout_seconds: String(p.timeout_seconds),
    }));
    setMsg({ kind: 'ok', text: `已填入 ${p.label} 的官方参数，API Key 仍需自己填` });
  };

  const num = (v: string) => (v.trim() === '' ? undefined : Number(v));

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const updated = await updateLLMConfig({
        model: form.model,
        base_url: form.base_url,
        // 空字符串 = 清空回退 .env；有值才提交
        ...(form.api_key.trim() !== '' ? { api_key: form.api_key } : {}),
        input_price: num(form.input_price),
        output_price: num(form.output_price),
        currency: form.currency,
        peak_multiplier: num(form.peak_multiplier),
        temperature: num(form.temperature),
        timeout_seconds: num(form.timeout_seconds),
      });
      applyView(updated);
      setMsg({ kind: 'ok', text: '配置已保存并立即生效（无需重启服务）' });
    } catch (e: any) {
      setMsg({ kind: 'err', text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await testLLMConfig();
      if (r.ok) {
        setTestResult(`连接成功：模型 ${r.model} 返回「${r.reply_preview}」，` +
          `token ${r.input_tokens ?? '?'}/${r.output_tokens ?? '?'}`);
      } else {
        setTestResult(`连接失败：${r.error}`);
      }
    } catch (e: any) {
      setTestResult(`连接失败：${e.message}`);
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <p className="text-slate-400 text-sm flex items-center gap-2">
        <Loader2 className="w-4 h-4 animate-spin" /> 加载中…
      </p>
    );
  }

  return (
    <div className="space-y-5 max-w-3xl">
      <div>
        <h3 className="text-lg font-semibold text-slate-800 mb-1 flex items-center gap-2">
          <Cpu className="w-5 h-5" /> 模型配置
        </h3>
        <p className="text-sm text-slate-500">
          填写你要接入的模型。配置保存后立即生效，无需重启服务。
          <code className="mx-1 px-1 bg-slate-100 rounded text-xs">.env</code>
          中的配置作为兜底默认值，此处填写的项优先。
        </p>
      </div>

      {msg && (
        <div className={`flex items-start gap-2 p-3 rounded-lg text-sm ${
          msg.kind === 'ok' ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'}`}>
          {msg.kind === 'ok'
            ? <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
            : <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />}
          <span>{msg.text}</span>
        </div>
      )}

      {/* 未配单价时预算保护不生效，必须显式提示 */}
      {cfg && !cfg.pricing_configured && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 text-amber-800 text-sm">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            单价未填写 → 成本恒为 0，
            <strong>成本预算上限不会生效</strong>。请按供应商报价填写输入/输出单价。
          </span>
        </div>
      )}

      {Object.keys(presets).length > 0 && (
        <div>
          <p className="text-sm font-medium text-slate-700 mb-2">快速填入预设</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(presets).map(([key, p]) => (
              <button
                key={key}
                onClick={() => applyPreset(key)}
                className="text-left text-xs border border-slate-300 rounded-lg px-3 py-2 hover:border-blue-400 hover:bg-blue-50 transition-colors"
              >
                <span className="block font-medium text-slate-700">{key}</span>
                <span className="block text-slate-500 mt-0.5">
                  {p.currency} {p.input_price}/{p.output_price} 每百万 token
                </span>
              </button>
            ))}
          </div>
          {presetNote && <p className="text-xs text-slate-400 mt-2">{presetNote}</p>}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <label className="text-sm">
          <span className="text-slate-600">模型名称 *</span>
          <input
            className="w-full border border-slate-300 rounded px-3 py-2 mt-1"
            value={form.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
            placeholder="如：deepseek-flash / gpt-4o-mini / qwen-plus"
          />
        </label>
        <label className="text-sm">
          <span className="text-slate-600">Base URL</span>
          <input
            className="w-full border border-slate-300 rounded px-3 py-2 mt-1"
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            placeholder="https://api.deepseek.com"
          />
        </label>
      </div>

      <label className="block text-sm">
        <span className="text-slate-600 flex items-center gap-1.5">
          <KeyRound className="w-3.5 h-3.5" /> API Key
        </span>
        <input
          type="password"
          className="w-full border border-slate-300 rounded px-3 py-2 mt-1"
          value={form.api_key}
          onChange={(e) => setForm({ ...form, api_key: e.target.value })}
          placeholder={cfg?.api_key_configured
            ? `当前已配置（${cfg.api_key_masked}）— 留空则不修改`
            : '请输入 API Key'}
        />
        <span className="text-xs text-slate-400 mt-1 block">
          加密存储，接口不会回传明文。要清空并回退 .env，请点下方「清空密钥」。
        </span>
      </label>

      <div className="grid grid-cols-3 gap-3">
        <label className="text-sm">
          <span className="text-slate-600">输入单价 / 百万 token</span>
          <input
            type="number" step="0.01"
            className="w-full border border-slate-300 rounded px-3 py-2 mt-1"
            value={form.input_price}
            onChange={(e) => setForm({ ...form, input_price: e.target.value })}
          />
        </label>
        <label className="text-sm">
          <span className="text-slate-600">输出单价 / 百万 token</span>
          <input
            type="number" step="0.01"
            className="w-full border border-slate-300 rounded px-3 py-2 mt-1"
            value={form.output_price}
            onChange={(e) => setForm({ ...form, output_price: e.target.value })}
          />
        </label>
        <label className="text-sm">
          <span className="text-slate-600">币种</span>
          <select
            className="w-full border border-slate-300 rounded px-3 py-2 mt-1"
            value={form.currency}
            onChange={(e) => setForm({ ...form, currency: e.target.value })}
          >
            <option value="CNY">CNY 人民币</option>
            <option value="USD">USD 美元</option>
          </select>
        </label>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <label className="text-sm">
          <span className="text-slate-600">高峰价格倍数</span>
          <input
            type="number" step="0.1" min="1"
            className="w-full border border-slate-300 rounded px-3 py-2 mt-1"
            value={form.peak_multiplier}
            onChange={(e) => setForm({ ...form, peak_multiplier: e.target.value })}
          />
          <span className="text-xs text-slate-400 mt-1 block">
            1 = 不分时段。DeepSeek 为 2（高峰=空闲×2）
          </span>
        </label>
        <label className="text-sm">
          <span className="text-slate-600">温度</span>
          <input
            type="number" step="0.1" min="0" max="2"
            className="w-full border border-slate-300 rounded px-3 py-2 mt-1"
            value={form.temperature}
            onChange={(e) => setForm({ ...form, temperature: e.target.value })}
          />
          <span className="text-xs text-slate-400 mt-1 block">
            评分类任务建议低温度以保证结果可复现
          </span>
        </label>
        <label className="text-sm">
          <span className="text-slate-600">超时（秒）</span>
          <input
            type="number" min="1" max="600"
            className="w-full border border-slate-300 rounded px-3 py-2 mt-1"
            value={form.timeout_seconds}
            onChange={(e) => setForm({ ...form, timeout_seconds: e.target.value })}
          />
        </label>
      </div>

      {cfg && (
        <div className="text-xs text-slate-500 bg-slate-50 rounded-lg p-3 space-y-1">
          <p>配置来源：{cfg.source === 'database' ? '界面配置（优先）' : '.env 兜底'}</p>
          <p>当前计价时段：{cfg.peak_multiplier > 1
            ? (cfg.is_peak_now ? '高峰时段（价格 ×' + cfg.peak_multiplier + '）' : '空闲时段')
            : '不分时段'}</p>
        </div>
      )}

      {testResult && (
        <div className={`text-sm p-3 rounded-lg ${
          testResult.startsWith('连接成功')
            ? 'bg-emerald-50 text-emerald-800'
            : 'bg-red-50 text-red-800'}`}>
          {testResult}
        </div>
      )}

      <div className="flex flex-wrap gap-2 pt-1">
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-1.5 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          保存配置
        </button>
        <button
          onClick={test}
          disabled={testing}
          className="flex items-center gap-1.5 border border-slate-300 px-4 py-2 rounded-lg text-sm disabled:opacity-50"
        >
          {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plug className="w-4 h-4" />}
          测试连接
        </button>
        <button
          onClick={async () => {
            if (!window.confirm('确认清空 API Key？清空后将回退使用 .env 中的配置。')) return;
            try {
              applyView(await updateLLMConfig({ api_key: '' }));
              setMsg({ kind: 'ok', text: 'API Key 已清空，当前回退 .env 配置' });
            } catch (e: any) {
              setMsg({ kind: 'err', text: e.message });
            }
          }}
          className="px-4 py-2 rounded-lg text-sm border border-slate-300 text-slate-600"
        >
          清空密钥
        </button>
      </div>
    </div>
  );
};

export default ModelConfigPanel;
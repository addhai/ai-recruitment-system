import React, { useCallback, useEffect, useState } from 'react';
import {
  Briefcase, Plus, FileText, Upload, CheckCircle2, Archive, Trash2,
  AlertTriangle, Loader2, ChevronDown, ChevronRight, Pencil, XCircle,
} from 'lucide-react';
import type { Position, JobDescription, JdProfile, JdSkill, JdBasic } from '../types';
import {
  getPositions, createPosition, updatePosition, closePosition, deletePosition,
} from '../services/positions';
import {
  getJobDescriptions, createJobDescription, parseJobDescription,
  updateJobDescription, activateJobDescription, archiveJobDescription, deleteJobDescription,
} from '../services/jobDescriptions';

const POS_STATUS: Record<string, { text: string; cls: string }> = {
  draft: { text: '草稿', cls: 'bg-slate-100 text-slate-600' },
  active: { text: '招聘中', cls: 'bg-emerald-100 text-emerald-700' },
  archived: { text: '已关闭', cls: 'bg-slate-200 text-slate-500' },
};

const JD_STATUS: Record<string, { text: string; cls: string }> = {
  draft: { text: '草稿', cls: 'bg-slate-100 text-slate-600' },
  active: { text: '已启用', cls: 'bg-emerald-100 text-emerald-700' },
  archived: { text: '已停用', cls: 'bg-slate-200 text-slate-500' },
};

const PARSE_STATUS: Record<string, { text: string; cls: string }> = {
  pending: { text: '待解析', cls: 'bg-amber-100 text-amber-700' },
  parsed: { text: '已解析', cls: 'bg-blue-100 text-blue-700' },
  failed: { text: '解析失败', cls: 'bg-red-100 text-red-700' },
};

/* ----------------------------------------------------------------- 岗位表单 */

const PositionForm = ({
  initial, onSave, onCancel, busy,
}: {
  initial?: Position;
  onSave: (data: any) => void;
  onCancel: () => void;
  busy: boolean;
}) => {
  const [form, setForm] = useState({
    title: initial?.title || '',
    department: initial?.department || '',
    location: initial?.location || '',
    headcount: initial?.headcount ? String(initial.headcount) : '',
    description: initial?.description || '',
  });
  return (
    <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="text-sm">
          <span className="text-slate-600">岗位名称 *</span>
          <input
            className="w-full border border-slate-300 rounded px-2 py-1.5 mt-1"
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder="如：AI Agent 工程师"
          />
        </label>
        <label className="text-sm">
          <span className="text-slate-600">所属部门</span>
          <input
            className="w-full border border-slate-300 rounded px-2 py-1.5 mt-1"
            value={form.department}
            onChange={(e) => setForm({ ...form, department: e.target.value })}
          />
        </label>
        <label className="text-sm">
          <span className="text-slate-600">工作地点</span>
          <input
            className="w-full border border-slate-300 rounded px-2 py-1.5 mt-1"
            value={form.location}
            onChange={(e) => setForm({ ...form, location: e.target.value })}
          />
        </label>
        <label className="text-sm">
          <span className="text-slate-600">招聘人数</span>
          <input
            type="number"
            className="w-full border border-slate-300 rounded px-2 py-1.5 mt-1"
            value={form.headcount}
            onChange={(e) => setForm({ ...form, headcount: e.target.value })}
          />
        </label>
      </div>
      <label className="block text-sm">
        <span className="text-slate-600">岗位说明（可选）</span>
        <textarea
          className="w-full border border-slate-300 rounded px-2 py-2 mt-1 h-20"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          placeholder="这个岗位要解决什么问题、团队背景等"
        />
      </label>
      <div className="flex gap-2">
        <button
          onClick={() => onSave({ ...form, headcount: form.headcount ? Number(form.headcount) : null })}
          disabled={busy}
          className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm disabled:opacity-50"
        >
          保存
        </button>
        <button onClick={onCancel} className="px-3 py-1.5 rounded text-sm border border-slate-300">
          取消
        </button>
      </div>
    </div>
  );
};

/* ----------------------------------------------------------------- JD 画像编辑 */

const SkillEditor = ({
  label, skills, onChange,
}: {
  label: string;
  skills: JdSkill[];
  onChange: (next: JdSkill[]) => void;
}) => (
  <div>
    <p className="text-sm font-medium text-slate-700 mb-2">{label}</p>
    {skills.length === 0 && <p className="text-sm text-slate-400">（无）</p>}
    <div className="space-y-2">
      {skills.map((s, i) => (
        <div key={i} className="border border-slate-200 rounded-lg p-3 bg-white">
          <input
            className="w-full border border-slate-300 rounded px-2 py-1 text-sm mb-1"
            value={s.skill}
            placeholder="技能名"
            onChange={(e) => {
              const next = [...skills];
              next[i] = { ...s, skill: e.target.value };
              onChange(next);
            }}
          />
          <input
            className="w-full border border-slate-300 rounded px-2 py-1 text-xs text-slate-600"
            value={s.evidence}
            placeholder="JD 原文依据（必填，无原文依据的技能会被服务端丢弃）"
            onChange={(e) => {
              const next = [...skills];
              next[i] = { ...s, evidence: e.target.value };
              onChange(next);
            }}
          />
        </div>
      ))}
    </div>
    <button
      onClick={() => onChange([...skills, { skill: '', evidence: '' }])}
      className="mt-2 text-xs text-blue-600 hover:text-blue-800"
    >
      + 添加技能项
    </button>
  </div>
);

/** 「不限制」勾选：勾上时清空输入并写入标记值，服务端据此跳过该维度评分 */
const UnconstrainedToggle = ({
  checked, onChange, label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) => (
  <label className="flex items-center gap-1.5 text-xs text-slate-600 mt-1 cursor-pointer">
    <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    {label}
  </label>
);

const isUnconstrained = (v?: string | null) =>
  !v || !v.trim() || ['不限制', '无', '不限', '无要求', '不设限'].includes(v.trim());

const ProfileEditor = ({ profile, onChange }: { profile: JdProfile; onChange: (p: JdProfile) => void }) => {
  const list = (key: keyof JdProfile) => (profile[key] as string[]) || [];
  const basic = profile.basic || {};
  const eduFree = isUnconstrained(basic.education_required);
  const yearsFree = isUnconstrained(basic.experience_years_required);
  const cultureFree = list('culture_values').length === 0
    || list('culture_values').every((v) => ['不限制', '无', '不限', '无要求', '不设限'].includes(v.trim()));

  const setBasic = (patch: Partial<JdBasic>) =>
    onChange({ ...profile, basic: { ...basic, ...patch } });

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-sm">
            <span className="text-slate-600">学历要求</span>
            <input
              className="w-full border border-slate-300 rounded px-2 py-1 mt-1 disabled:bg-slate-100"
              disabled={eduFree}
              value={basic.education_required || ''}
              placeholder="如：本科及以上"
              onChange={(e) => setBasic({ education_required: e.target.value })}
            />
          </label>
          <UnconstrainedToggle
            checked={eduFree}
            label="不限制学历（该维度不参与评分，不卡候选人）"
            onChange={(v) => setBasic({ education_required: v ? '不限制' : '' })}
          />
        </div>
        <div>
          <label className="text-sm">
            <span className="text-slate-600">年限要求</span>
            <input
              className="w-full border border-slate-300 rounded px-2 py-1 mt-1 disabled:bg-slate-100"
              disabled={yearsFree}
              value={basic.experience_years_required || ''}
              placeholder="如：3年以上"
              onChange={(e) => setBasic({ experience_years_required: e.target.value })}
            />
          </label>
          <UnconstrainedToggle
            checked={yearsFree}
            label="不限制年限（不按年限扣分，只评职责相关性）"
            onChange={(v) => setBasic({ experience_years_required: v ? '不限制' : '' })}
          />
        </div>
      </div>

      <SkillEditor label="硬性要求技能（人岗匹配逐条核对的依据）" skills={profile.required_skills}
        onChange={(next) => onChange({ ...profile, required_skills: next })} />
      <SkillEditor label="加分技能" skills={profile.preferred_skills}
        onChange={(next) => onChange({ ...profile, preferred_skills: next })} />

      <div>
        <p className="text-sm font-medium text-slate-700 mb-2">
          企业价值观（文化契合评估依据，须来自 JD 原文）
        </p>
        {!cultureFree ? (
          <div className="flex flex-wrap gap-2">
            {list('culture_values').map((v, i) => (
              <span key={i} className="px-2 py-1 bg-purple-50 text-purple-700 text-sm rounded">{v}</span>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-500">
            未设置价值观，该维度不参与评分，文化门槛直接放行。
          </p>
        )}
        <UnconstrainedToggle
          checked={cultureFree}
          label="不设企业价值观（文化契合不评分、不卡人）"
          onChange={(v) => onChange({ ...profile, culture_values: v ? ['不限制'] : [] })}
        />
      </div>

      <div>
        <p className="text-sm font-medium text-slate-700 mb-2">岗位职责</p>
        <ul className="text-sm text-slate-600 space-y-1">
          {list('responsibilities').map((r, i) => <li key={i}>· {r}</li>)}
        </ul>
      </div>
    </div>
  );
};

/* ----------------------------------------------------------------- JD 新建表单 */

const JdCreateForm = ({
  positionId, positionTitle, onDone, onCancel,
}: {
  positionId: number;
  positionTitle: string;
  onDone: () => void | Promise<void>;
  onCancel: () => void;
}) => {
  const [rawText, setRawText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!rawText.trim() && !file) {
      setErr('请粘贴 JD 文本或上传 JD 文件');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await createJobDescription({
        position_id: positionId,
        title: positionTitle,
        raw_text: rawText,
        file,
      });
      await onDone();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 space-y-3">
      <p className="text-sm text-slate-600">
        新建「{positionTitle}」的 JD。录入后需 AI 解析、人工核对原文依据，才能启用。
      </p>
      <textarea
        className="w-full border border-slate-300 rounded px-2 py-2 h-40 font-mono text-xs"
        value={rawText}
        onChange={(e) => setRawText(e.target.value)}
        placeholder="粘贴 JD 正文：岗位职责、任职资格、加分项…"
      />
      <label className="flex items-center gap-2 text-sm text-slate-600">
        <Upload className="w-4 h-4" />
        <input
          type="file"
          accept=".txt,.pdf,.docx,.doc,.html,.htm,image/*"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        {file && <span className="text-slate-800">{file.name}</span>}
      </label>
      {err && <p className="text-xs text-red-600">{err}</p>}
      <div className="flex gap-2">
        <button
          onClick={submit}
          disabled={busy}
          className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm disabled:opacity-50 flex items-center gap-1.5"
        >
          {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          创建 JD
        </button>
        <button onClick={onCancel} className="px-3 py-1.5 rounded text-sm border border-slate-300">
          取消
        </button>
      </div>
    </div>
  );
};

/* ----------------------------------------------------------------- 主页面 */

const Positions: React.FC = () => {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [expanded, setExpanded] = useState<number | null>(null);
  const [jds, setJds] = useState<Record<number, JobDescription[]>>({});

  const [creating, setCreating] = useState(false);
  const [editingPos, setEditingPos] = useState<Position | null>(null);
  const [jdCreatingFor, setJdCreatingFor] = useState<number | null>(null);
  const [profileEditing, setProfileEditing] = useState<JobDescription | null>(null);
  const [draftProfile, setDraftProfile] = useState<JdProfile | null>(null);

  const loadPositions = useCallback(async () => {
    setLoading(true);
    try {
      setPositions(await getPositions());
      setMessage(null);
    } catch (e: any) {
      setMessage({ kind: 'err', text: e.message });
    } finally {
      setLoading(false);
    }
  }, []);

  const loadJds = useCallback(async (positionId: number) => {
    try {
      const list = await getJobDescriptions({ position_id: positionId });
      setJds((prev) => ({ ...prev, [positionId]: list }));
    } catch (e: any) {
      setMessage({ kind: 'err', text: e.message });
    }
  }, []);

  useEffect(() => { loadPositions(); }, [loadPositions]);

  const toggle = async (p: Position) => {
    if (expanded === p.id) {
      setExpanded(null);
      return;
    }
    setExpanded(p.id);
    await loadJds(p.id);
  };

  const savePosition = async (data: any) => {
    setBusy('pos');
    try {
      if (editingPos) {
        await updatePosition(editingPos.id, data);
        setMessage({ kind: 'ok', text: '岗位已更新' });
      } else {
        await createPosition(data);
        setMessage({ kind: 'ok', text: '岗位已创建，现在可以在它下面新增 JD' });
      }
      setEditingPos(null);
      setCreating(false);
      await loadPositions();
    } catch (e: any) {
      setMessage({ kind: 'err', text: e.message });
    } finally {
      setBusy(null);
    }
  };

  const onClose = async (p: Position) => {
    if (!window.confirm(`确认关闭岗位「${p.title}」？\n其下已启用的 JD 会一并停用，已有候选人数据保留。`)) return;
    setBusy(`pos-${p.id}`);
    try {
      await closePosition(p.id);
      await loadPositions();
      if (expanded === p.id) await loadJds(p.id);
    } catch (e: any) {
      setMessage({ kind: 'err', text: e.message });
    } finally {
      setBusy(null);
    }
  };

  const onDeletePos = async (p: Position) => {
    if (!window.confirm(`确认删除岗位「${p.title}」？`)) return;
    setBusy(`pos-${p.id}`);
    try {
      await deletePosition(p.id);
      await loadPositions();
    } catch (e: any) {
      setMessage({ kind: 'err', text: e.message });
    } finally {
      setBusy(null);
    }
  };

  const doParse = async (positionId: number, jd: JobDescription) => {
    setBusy(`jd-${jd.id}`);
    setMessage(null);
    try {
      const updated = await parseJobDescription(jd.id);
      setMessage({
        kind: 'ok',
        text: `解析完成：硬技能 ${updated.parsed_data?.required_skills.length ?? 0} 项、加分 ${updated.parsed_data?.preferred_skills.length ?? 0} 项。请核对原文依据后启用。`,
      });
      setProfileEditing(updated);
      setDraftProfile(updated.parsed_data);
      await loadJds(positionId);
    } catch (e: any) {
      setMessage({ kind: 'err', text: e.message });
      await loadJds(positionId);
    } finally {
      setBusy(null);
    }
  };

  const saveProfile = async (positionId: number, jd: JobDescription) => {
    if (!draftProfile) return;
    setBusy(`jd-${jd.id}`);
    try {
      await updateJobDescription(jd.id, { parsed_data: draftProfile });
      setMessage({ kind: 'ok', text: '画像已保存（无原文依据的技能项已自动剔除）' });
      setProfileEditing(null);
      await loadJds(positionId);
    } catch (e: any) {
      setMessage({ kind: 'err', text: e.message });
    } finally {
      setBusy(null);
    }
  };

  const onActivate = async (positionId: number, jd: JobDescription) => {
    setBusy(`jd-${jd.id}`);
    try {
      await activateJobDescription(jd.id);
      setMessage({ kind: 'ok', text: 'JD 已启用，现在可以绑定候选人跑 AI 评估' });
      await loadJds(positionId);
    } catch (e: any) {
      setMessage({ kind: 'err', text: e.message });
    } finally {
      setBusy(null);
    }
  };

  const onArchiveJd = async (positionId: number, jd: JobDescription) => {
    setBusy(`jd-${jd.id}`);
    try {
      await archiveJobDescription(jd.id);
      await loadJds(positionId);
    } catch (e: any) {
      setMessage({ kind: 'err', text: e.message });
    } finally {
      setBusy(null);
    }
  };

  const onDeleteJd = async (positionId: number, jd: JobDescription) => {
    if (!window.confirm(`确认删除 JD「${jd.title}」？`)) return;
    setBusy(`jd-${jd.id}`);
    try {
      await deleteJobDescription(jd.id);
      await loadJds(positionId);
      await loadPositions();
    } catch (e: any) {
      setMessage({ kind: 'err', text: e.message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-800 flex items-center gap-2">
            <Briefcase className="w-6 h-6" />
            岗位管理
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            岗位 → 岗位 JD 两层。人岗匹配以启用状态的 JD 为依据，请先解析并核对原文依据。
          </p>
        </div>
        <button
          onClick={() => { setCreating(true); setEditingPos(null); }}
          className="flex items-center gap-1.5 bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 text-sm"
        >
          <Plus className="w-4 h-4" />
          新建岗位
        </button>
      </div>

      {message && (
        <div className={`flex items-start gap-2 p-3 rounded-lg text-sm ${
          message.kind === 'ok' ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'}`}>
          {message.kind === 'ok'
            ? <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
            : <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />}
          <span>{message.text}</span>
        </div>
      )}

      {creating && (
        <PositionForm onSave={savePosition} onCancel={() => setCreating(false)} busy={busy === 'pos'} />
      )}

      {loading ? (
        <p className="text-slate-400 text-sm">加载中…</p>
      ) : positions.length === 0 ? (
        <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-slate-400 text-sm">
          还没有岗位。新建岗位后，在它下面添加 JD，候选人才能启动 AI 人岗匹配。
        </div>
      ) : (
        <div className="space-y-3">
          {positions.map((p) => {
            const st = POS_STATUS[p.status];
            const list = jds[p.id] || [];
            return (
              <div key={p.id} className="bg-white rounded-xl border border-slate-200">
                <div className="p-4 flex items-start justify-between gap-3">
                  <button
                    onClick={() => toggle(p)}
                    className="flex items-start gap-2 text-left flex-1"
                  >
                    {expanded === p.id
                      ? <ChevronDown className="w-4 h-4 mt-1 text-slate-400 shrink-0" />
                      : <ChevronRight className="w-4 h-4 mt-1 text-slate-400 shrink-0" />}
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-slate-800">{p.title}</span>
                        <span className={`px-2 py-0.5 text-xs rounded ${st.cls}`}>{st.text}</span>
                      </div>
                      <p className="text-xs text-slate-500 mt-1">
                        {[p.department, p.location].filter(Boolean).join(' · ') || '未填部门/地点'}
                        {p.headcount ? ` · 招 ${p.headcount} 人` : ''}
                        {' · '}{p.jd_count} 份 JD（启用 {p.active_jd_count}）· 已绑定 {p.candidate_count} 位候选人
                      </p>
                      {p.active_jd_count === 0 && p.status !== 'archived' && (
                        <p className="text-xs text-amber-600 mt-1">
                          尚无已启用的 JD，该岗位下的候选人无法启动 AI 评估
                        </p>
                      )}
                    </div>
                  </button>
                  <div className="flex gap-2 shrink-0">
                    <button
                      onClick={() => { setEditingPos(p); setCreating(false); }}
                      className="flex items-center gap-1 text-xs border border-slate-300 px-2.5 py-1.5 rounded"
                    >
                      <Pencil className="w-3 h-3" />
                      编辑
                    </button>
                    {p.status !== 'archived' && (
                      <button
                        onClick={() => onClose(p)}
                        disabled={busy === `pos-${p.id}`}
                        className="flex items-center gap-1 text-xs border border-slate-300 px-2.5 py-1.5 rounded disabled:opacity-50"
                      >
                        <Archive className="w-3 h-3" />
                        关闭招聘
                      </button>
                    )}
                    <button
                      onClick={() => onDeletePos(p)}
                      disabled={busy === `pos-${p.id}` || p.jd_count > 0}
                      title={p.jd_count > 0 ? '岗位下还有 JD，请改用「关闭招聘」' : '删除'}
                      className="flex items-center gap-1 text-xs border border-slate-300 px-2.5 py-1.5 rounded disabled:opacity-40"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                </div>

                {editingPos?.id === p.id && (
                  <div className="px-4 pb-4">
                    <PositionForm
                      initial={p}
                      busy={busy === 'pos'}
                      onSave={savePosition}
                      onCancel={() => setEditingPos(null)}
                    />
                  </div>
                )}

                {expanded === p.id && (
                  <div className="border-t border-slate-100 p-4 space-y-3 bg-slate-50">
                    {jdCreatingFor !== p.id && (
                      <button
                        onClick={() => setJdCreatingFor(p.id)}
                        disabled={p.status === 'archived'}
                        className="flex items-center gap-1.5 text-sm border border-slate-300 bg-white px-3 py-1.5 rounded disabled:opacity-40"
                      >
                        <Plus className="w-4 h-4" />
                        为该岗位新增 JD
                      </button>
                    )}
                    {jdCreatingFor === p.id && (
                      <JdCreateForm
                        positionId={p.id}
                        positionTitle={p.title}
                        onDone={() => { setJdCreatingFor(null); loadJds(p.id); loadPositions(); }}
                        onCancel={() => setJdCreatingFor(null)}
                      />
                    )}

                    {list.length === 0 && jdCreatingFor !== p.id && (
                      <p className="text-sm text-slate-400">该岗位下还没有 JD。</p>
                    )}

                    {list.map((jd) => {
                      const js = JD_STATUS[jd.status];
                      const ps = PARSE_STATUS[jd.parse_status];
                      return (
                        <div key={jd.id} className="bg-white rounded-lg border border-slate-200 p-3">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-slate-800">{jd.title}</span>
                                <span className={`px-2 py-0.5 text-xs rounded ${js.cls}`}>{js.text}</span>
                                <span className={`px-2 py-0.5 text-xs rounded ${ps.cls}`}>{ps.text}</span>
                              </div>
                              <p className="text-xs text-slate-500 mt-1">
                                已绑定 {jd.candidate_count} 位候选人
                                {jd.parsed_data &&
                                  ` · 硬技能 ${jd.parsed_data.required_skills.length} / 加分 ${jd.parsed_data.preferred_skills.length}`}
                              </p>
                              {jd.parse_status === 'failed' && jd.parse_error && (
                                <p className="text-xs text-red-600 mt-1">解析失败：{jd.parse_error}</p>
                              )}
                            </div>
                            <div className="flex gap-2 shrink-0">
                              <button
                                onClick={() => doParse(p.id, jd)}
                                disabled={busy === `jd-${jd.id}`}
                                className="flex items-center gap-1 text-xs border border-slate-300 px-2.5 py-1 rounded disabled:opacity-50"
                              >
                                {busy === `jd-${jd.id}`
                                  ? <Loader2 className="w-3 h-3 animate-spin" />
                                  : <FileText className="w-3 h-3" />}
                                {jd.parse_status === 'parsed' ? '重新解析' : 'AI 解析'}
                              </button>
                              {jd.parse_status === 'parsed' && jd.status !== 'active' && (
                                <button
                                  onClick={() => onActivate(p.id, jd)}
                                  disabled={busy === `jd-${jd.id}`}
                                  className="flex items-center gap-1 text-xs bg-emerald-600 text-white px-2.5 py-1 rounded disabled:opacity-50"
                                >
                                  <CheckCircle2 className="w-3 h-3" />
                                  启用
                                </button>
                              )}
                              {jd.status === 'active' && (
                                <button
                                  onClick={() => onArchiveJd(p.id, jd)}
                                  disabled={busy === `jd-${jd.id}`}
                                  className="flex items-center gap-1 text-xs border border-slate-300 px-2.5 py-1 rounded disabled:opacity-50"
                                >
                                  <XCircle className="w-3 h-3" />
                                  停用
                                </button>
                              )}
                              <button
                                onClick={() => onDeleteJd(p.id, jd)}
                                disabled={busy === `jd-${jd.id}` || jd.candidate_count > 0}
                                title={jd.candidate_count > 0 ? '已有候选人绑定，无法删除' : '删除'}
                                className="flex items-center gap-1 text-xs border border-slate-300 px-2.5 py-1 rounded disabled:opacity-40"
                              >
                                <Trash2 className="w-3 h-3" />
                              </button>
                            </div>
                          </div>

                          {jd.parse_status === 'parsed' && profileEditing?.id !== jd.id && (
                            <button
                              onClick={() => { setProfileEditing(jd); setDraftProfile(jd.parsed_data); }}
                              className="mt-2 text-xs text-blue-600 hover:text-blue-800"
                            >
                              查看/编辑岗位画像
                            </button>
                          )}

                          {profileEditing?.id === jd.id && jd.parsed_data && (
                            <div className="mt-3 pt-3 border-t border-slate-100">
                              <p className="text-xs text-slate-500 mb-3">
                                人工核对：每项技能的「JD 原文依据」是匹配依据的来源，确认与原文一致。
                              </p>
                              <ProfileEditor profile={draftProfile || jd.parsed_data} onChange={setDraftProfile} />
                              <div className="flex gap-2 mt-4">
                                <button
                                  onClick={() => saveProfile(p.id, jd)}
                                  disabled={busy === `jd-${jd.id}`}
                                  className="text-sm bg-slate-700 text-white px-3 py-1.5 rounded disabled:opacity-50"
                                >
                                  保存核对结果
                                </button>
                                <button
                                  onClick={() => setProfileEditing(null)}
                                  className="text-sm border border-slate-300 px-3 py-1.5 rounded"
                                >
                                  收起
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default Positions;
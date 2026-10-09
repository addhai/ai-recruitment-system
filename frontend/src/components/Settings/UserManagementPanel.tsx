import React, { useCallback, useEffect, useState } from 'react';
import { Plus, X, Edit2, KeyRound, Ban, CheckCircle2, Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import type { ManagedUser, Role } from '../../services/users';
import {
  ROLE_DESCRIPTIONS,
  ROLE_LABELS,
  ROLES,
  USER_PAGE_SIZE,
  changeOwnPassword,
  createUser,
  listUsers,
  resetUserPassword,
  updateUser,
} from '../../services/users';

/**
 * 用户管理。数据全部来自后端——此前这一页是本地演示数据（页面上自己标着
 * "接口将在后续版本提供"），而 register 端点收紧到管理员之后，管理员就
 * 完全没有界面可以建号了。
 *
 * 只有「停用」没有「删除」：各表外键指向 users 且未声明 ondelete，
 * 硬删在 PostgreSQL 上会违反外键，且会让历史评估指向不存在的人。
 */
const errText = (e: any) => e?.message || '操作失败';

const UserManagementPanel: React.FC = () => {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<ManagedUser | null>(null);
  const [resetting, setResetting] = useState<ManagedUser | null>(null);
  const [pwdForm, setPwdForm] = useState({ oldPwd: '', newPwd: '' });

  const [form, setForm] = useState<{
    username: string; email: string; password: string;
    full_name: string; department: string; role: Role;
  }>({ username: '', email: '', password: '', full_name: '', department: '', role: 'hr' });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setUsers(await listUsers());
      setMsg(null);
    } catch (e: any) {
      setMsg({ kind: 'err', text: errText(e) });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const run = async (fn: () => Promise<unknown>, okText: string) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      await load();
      setMsg({ kind: 'ok', text: okText });
      return true;
    } catch (e: any) {
      setMsg({ kind: 'err', text: errText(e) });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const submitAdd = async () => {
    const ok = await run(
      () => createUser({
        username: form.username.trim(),
        email: form.email.trim(),
        password: form.password,
        full_name: form.full_name.trim() || undefined,
        department: form.department.trim() || undefined,
        role: form.role,
      }),
      `已创建用户 ${form.username}`,
    );
    if (ok) {
      setShowAdd(false);
      setForm({ username: '', email: '', password: '', full_name: '', department: '', role: 'hr' });
    }
  };

  const submitEdit = async () => {
    if (!editing) return;
    const ok = await run(
      () => updateUser(editing.id, {
        email: editing.email,
        full_name: editing.full_name ?? '',
        department: editing.department ?? '',
        role: editing.role as Role,
        is_active: editing.is_active,
      }),
      `已保存 ${editing.username} 的修改`,
    );
    if (ok) setEditing(null);
  };

  const submitReset = async () => {
    if (!resetting) return;
    const ok = await run(
      () => resetUserPassword(resetting.id, pwdForm.newPwd),
      `已重置 ${resetting.username} 的密码，其此前的登录状态已全部失效`,
    );
    if (ok) { setResetting(null); setPwdForm({ oldPwd: '', newPwd: '' }); }
  };

  const submitOwnPassword = async () => {
    const ok = await run(async () => {
      const r = await changeOwnPassword(pwdForm.oldPwd, pwdForm.newPwd);
      // 换掉本地令牌：后端已让旧令牌失效，不更新的话下一次请求就会 401
      localStorage.setItem('token', r.access_token);
    }, '密码已修改，其他设备上的登录状态已失效');
    if (ok) setPwdForm({ oldPwd: '', newPwd: '' });
  };

  const roleBadge = (role: string) =>
    role === 'admin' ? 'bg-red-100 text-red-700'
      : role === 'hr' ? 'bg-blue-100 text-blue-700'
        : role === 'interviewer' ? 'bg-green-100 text-green-700'
          : 'bg-slate-100 text-slate-700';

  const counts = ROLES.map((r) => ({
    role: r,
    count: users.filter((u) => u.role === r).length,
    active: users.filter((u) => u.role === r && u.is_active).length,
  }));

  return (
    <div className="space-y-6">
      {me && (
        <div className="flex items-center gap-4 p-4 bg-gradient-to-r from-blue-50 to-purple-50 dark:from-blue-900/30 dark:to-purple-900/30 border border-blue-100 dark:border-blue-800 rounded-xl">
          <div className="w-12 h-12 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white font-bold">
            {(me.full_name || me.username).charAt(0)}
          </div>
          <div className="flex-1">
            <p className="font-semibold text-slate-800 dark:text-slate-100">
              {me.full_name || me.username}
            </p>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {me.department || '—'} · 角色：{ROLE_LABELS[me.role] || me.role}
            </p>
          </div>
          <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-green-100 text-green-700">
            当前登录
          </span>
        </div>
      )}

      {msg && (
        <div className={`flex items-start gap-2 p-3 rounded-lg text-sm ${
          msg.kind === 'ok' ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800`'}`}>
          {msg.kind === 'ok'
            ? <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
            : <X className="w-4 h-4 mt-0.5 shrink-0" />}
          <span>{msg.text}</span>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-slate-800 dark:text-slate-100">用户管理</h3>
          <p className="text-xs text-slate-400 mt-0.5">
            停用代替删除——历史评估与面试记录必须一直能指向真人。
            停用或重置密码会立即让该用户所有已签发的登录状态失效。
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm"
        >
          <Plus size={16} /> 添加用户
        </button>
      </div>

      {loading ? (
        <p className="text-slate-400 text-sm flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" /> 加载中…
        </p>
      ) : (
        <>
          {users.length >= USER_PAGE_SIZE && (
            <p className="text-xs text-amber-700 bg-amber-50 rounded-lg px-3 py-2">
              已达单次拉取上限 {USER_PAGE_SIZE} 人，下面可能不是全部用户。
              接口暂未返回总数，需要看全量请调大上限或加搜索。
            </p>
          )}
          <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-500 dark:text-slate-400 border-b border-slate-100 dark:border-slate-700">
                <th className="pb-3 font-medium">用户名</th>
                <th className="pb-3 font-medium">姓名</th>
                <th className="pb-3 font-medium">邮箱</th>
                <th className="pb-3 font-medium">部门</th>
                <th className="pb-3 font-medium">角色</th>
                <th className="pb-3 font-medium">状态</th>
                <th className="pb-3 font-medium text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {users.map((u) => (
                <tr key={u.id} className={u.is_active ? '' : 'opacity-50'}>
                  <td className="py-3">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center text-white text-sm">
                        {(u.full_name || u.username).charAt(0)}
                      </div>
                      <span className="font-medium text-slate-800 dark:text-slate-200">{u.username}</span>
                    </div>
                  </td>
                  <td className="py-3 text-slate-600 dark:text-slate-300">{u.full_name || '—'}</td>
                  <td className="py-3 text-slate-600 dark:text-slate-300">{u.email}</td>
                  <td className="py-3 text-slate-600 dark:text-slate-300">{u.department || '—'}</td>
                  <td className="py-3">
                    <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${roleBadge(u.role)}`}>
                      {ROLE_LABELS[u.role] || u.role}
                    </span>
                  </td>
                  <td className="py-3">
                    {u.is_active ? (
                      <span className="inline-flex items-center gap-1.5 text-sm text-green-600">
                        <span className="w-2 h-2 bg-green-500 rounded-full" /> 正常
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 text-sm text-slate-500">
                        <span className="w-2 h-2 bg-slate-400 rounded-full" /> 已停用
                      </span>
                    )}
                  </td>
                  <td className="py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => setEditing({ ...u })}
                              className="p-1.5 text-slate-400 hover:text-blue-600 rounded-lg"
                              title="编辑资料与角色">
                        <Edit2 size={16} />
                      </button>
                      <button onClick={() => { setResetting(u); setPwdForm({ oldPwd: '', newPwd: '' }); }}
                              className="p-1.5 text-slate-400 hover:text-amber-600 rounded-lg"
                              title="重置该用户密码">
                        <KeyRound size={16} />
                      </button>
                      {u.id !== me?.id && (
                        <button
                          disabled={busy}
                          onClick={() => run(
                            () => updateUser(u.id, { is_active: !u.is_active }),
                            u.is_active ? `已停用 ${u.username}` : `已启用 ${u.username}`)}
                          className={`p-1.5 rounded-lg ${u.is_active
                            ? 'text-slate-400 hover:text-red-600'
                            : 'text-green-600 hover:text-green-700'}`}
                          title={u.is_active ? '停用该账号' : '重新启用'}
                        >
                          {u.is_active ? <Ban size={16} /> : <CheckCircle2 size={16} />}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </>
      )}

      {/* ---------- 角色分布（真实统计，不再写死） ---------- */}
      <div>
        <h4 className="font-medium text-slate-700 dark:text-slate-200 mb-3">角色分布</h4>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {counts.map((c) => (
            <div key={c.role} className="p-4 border border-slate-200 dark:border-slate-700 rounded-lg">
              <div className="flex items-start justify-between">
                <div>
                  <h5 className="font-medium text-slate-800 dark:text-slate-100">{ROLE_LABELS[c.role]}</h5>
                  <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
                    {ROLE_DESCRIPTIONS[c.role]}
                  </p>
                </div>
                <span className="text-sm text-slate-400 whitespace-nowrap">
                  {c.active} 可用
                  {c.count > c.active && ` / ${c.count - c.active} 停用`}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ---------- 修改自己的密码 ---------- */}
      <div className="border border-slate-200 dark:border-slate-700 rounded-xl p-4">
        <h4 className="font-medium text-slate-700 dark:text-slate-200 mb-1">修改我的密码</h4>
        <p className="text-xs text-slate-400 mb-3">
          修改成功后，你在其他设备上的登录状态会立即失效（本页会自动换成新令牌，不中断）。
          密码泄漏时的首选止血手段。
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="text-slate-600 dark:text-slate-300">当前密码</span>
            <input type="password" value={pwdForm.oldPwd}
                   onChange={(e) => setPwdForm({ ...pwdForm, oldPwd: e.target.value })}
                   className="block w-48 border border-slate-300 dark:border-slate-600 dark:bg-slate-700 rounded px-3 py-2 mt-1 text-sm" />
          </label>
          <label className="text-sm">
            <span className="text-slate-600 dark:text-slate-300">新密码（至少 8 位）</span>
            <input type="password" value={pwdForm.newPwd}
                   onChange={(e) => setPwdForm({ ...pwdForm, newPwd: e.target.value })}
                   className="block w-48 border border-slate-300 dark:border-slate-600 dark:bg-slate-700 rounded px-3 py-2 mt-1 text-sm" />
          </label>
          <button
            disabled={busy || !pwdForm.oldPwd || pwdForm.newPwd.length < 8}
            onClick={submitOwnPassword}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            修改密码
          </button>
        </div>
      </div>

      {/* ---------- 添加用户 ---------- */}
      {showAdd && (
        <Modal title="添加用户" onClose={() => setShowAdd(false)} onSubmit={submitAdd}
               busy={busy} submitLabel="创建">
          <Field label="用户名 *">
            <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })}
                   className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            <p className="text-xs text-slate-400 mt-1">创建后不可修改（它是登录凭据与数据关联依据）</p>
          </Field>
          <Field label="邮箱 *">
            <input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })}
                   className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
          </Field>
          <Field label="初始密码 *（至少 8 位）">
            <input type="password" value={form.password}
                   onChange={(e) => setForm({ ...form, password: e.target.value })}
                   className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="姓名">
              <input value={form.full_name}
                     onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                     className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </Field>
            <Field label="部门">
              <input value={form.department}
                     onChange={(e) => setForm({ ...form, department: e.target.value })}
                     className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </Field>
          </div>
          <Field label="角色">
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
                    className="w-full border border-slate-300 rounded px-3 py-2 text-sm">
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
            </select>
            <p className="text-xs text-slate-400 mt-1">{ROLE_DESCRIPTIONS[form.role]}</p>
          </Field>
        </Modal>
      )}

      {/* ---------- 编辑用户 ---------- */}
      {editing && (
        <Modal title={`编辑 ${editing.username}`} onClose={() => setEditing(null)}
               onSubmit={submitEdit} busy={busy} submitLabel="保存">
          <Field label="邮箱">
            <input value={editing.email}
                   onChange={(e) => setEditing({ ...editing, email: e.target.value })}
                   className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="姓名">
              <input value={editing.full_name ?? ''}
                     onChange={(e) => setEditing({ ...editing, full_name: e.target.value })}
                     className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </Field>
            <Field label="部门">
              <input value={editing.department ?? ''}
                     onChange={(e) => setEditing({ ...editing, department: e.target.value })}
                     className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </Field>
          </div>
          <Field label="角色">
            <select value={editing.role}
                    onChange={(e) => setEditing({ ...editing, role: e.target.value })}
                    className="w-full border border-slate-300 rounded px-3 py-2 text-sm">
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
            </select>
            <p className="text-xs text-slate-400 mt-1">{ROLE_DESCRIPTIONS[editing.role]}</p>
          </Field>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={editing.is_active}
                   onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
            账号可用
          </label>
        </Modal>
      )}

      {/* ---------- 重置他人密码 ---------- */}
      {resetting && (
        <Modal title={`重置 ${resetting.username} 的密码`} onClose={() => setResetting(null)}
               onSubmit={submitReset} busy={busy} submitLabel="重置">
          <p className="text-xs text-slate-500">
            重置后该用户此前的登录状态会**全部失效**，需要用新密码重新登录。
            这是用户忘记密码时唯一的出路——没有它，账号只能弃用重开，
            历史评估也就跟着断了。
          </p>
          <Field label="新密码（至少 8 位）">
            <input type="password" value={pwdForm.newPwd}
                   onChange={(e) => setPwdForm({ ...pwdForm, newPwd: e.target.value })}
                   className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
          </Field>
        </Modal>
      )}
    </div>
  );
};

// ---------------------------------------------------------------- 小组件

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div>
    <label className="block text-sm text-slate-600 dark:text-slate-300 mb-1">{label}</label>
    {children}
  </div>
);

const Modal: React.FC<{
  title: string; children: React.ReactNode; busy: boolean; submitLabel: string;
  onClose: () => void; onSubmit: () => void;
}> = ({ title, children, busy, submitLabel, onClose, onSubmit }) => (
  <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
    <div className="bg-white dark:bg-slate-800 rounded-xl w-full max-w-md p-6 shadow-xl max-h-full overflow-y-auto">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-slate-800 dark:text-slate-100">{title}</h3>
        <button onClick={onClose} className="p-2 text-slate-400 hover:text-slate-600 rounded-lg">
          <X size={20} />
        </button>
      </div>
      <div className="space-y-4">{children}</div>
      <div className="flex justify-end gap-2 mt-6">
        <button onClick={onClose} className="px-4 py-2 text-sm text-slate-600 rounded-lg hover:bg-slate-100">
          取消
        </button>
        <button onClick={onSubmit} disabled={busy}
                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50">
          {busy ? '处理中…' : submitLabel}
        </button>
      </div>
    </div>
  </div>
);

export default UserManagementPanel;

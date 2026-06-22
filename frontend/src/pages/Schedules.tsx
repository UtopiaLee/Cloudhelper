import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Schedule } from "../lib/api";
import { useAccount } from "../lib/account-context";
import { PageHeader } from "../lib/components";
import { AccountPicker } from "../lib/ui";

export default function SchedulesPage() {
  const { current } = useAccount();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  const list = useQuery({
    enabled: !!current,
    queryKey: ["schedules", current?.id],
    queryFn: async () => (await api.get<Schedule[]>(`/accounts/${current!.id}/schedules`)).data,
  });
  const del = useMutation({
    mutationFn: async (id: number) => api.delete(`/accounts/${current!.id}/schedules/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules", current?.id] }),
  });
  const toggle = useMutation({
    mutationFn: async (s: Schedule) => api.put(`/accounts/${current!.id}/schedules/${s.id}`, { ...s, enabled: !s.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules", current?.id] }),
  });

  return (
    <div>
      <PageHeader
        title="定时任务"
        description={current ? `${current.provider.toUpperCase()} · ${current.name}` : undefined}
        actions={<button className="btn-primary" disabled={!current} onClick={() => setOpen(true)}>+ 添加任务</button>}
      />
      <AccountPicker />
      <div className="glass !p-0 overflow-x-auto">
        <table className="table-clean">
          <thead>
            <tr>
              <th className="pl-4">实例</th><th>动作</th><th>Cron</th>
              <th>启用</th><th>备注</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {list.isLoading && <tr><td className="py-8 text-center text-slate-400" colSpan={6}>加载中…</td></tr>}
            {list.data?.map((s) => (
              <tr key={s.id}>
                <td className="pl-4 font-mono text-xs">{s.instance_id}</td>
                <td><span className="tag tag-info uppercase">{s.action}</span></td>
                <td className="font-mono text-xs">{s.cron}</td>
                <td><input type="checkbox" checked={s.enabled} onChange={() => toggle.mutate(s)} /></td>
                <td className="text-xs text-slate-500">{s.note}</td>
                <td><button className="btn-danger btn-sm" onClick={() => confirm("删除？") && del.mutate(s.id)}>删除</button></td>
              </tr>
            ))}
            {!list.isLoading && list.data?.length === 0 && <tr><td className="py-8 text-center text-slate-400" colSpan={6}>暂无任务</td></tr>}
          </tbody>
        </table>
      </div>
      {open && current && <CreateScheduleModal accountId={current.id} onClose={() => { setOpen(false); qc.invalidateQueries({ queryKey: ["schedules", current.id] }); }} />}
    </div>
  );
}

function CreateScheduleModal({ accountId, onClose }: { accountId: number; onClose: () => void }) {
  const [f, setF] = useState({ instance_id: "", action: "stop" as "start" | "stop" | "restart" | "destroy", cron: "0 23 * * *", enabled: true, note: "" });
  const create = useMutation({
    mutationFn: async () => api.post(`/accounts/${accountId}/schedules`, f),
    onSuccess: onClose,
  });
  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-10 p-4">
      <div className="glass w-[480px] !p-0">
        <div className="px-6 py-4 border-b border-white/40">
          <h3 className="text-lg font-bold gradient-text">添加定时任务</h3>
        </div>
        <div className="p-6 space-y-4">
          <div><label className="label">实例 ID</label>
            <input className="input font-mono" value={f.instance_id} onChange={(e) => setF({ ...f, instance_id: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">动作</label>
              <select className="input" value={f.action} onChange={(e) => setF({ ...f, action: e.target.value as any })}>
                <option value="start">启动</option>
                <option value="stop">停止</option>
                <option value="restart">重启</option>
                <option value="destroy">销毁</option>
              </select></div>
            <div><label className="label">Cron（5 段）</label>
              <input className="input font-mono" value={f.cron} onChange={(e) => setF({ ...f, cron: e.target.value })} /></div>
          </div>
          <div><label className="label">备注</label>
            <input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></div>
          {f.action === "destroy" && (
            <div className="text-sm text-red-700 bg-red-50/80 border border-red-200/60 rounded-lg p-3">
              ⚠ <b>销毁</b>不可恢复。到时间会调用 terminate 删除该实例（含磁盘）。
            </div>
          )}
          <div className="text-xs text-slate-500 bg-slate-50/60 rounded-lg p-3">
            <div>示例：</div>
            <div className="font-mono mt-1"><code>0 23 * * *</code> — 每天 23:00</div>
            <div className="font-mono"><code>0 0 1 * *</code> — 每月 1 日 00:00</div>
            <div className="font-mono"><code>*/30 * * * *</code> — 每 30 分钟</div>
          </div>
          {create.isError && <div className="text-sm text-red-600 bg-red-50/80 border border-red-200/60 rounded-lg p-2.5">{(create.error as Error).message}</div>}
        </div>
        <div className="px-6 py-3 border-t border-white/40 flex justify-end gap-2">
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn-primary" disabled={create.isPending} onClick={() => create.mutate()}>保存</button>
        </div>
      </div>
    </div>
  );
}

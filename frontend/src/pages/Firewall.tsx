import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, FirewallRule } from "../lib/api";
import { useAccount } from "../lib/account-context";
import { PageHeader } from "../lib/components";
import { AccountPicker } from "../lib/ui";

export default function FirewallPage() {
  const { current } = useAccount();
  const qc = useQueryClient();
  const [region, setRegion] = useState("");
  const [open, setOpen] = useState(false);

  const list = useQuery({
    enabled: !!current,
    queryKey: ["firewall", current?.id, region],
    queryFn: async () => (await api.get<FirewallRule[]>(`/accounts/${current!.id}/firewall`, { params: region ? { region } : {} })).data,
  });

  const del = useMutation({
    mutationFn: async (rule: FirewallRule) =>
      api.delete(`/accounts/${current!.id}/firewall/${encodeURIComponent(rule.id)}`, { params: { region: region || current!.default_region } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["firewall", current?.id, region] }),
  });

  return (
    <div>
      <PageHeader
        title="防火墙"
        description={current ? `${current.provider.toUpperCase()} · ${current.name}` : undefined}
        actions={<button className="btn-primary" disabled={!current} onClick={() => setOpen(true)}>+ 添加规则</button>}
      />
      <AccountPicker />
      {current && (
        <div className="flex items-center gap-2 mb-4">
          <span className="text-sm text-slate-500">区域：</span>
          <input className="input w-48 font-mono" placeholder={current.default_region} value={region} onChange={(e) => setRegion(e.target.value)} />
        </div>
      )}
      <div className="glass !p-0 overflow-x-auto">
        <table className="table-clean">
          <thead>
            <tr>
              <th className="pl-4">方向</th><th>协议</th><th>端口</th>
              <th>CIDR</th><th>目标</th><th>说明</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {list.isLoading && <tr><td className="py-8 text-center text-slate-400" colSpan={7}>加载中…</td></tr>}
            {list.data?.map((r) => (
              <tr key={r.id}>
                <td className="pl-4">{r.direction === "ingress" ? <span className="tag tag-info">入站</span> : <span className="tag tag-stopped">出站</span>}</td>
                <td className="uppercase font-mono text-xs">{r.protocol}</td>
                <td className="font-mono">{r.port_range}</td>
                <td className="font-mono text-xs">{r.cidrs.join(", ")}</td>
                <td className="font-mono text-xs">{r.target}</td>
                <td className="text-xs text-slate-500">{r.description}</td>
                <td><button className="btn-danger btn-sm" onClick={() => confirm("删除此规则？") && del.mutate(r)}>删除</button></td>
              </tr>
            ))}
            {!list.isLoading && list.data?.length === 0 && <tr><td className="py-8 text-center text-slate-400" colSpan={7}>暂无规则</td></tr>}
          </tbody>
        </table>
      </div>
      {open && current && <CreateFirewallModal accountId={current.id} region={region || current.default_region} onClose={() => { setOpen(false); qc.invalidateQueries({ queryKey: ["firewall", current.id, region] }); }} />}
    </div>
  );
}

function CreateFirewallModal({ accountId, region, onClose }: { accountId: number; region: string; onClose: () => void }) {
  const [f, setF] = useState({
    direction: "ingress" as "ingress" | "egress",
    protocol: "tcp" as "tcp" | "udp" | "icmp" | "all",
    port_range: "22", cidrs: "0.0.0.0/0", description: "", target: "",
  });

  // 加载该 region 的 SG 列表
  const sgs = useQuery({
    queryKey: ["sgs-for-fw", accountId, region],
    queryFn: async () => (await api.get<{ id: string; name: string; description: string }[]>(
      `/accounts/${accountId}/instances/options/security-groups`,
      { params: { region } }
    )).data,
  });

  // 拉到 SG 后自动选第一个
  useEffect(() => {
    if (sgs.data && sgs.data.length > 0 && !f.target) {
      setF((prev) => ({ ...prev, target: sgs.data![0].id }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sgs.data]);

  const create = useMutation({
    mutationFn: async () => {
      if (!f.target) throw new Error("请选择安全组目标");
      return api.post(`/accounts/${accountId}/firewall`, {
        ...f, cidrs: f.cidrs.split(",").map((s) => s.trim()).filter(Boolean),
      }, { params: { region } });
    },
    onSuccess: onClose,
  });
  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-10 p-4">
      <div className="glass w-[520px] !p-0">
        <div className="px-6 py-4 border-b border-white/40">
          <h3 className="text-lg font-bold gradient-text">添加防火墙规则</h3>
        </div>
        <div className="p-6 space-y-4">
          <div>
            <label className="label">目标安全组</label>
            {sgs.isLoading ? (
              <div className="text-xs text-slate-400 px-2 py-2">加载中…</div>
            ) : sgs.data && sgs.data.length > 0 ? (
              <select className="input" value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })}>
                {sgs.data.map((sg) => (
                  <option key={sg.id} value={sg.id}>
                    {sg.name} ({sg.id}){sg.description ? ` — ${sg.description.slice(0, 40)}` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <input className="input font-mono" placeholder="sg-xxxxxxxx" value={f.target}
                onChange={(e) => setF({ ...f, target: e.target.value })} />
            )}
            <div className="text-[11px] text-slate-500 mt-1">规则会添加到这个安全组里</div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">方向</label>
              <select className="input" value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value as "ingress" | "egress" })}>
                <option value="ingress">入站</option><option value="egress">出站</option>
              </select></div>
            <div><label className="label">协议</label>
              <select className="input" value={f.protocol} onChange={(e) => setF({ ...f, protocol: e.target.value as "tcp" | "udp" | "icmp" | "all" })}>
                <option value="tcp">TCP</option><option value="udp">UDP</option><option value="icmp">ICMP</option><option value="all">ALL</option>
              </select></div>
          </div>
          <div><label className="label">端口（如 22 / 80-90 / *）</label>
            <input className="input font-mono" value={f.port_range} onChange={(e) => setF({ ...f, port_range: e.target.value })} /></div>
          <div><label className="label">CIDR（逗号分隔，0.0.0.0/0 = 全网）</label>
            <input className="input font-mono" value={f.cidrs} onChange={(e) => setF({ ...f, cidrs: e.target.value })} /></div>
          <div><label className="label">说明</label>
            <input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
          {/* 快捷预设 */}
          <div className="flex gap-1 flex-wrap text-[11px]">
            <span className="text-slate-500 mr-1 self-center">快速：</span>
            <button type="button" className="px-2 py-0.5 rounded bg-slate-100 hover:bg-slate-200 text-slate-700"
              onClick={() => setF({ ...f, protocol: "tcp", port_range: "22", cidrs: "0.0.0.0/0", description: "SSH" })}>SSH 22</button>
            <button type="button" className="px-2 py-0.5 rounded bg-slate-100 hover:bg-slate-200 text-slate-700"
              onClick={() => setF({ ...f, protocol: "tcp", port_range: "80", cidrs: "0.0.0.0/0", description: "HTTP" })}>HTTP 80</button>
            <button type="button" className="px-2 py-0.5 rounded bg-slate-100 hover:bg-slate-200 text-slate-700"
              onClick={() => setF({ ...f, protocol: "tcp", port_range: "443", cidrs: "0.0.0.0/0", description: "HTTPS" })}>HTTPS 443</button>
            <button type="button" className="px-2 py-0.5 rounded bg-slate-100 hover:bg-slate-200 text-slate-700"
              onClick={() => setF({ ...f, protocol: "icmp", port_range: "*", cidrs: "0.0.0.0/0", description: "Ping" })}>Ping</button>
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

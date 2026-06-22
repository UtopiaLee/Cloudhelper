import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, SSHKey } from "../lib/api";
import { PageHeader } from "../lib/components";

export default function SSHKeysPage() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["ssh-keys"],
    queryFn: async () => (await api.get<SSHKey[]>("/ssh-keys")).data,
  });

  const [showImport, setShowImport] = useState(false);
  const [genName, setGenName] = useState("default");

  const generate = useMutation({
    mutationFn: async () => (await api.post<SSHKey>("/ssh-keys/generate", null, { params: { name: genName, is_default: true } })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ssh-keys"] }),
  });
  const setDefault = useMutation({
    mutationFn: async (id: number) => api.put(`/ssh-keys/${id}/default`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ssh-keys"] }),
  });
  const del = useMutation({
    mutationFn: async (id: number) => api.delete(`/ssh-keys/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ssh-keys"] }),
  });

  return (
    <div>
      <PageHeader
        title="SSH 密钥"
        description="CloudHelper 用此密钥登录所有实例采集流量。把公钥贴到云的 SSH key 配置或实例 authorized_keys 里。"
        actions={
          <>
            <input className="input w-32" placeholder="新密钥名" value={genName} onChange={(e) => setGenName(e.target.value)} />
            <button className="btn-primary" disabled={generate.isPending} onClick={() => generate.mutate()}>
              {generate.isPending ? "生成中…" : "生成 Ed25519"}
            </button>
            <button className="btn" onClick={() => setShowImport(true)}>导入私钥</button>
          </>
        }
      />

      <div className="space-y-3">
        {list.data?.map((k) => (
          <div key={k.id} className="card">
            <div className="flex items-center gap-2 mb-3">
              <span className="font-semibold text-slate-900">{k.name}</span>
              {k.is_default && <span className="tag tag-running">默认</span>}
              <span className="text-xs text-slate-400 ml-auto">{new Date(k.created_at).toLocaleString()}</span>
            </div>
            <textarea className="input font-mono text-xs h-20 bg-slate-50" readOnly value={k.public_key} />
            <div className="flex gap-2 mt-3">
              <button className="btn" onClick={() => navigator.clipboard.writeText(k.public_key)}>复制公钥</button>
              {!k.is_default && <button className="btn" onClick={() => setDefault.mutate(k.id)}>设为默认</button>}
              <button className="btn-danger ml-auto" onClick={() => confirm("删除？") && del.mutate(k.id)}>删除</button>
            </div>
          </div>
        ))}
        {list.data?.length === 0 && (
          <div className="card text-center py-8 text-slate-400 text-sm">
            尚未配置密钥。点击 "生成 Ed25519" 一键创建。
          </div>
        )}
      </div>

      {showImport && <ImportModal onClose={() => { setShowImport(false); qc.invalidateQueries({ queryKey: ["ssh-keys"] }); }} />}
    </div>
  );
}

function ImportModal({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState({ name: "", private_key: "", passphrase: "", is_default: true });
  const create = useMutation({
    mutationFn: async () => api.post("/ssh-keys", f),
    onSuccess: onClose,
  });
  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-center justify-center z-10 p-4">
      <div className="glass w-[560px] !p-0">
        <div className="px-6 py-4 border-b border-white/40">
          <h3 className="text-lg font-bold gradient-text">导入 SSH 私钥</h3>
        </div>
        <div className="p-6 space-y-4">
          <div><label className="label">名称</label>
            <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
          <div><label className="label">私钥（OpenSSH / PEM）</label>
            <textarea className="input font-mono text-xs h-40" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----..."
              value={f.private_key} onChange={(e) => setF({ ...f, private_key: e.target.value })} /></div>
          <div><label className="label">Passphrase（如有）</label>
            <input type="password" className="input" value={f.passphrase} onChange={(e) => setF({ ...f, passphrase: e.target.value })} /></div>
          <label className="text-sm flex items-center gap-2">
            <input type="checkbox" checked={f.is_default} onChange={(e) => setF({ ...f, is_default: e.target.checked })} />
            设为默认密钥
          </label>
          {create.isError && <div className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-md p-2">{(create.error as Error).message}</div>}
        </div>
        <div className="px-6 py-3 border-t border-white/40 flex justify-end gap-2">
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn-primary" disabled={create.isPending} onClick={() => create.mutate()}>保存</button>
        </div>
      </div>
    </div>
  );
}

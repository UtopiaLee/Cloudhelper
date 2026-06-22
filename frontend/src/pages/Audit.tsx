import { useQuery } from "@tanstack/react-query";
import { api, AuditEntry } from "../lib/api";
import { PageHeader } from "../lib/components";

export default function AuditPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["audit"],
    queryFn: async () => (await api.get<AuditEntry[]>("/audit")).data,
    refetchInterval: 30_000,
  });
  return (
    <div>
      <PageHeader title="审计日志" description={`最近 ${data?.length || 0} 条`} />
      <div className="glass !p-0 overflow-x-auto">
        <table className="table-clean">
          <thead>
            <tr>
              <th className="pl-4">时间</th>
              <th>操作</th>
              <th>目标</th>
              <th>结果</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td className="py-8 text-center text-slate-400" colSpan={5}>加载中…</td></tr>}
            {data?.map((r) => (
              <tr key={r.id}>
                <td className="pl-4 whitespace-nowrap text-xs text-slate-600">{new Date(r.at).toLocaleString()}</td>
                <td className="font-mono text-xs">{r.action}</td>
                <td className="font-mono text-xs">{r.target}</td>
                <td>{r.ok ? <span className="tag tag-running">OK</span> : <span className="tag tag-error">ERR</span>}</td>
                <td className="font-mono text-xs max-w-xl truncate text-slate-500">
                  {r.error ? <span className="text-red-600">{r.error}</span> : JSON.stringify(r.detail)}
                </td>
              </tr>
            ))}
            {!isLoading && data?.length === 0 && <tr><td className="py-8 text-center text-slate-400" colSpan={5}>暂无日志</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

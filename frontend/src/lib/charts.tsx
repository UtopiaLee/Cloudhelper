import { motion } from "framer-motion";

interface Point {
  at: string;
  bytes_in: number;
  bytes_out: number;
  cpu_pct?: number;
  mem_pct?: number;
}

/** 简单 SVG 折线图，无外部依赖。data 已经是按时间排序的累计字节。 */
export function TrafficChart({
  data,
  width = 720,
  height = 180,
  series = "out",
}: {
  data: Point[];
  width?: number;
  height?: number;
  series?: "in" | "out" | "both";
}) {
  if (data.length < 2) {
    return (
      <div className="text-center text-slate-400 text-sm py-12">
        采样点不足，至少需要 2 个采集周期才能画图
      </div>
    );
  }

  const padL = 50, padR = 20, padT = 16, padB = 32;
  const W = width, H = height;
  const innerW = W - padL - padR, innerH = H - padT - padB;

  // 累计 → 区间增量（diff 后画更直观）
  const incrementsOut: number[] = [];
  const incrementsIn: number[] = [];
  for (let i = 1; i < data.length; i++) {
    const dOut = Math.max(0, data[i].bytes_out - data[i - 1].bytes_out);
    const dIn = Math.max(0, data[i].bytes_in - data[i - 1].bytes_in);
    incrementsOut.push(dOut);
    incrementsIn.push(dIn);
  }
  const labels = data.slice(1).map((d) => new Date(d.at));

  const valuesOut = series === "in" ? [] : incrementsOut;
  const valuesIn = series === "out" ? [] : incrementsIn;
  const allVals = [...valuesOut, ...valuesIn];
  const maxV = Math.max(1, ...allVals);

  const x = (i: number) => padL + (i / (incrementsOut.length - 1 || 1)) * innerW;
  const y = (v: number) => padT + innerH - (v / maxV) * innerH;

  function path(vals: number[]): string {
    return vals.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  }
  function area(vals: number[]): string {
    const line = vals.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
    return `${line} L ${x(vals.length - 1).toFixed(1)} ${padT + innerH} L ${x(0).toFixed(1)} ${padT + innerH} Z`;
  }

  // y 轴刻度
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((p) => maxV * p);

  function fmt(v: number): string {
    if (v >= 1e9) return (v / 1e9).toFixed(2) + " GB";
    if (v >= 1e6) return (v / 1e6).toFixed(2) + " MB";
    if (v >= 1e3) return (v / 1e3).toFixed(1) + " KB";
    return v.toFixed(0) + " B";
  }
  function fmtTime(d: Date): string {
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const h = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${m}/${dd} ${h}:${mm}`;
  }

  // X 轴只显示首尾和中间几个
  const xLabelIdxs = [0, Math.floor(labels.length / 4), Math.floor(labels.length / 2),
                       Math.floor(labels.length * 3 / 4), labels.length - 1];

  return (
    <motion.svg
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      width={W} height={H} viewBox={`0 0 ${W} ${H}`}
      className="w-full h-auto"
    >
      <defs>
        <linearGradient id="grad-out" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgb(99,102,241)" stopOpacity="0.45" />
          <stop offset="100%" stopColor="rgb(99,102,241)" stopOpacity="0.02" />
        </linearGradient>
        <linearGradient id="grad-in" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgb(16,185,129)" stopOpacity="0.45" />
          <stop offset="100%" stopColor="rgb(16,185,129)" stopOpacity="0.02" />
        </linearGradient>
      </defs>

      {/* 网格 */}
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={padL} y1={y(t)} x2={W - padR} y2={y(t)}
                stroke="rgba(148,163,184,0.18)" strokeDasharray="3 3" />
          <text x={padL - 6} y={y(t)} textAnchor="end" alignmentBaseline="middle"
                fontSize="10" fill="rgb(100,116,139)">{fmt(t)}</text>
        </g>
      ))}

      {/* X 标签 */}
      {xLabelIdxs.map((i) => (
        labels[i] ? (
          <text key={i} x={x(i)} y={H - 8} textAnchor="middle"
                fontSize="10" fill="rgb(100,116,139)">{fmtTime(labels[i])}</text>
        ) : null
      ))}

      {/* 出站 */}
      {valuesOut.length > 0 && (
        <>
          <path d={area(valuesOut)} fill="url(#grad-out)" />
          <path d={path(valuesOut)} fill="none" stroke="rgb(99,102,241)" strokeWidth="2" />
        </>
      )}
      {/* 入站 */}
      {valuesIn.length > 0 && (
        <>
          <path d={area(valuesIn)} fill="url(#grad-in)" />
          <path d={path(valuesIn)} fill="none" stroke="rgb(16,185,129)" strokeWidth="2" />
        </>
      )}

      {/* 图例 */}
      <g transform={`translate(${W - padR - 120}, 8)`}>
        {valuesOut.length > 0 && (
          <g>
            <rect width="12" height="3" y="6" fill="rgb(99,102,241)" />
            <text x="18" y="10" fontSize="11" fill="rgb(100,116,139)">出站增量</text>
          </g>
        )}
        {valuesIn.length > 0 && (
          <g transform="translate(70, 0)">
            <rect width="12" height="3" y="6" fill="rgb(16,185,129)" />
            <text x="18" y="10" fontSize="11" fill="rgb(100,116,139)">入站增量</text>
          </g>
        )}
      </g>
    </motion.svg>
  );
}

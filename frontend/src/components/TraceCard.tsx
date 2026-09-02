import { useState } from 'react'
import type { components } from '../api/schema.gen'

type ToolCallTraceOut = components['schemas']['ToolCallTraceOut']

interface TraceCardProps {
  trace: ToolCallTraceOut
  highlighted: boolean
}

type Scalar = string | number | boolean | null

/**
 * Splits a trace's arguments/result object into top-level primitive fields
 * (shown inline) and everything else (arrays, nested objects -- e.g.
 * run_backtest's 70+-entry trade_returns array, or the entire embedded
 * StrategyRule tree). One generic rule instead of per-tool_name formatting,
 * so a new tool added later needs no new frontend code to render sensibly.
 */
function summarizeScalars(obj: Record<string, unknown>): { scalars: [string, Scalar][]; hasMore: boolean } {
  const scalars: [string, Scalar][] = []
  let hasMore = false
  for (const [key, value] of Object.entries(obj)) {
    if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      scalars.push([key, value as Scalar])
    } else {
      hasMore = true
    }
  }
  return { scalars, hasMore }
}

function TraceObjectBlock({ label, obj }: { label: string; obj: Record<string, unknown> }) {
  const [showRaw, setShowRaw] = useState(false)
  const { scalars, hasMore } = summarizeScalars(obj)

  return (
    <div style={{ marginTop: 14 }}>
      <div className="card-eyebrow">{label}</div>
      {scalars.length > 0 ? (
        <dl className="kv-list">
          {scalars.map(([key, value]) => (
            <div key={key} className="kv-row">
              <dt>{key}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="block-body" style={{ color: 'var(--text-3)', maxWidth: 'none', marginTop: 6 }}>
          (no scalar fields)
        </p>
      )}
      {hasMore && (
        <>
          <button
            type="button"
            className="btn btn-ghost"
            style={{ marginTop: 6, padding: '4px 10px', fontSize: 12 }}
            onClick={() => setShowRaw((v) => !v)}
          >
            {showRaw ? 'Hide full JSON' : 'Show full JSON'}
          </button>
          {showRaw && <pre className="trace-raw">{JSON.stringify(obj, null, 2)}</pre>}
        </>
      )}
    </div>
  )
}

export function TraceCard({ trace, highlighted }: TraceCardProps) {
  return (
    <div id={`trace-${trace.id}`} className={`card${highlighted ? ' highlighted' : ''}`}>
      <div className="card-top">
        <div>
          <div className="card-eyebrow">
            step {trace.step_index} · window {trace.window_index} · {new Date(trace.called_at).toLocaleString()}
          </div>
          <h3 className="card-title" style={{ fontSize: 16, fontFamily: 'var(--font-mono)' }}>
            {trace.tool_name}
          </h3>
        </div>
        {trace.is_error && <span className="pill rejected">Error</span>}
      </div>

      <TraceObjectBlock label="Arguments" obj={trace.arguments} />
      <TraceObjectBlock label="Result" obj={trace.result} />
    </div>
  )
}

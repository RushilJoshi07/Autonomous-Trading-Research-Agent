import type { components } from '../api/schema.gen'

type Charter = components['schemas']['Charter']

interface CharterSummaryProps {
  charter: Charter
  blocked: boolean
}

/**
 * Pure display of a parsed+resolved Charter -- no buttons, no phase logic.
 * Renders exactly the fields scripts/set_charter.py prints today (universe
 * filter, the screening disclosure, resolved universe, hypothesis
 * families, timeframe/history, scoring preference). Used by MandatePage in
 * both the reviewing and confirmed states, which is the actual reuse that
 * justifies pulling it out of the page component.
 */
export function CharterSummary({ charter, blocked }: CharterSummaryProps) {
  const { parsed, resolved_universe, screening_as_of, screening_group_size } = charter

  return (
    <div className="card">
      <div className="card-top">
        <div>
          <div className="card-eyebrow">Parsed charter</div>
          <h2 className="card-title">
            {parsed.universe.sector ?? 'Any sector'}
            {parsed.universe.industry ? ` / ${parsed.universe.industry}` : ''}
          </h2>
        </div>
        {blocked && <span className="pill rejected">Blocked</span>}
      </div>

      <div className="stat-row">
        <div className="stat-tile">
          <div className="k">Universe filter</div>
          <div className="v" style={{ fontSize: 18 }}>{parsed.universe.metric}</div>
          <div className="sub">cut = {parsed.universe.cut}</div>
        </div>
        <div className="stat-tile">
          <div className="k">Screening</div>
          <div className="v">{screening_group_size}{'→'}{resolved_universe.length}</div>
          <div className="sub">matched {'→'} survived the {parsed.universe.cut} cut, as of {screening_as_of}</div>
        </div>
        <div className="stat-tile">
          <div className="k">Timeframe</div>
          <div className="v" style={{ fontSize: 18 }}>{parsed.timeframe}</div>
          <div className="sub">history from {parsed.history_start ?? 'all available'}</div>
        </div>
        <div className="stat-tile">
          <div className="k">Scoring preference</div>
          <div className="v" style={{ fontSize: 18 }}>{parsed.scoring_preference}</div>
        </div>
      </div>

      <p className="block-body" style={{ color: 'var(--text-2)', marginTop: 18 }}>
        <strong style={{ color: 'var(--text-1)' }}>Resolved universe.</strong>{' '}
        {resolved_universe.length > 0 ? resolved_universe.join(', ') : 'none'}
      </p>

      <div className="pill-row" style={{ marginTop: 12 }}>
        {parsed.hypothesis_families.map((family) => (
          <span key={family} className="pill inconclusive">
            {family}
          </span>
        ))}
      </div>
    </div>
  )
}

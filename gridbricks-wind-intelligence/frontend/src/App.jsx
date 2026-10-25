import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from 'recharts';
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, Check, ChevronDown,
  CircleHelp, Clock3, Cloud, Database, Download, ExternalLink, Gauge, RefreshCw,
  ShieldCheck, Wind, Zap,
} from 'lucide-react';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const DEFAULT_MONTH = '2026-01';
const DEFAULT_DATE = '2026-01-15';
const fmt = (value, digits = 0) => value === null || value === undefined || !Number.isFinite(Number(value))
  ? '—'
  : Number(value).toLocaleString('en-GB', { maximumFractionDigits: digits, minimumFractionDigits: digits });
const metricFormat = (value, digits = 0) => value === null || value === undefined ? '—' : fmt(value, digits);
const getTime = (iso) => iso ? new Date(iso).toLocaleTimeString('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit' }) : '—';
const getDateLabel = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
const getSelectedDateLabel = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const addDaysUtc = (iso, days) => { const value = new Date(`${iso}T00:00:00Z`); value.setUTCDate(value.getUTCDate() + days); return value.toISOString().slice(0, 10); }

function MetricCard({ icon: Icon, title, value, unit, detail, tone = 'teal', footerIcon: FooterIcon }) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <div className="metric-top">
        <div className="metric-icon"><Icon size={18} strokeWidth={1.8} /></div>
        <span className="metric-title">{title}</span>
        <CircleHelp size={15} className="muted-help" aria-label={detail} title={detail} />
      </div>
      <div className="metric-value">{value}<span>{unit}</span></div>
      <div className="metric-foot">{FooterIcon && <FooterIcon size={13} />}<span>{detail}</span></div>
    </article>
  );
}

function ChartTooltip({ active, payload, label, formatter }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="tooltip-label">{formatter ? formatter(label) : label}</div>
      {payload.map((item) => (
        <div className="tooltip-row" key={item.dataKey}>
          <span className="tooltip-dot" style={{ background: item.color }} />
          <span>{item.name}</span>
          <strong>{fmt(item.value, 0)} MW</strong>
        </div>
      ))}
    </div>
  );
}

function EmptyChart({ message }) {
  return <div className="empty-chart"><Activity size={22} /><span>{message || 'No matching observations available for this view.'}</span></div>;
}

export default function App() {
  const [selectedDate, setSelectedDate] = useState(DEFAULT_DATE);
  const [cutoff, setCutoff] = useState('10:00');
  const [mode, setMode] = useState('live');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [liveWarning, setLiveWarning] = useState('');
  const [showMethodology, setShowMethodology] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);

  const loadData = useCallback(async (requestedMode = mode, automaticFallback = true) => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({
        month: DEFAULT_MONTH,
        selected_date: selectedDate,
        cutoff_time: cutoff,
        mode: requestedMode,
      });
      const response = await fetch(`${API_BASE}/api/dashboard?${params.toString()}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof payload.detail === 'string' ? payload.detail : payload.detail?.reason || payload.detail?.message || 'The API request failed.';
        throw new Error(detail);
      }
      setData(payload);
      setMode(requestedMode);
      setLastUpdated(new Date());
      if (requestedMode === 'live') setLiveWarning('');
      else if (liveWarning && requestedMode === 'demo') setLiveWarning(liveWarning);
      return true;
    } catch (err) {
      if (requestedMode === 'live' && automaticFallback) {
        setLiveWarning(`Live Elexon data could not be loaded (${err.message}). Showing synthetic sample data for the UI preview; these values are not market data.`);
        return await loadData('demo', false);
      }
      setError(err.message || 'Unable to load dashboard data.');
      return false;
    } finally {
      setLoading(false);
    }
  // intentionally includes liveWarning only for the successful demo branch; no automatic fetch loop.
  }, [selectedDate, cutoff, mode, liveWarning]);

  useEffect(() => {
    loadData('live', true);
    // Initial load plus date/cutoff changes are handled here; mode changes are explicit actions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDate, cutoff]);

  const monthlyData = useMemo(() => (data?.daily_series || []).map((item) => ({
    ...item,
    day: item.date.slice(-2),
    date_label: getDateLabel(item.date),
  })), [data]);

  const intradayData = useMemo(() => (data?.intraday || []).map((item) => ({
    ...item,
    time_label: getTime(item.timestamp),
  })), [data]);

  const handleDateChange = (value) => {
    if (value >= '2026-01-01' && value <= '2026-01-31') setSelectedDate(value);
  };

  const downloadCsv = () => {
    if (!data?.intraday?.length) return;
    const columns = ['target_hour_utc', 'hour_period', 'forecast_mw', 'actual_hourly_average_mw', 'actual_half_hour_1_mw', 'actual_half_hour_2_mw', 'error_mw', 'abs_error_mw', 'forecast_publish_time_utc'];
    const rows = data.intraday.map((r) => [r.timestamp, r.hour_period, r.forecast_mw, r.actual_mw, r.actual_half_hour_values_mw?.[0], r.actual_half_hour_values_mw?.[1], r.error_mw, r.abs_error_mw, r.publish_time]);
    const csv = [columns, ...rows].map((row) => row.map((v) => v === null || v === undefined ? '' : `"${String(v).replaceAll('"', '""')}"`).join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `wind-intelligence-decision-${selectedDate}-delivery-${selectedDeliveryDate}-cutoff-${cutoff.replace(':', '')}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const metrics = data?.metrics || {};
  const coverage = data?.coverage || {};
  const coveragePct = Math.max(0, Math.min(100, Number(coverage.matched_pct_of_month || 0)));
  const isDemo = data?.source_mode === 'demo';
  const selectedDeliveryDate = data?.selected_delivery_date || addDaysUtc(selectedDate, 1);
  const hasIncompleteCoverage = Boolean(data && Number(coverage.matched_intervals || 0) < Number(coverage.expected_intervals || 0));
  const sourceStatusLabel = isDemo ? 'SYNTHETIC DEMO DATA' : data?.source_mode === 'live' ? (hasIncompleteCoverage ? 'ELEXON LIVE · PARTIAL COVERAGE' : 'ELEXON LIVE SOURCE') : 'LOADING SOURCE';

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-symbol"><Wind size={22} strokeWidth={2.3} /></div>
          <div><div className="brand-name">grid<span>bricks</span></div><div className="brand-product">ENERGY INTELLIGENCE</div></div>
        </div>
        <div className="topbar-right">
          <div className="market-chip"><span className="status-dot" />GB NATIONAL SYSTEM</div>
          <div className="topbar-divider" />
          <button className="profile-button" aria-label="Analyst workspace">ZA<span>Analyst workspace</span><ChevronDown size={14} /></button>
        </div>
      </header>

      <main className="page-content">
        <section className="intro-row">
          <div>
            <div className="eyebrow"><span className="eyebrow-line" />DAY-AHEAD PROCUREMENT WORKSPACE</div>
            <h1>Wind forecast intelligence<span className="heading-period">.</span></h1>
            <p className="intro-copy">Review the wind generation forecast available at decision time, then quantify the gap against actual outturn.</p>
          </div>
          <div className="replay-badge"><div className="replay-icon"><Clock3 size={16} /></div><div><strong>Historical replay</strong><span>Decision dates 01—31 January 2026 · UTC</span></div></div>
        </section>

        <section className="control-panel" aria-label="Dashboard controls">
          <div className="control-field date-field">
            <label htmlFor="decision-date">DECISION DATE</label>
            <div className="field-input"><input id="decision-date" type="date" min="2026-01-01" max="2026-01-31" value={selectedDate} onChange={(e) => handleDateChange(e.target.value)} /><ChevronDown className="input-chevron" size={14} /></div>
          </div>
          <div className="control-separator" />
          <div className="control-field cutoff-field">
            <label htmlFor="cutoff-time">DECISION SNAPSHOT CUTOFF</label>
            <div className="field-input time-input"><input id="cutoff-time" type="time" value={cutoff} onChange={(e) => setCutoff(e.target.value || '10:00')} /><span className="utc-tag">UTC</span></div>
          </div>
          <div className="control-context"><div className="context-icon"><ShieldCheck size={17} /></div><div><strong>Next-day delivery replay</strong><span>Cutoff applies on the selected decision date</span></div></div>
          <div className="control-actions">
            <button className="refresh-button" onClick={() => loadData(mode, false)} disabled={loading}><RefreshCw size={15} className={loading ? 'spin' : ''} />{loading ? 'Refreshing' : 'Refresh data'}</button>
          </div>
        </section>

        {liveWarning && <div className={`notice ${isDemo ? 'notice-demo' : ''}`}><AlertTriangle size={17} /><span>{liveWarning}</span><button onClick={() => loadData('live', false)} disabled={loading}>Retry live data</button></div>}
        {error && <div className="notice notice-error"><AlertTriangle size={17} /><span>{error}</span><button onClick={() => loadData('demo', false)}>Load demo data</button></div>}
        {hasIncompleteCoverage && <div className="notice"><AlertTriangle size={17} /><span><strong>Incomplete forecast coverage.</strong> {fmt(coverage.matched_intervals)} of {fmt(coverage.expected_intervals)} expected hourly intervals are matched ({coveragePct.toFixed(1)}%). Accuracy metrics use matched pairs only and should not be described as a complete-month hourly evaluation. Each actual hour is the average of its two half-hour FUELHH observations.</span></div>}

        <div className="section-heading-row">
          <div><h2>Forecast performance</h2><p>Monthly accuracy metrics · hourly forecasts vs hourly-average actuals</p></div>
          <div className={`data-source-pill ${isDemo ? 'source-demo' : hasIncompleteCoverage ? 'source-incomplete' : ''}`}><span className="source-indicator" />{sourceStatusLabel}<span className="source-divider" /><Database size={13} />{fmt(coverage.matched_intervals)} matched periods</div>
        </div>

        <section className="metrics-grid" aria-label="Forecast error metrics">
          <MetricCard icon={Gauge} title="Mean absolute error" value={metricFormat(metrics.mae_mw)} unit=" MW" tone="teal" detail="Average absolute forecast miss" footerIcon={Activity} />
          <MetricCard icon={Activity} title="Root mean squared error" value={metricFormat(metrics.rmse_mw)} unit=" MW" tone="blue" detail="Penalises larger misses" footerIcon={Activity} />
          <MetricCard icon={Zap} title="Weighted absolute error" value={metricFormat(metrics.wape_pct, 1)} unit="%" tone="violet" detail="Absolute error ÷ actual generation" footerIcon={Activity} />
          <MetricCard icon={metrics.bias_mw < 0 ? ArrowDownRight : ArrowUpRight} title="Forecast bias" value={metrics.bias_mw === null || metrics.bias_mw === undefined ? '—' : `${metrics.bias_mw > 0 ? '+' : ''}${fmt(metrics.bias_mw)}`} unit=" MW" tone={metrics.bias_mw > 0 ? 'amber' : 'green'} detail={metrics.bias_mw > 0 ? 'Over-forecast on average' : metrics.bias_mw < 0 ? 'Under-forecast on average' : 'Mean forecast minus actual'} footerIcon={metrics.bias_mw > 0 ? ArrowUpRight : ArrowDownRight} />
        </section>

        <section className="coverage-strip">
          <div className="coverage-label"><div className="coverage-icon"><Check size={16} /></div><div><strong>January decision-window coverage</strong><span>Hourly forecast + both half-hour actuals present</span></div></div>
          <div className="coverage-bar-area"><div className="coverage-track"><div className="coverage-fill" style={{ width: `${coveragePct}%` }} /></div><div className="coverage-caption"><span>{coveragePct.toFixed(1)}% matched of expected periods</span><span>{fmt(coverage.matched_intervals)} / {fmt(coverage.expected_intervals)}</span></div></div>
          <div className="coverage-divider" />
          <div className="coverage-meta"><span>SNAPSHOT TIME</span><strong>{cutoff} <i>UTC · D−1</i></strong></div>
        </section>

        <div className="charts-grid">
          <section className="panel monthly-panel">
            <div className="panel-header">
              <div><div className="panel-kicker">MONTHLY OVERVIEW</div><h3>Wind generation profile</h3><p>Delivery dates Jan 2—Feb 1 · for January decisions</p></div>
              <div className="chart-legend"><span><i className="legend-line legend-forecast" />Forecast</span><span><i className="legend-line legend-actual" />Actual</span></div>
            </div>
            <div className="chart-wrap monthly-chart">
              {monthlyData.length ? <ResponsiveContainer width="100%" height="100%"><LineChart data={monthlyData} margin={{ top: 12, right: 8, bottom: 0, left: -16 }}>
                <CartesianGrid strokeDasharray="3 5" vertical={false} stroke="#e9edf3" />
                <XAxis dataKey="day" tickLine={false} axisLine={false} tick={{ fill: '#8290a3', fontSize: 11 }} interval={4} tickMargin={12} />
                <YAxis tickLine={false} axisLine={false} tick={{ fill: '#8290a3', fontSize: 11 }} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} width={42} />
                <Tooltip content={<ChartTooltip formatter={(value) => monthlyData.find((item) => item.day === String(value).padStart(2, '0'))?.date_label || value} />} />
                <Line type="monotone" dataKey="forecast_avg_mw" name="Forecast" stroke="#14a9a0" strokeWidth={2.3} dot={false} activeDot={{ r: 4, strokeWidth: 0 }} connectNulls />
                <Line type="monotone" dataKey="actual_avg_mw" name="Actual" stroke="#7c8ca3" strokeWidth={2} strokeDasharray="5 4" dot={false} activeDot={{ r: 4, strokeWidth: 0 }} connectNulls />
              </LineChart></ResponsiveContainer> : <EmptyChart />}
            </div>
            <div className="chart-footnote"><span className="footnote-dot" />Select a January decision date above to inspect the following delivery day. Daily averages include matched observations only.</div>
          </section>

          <section className="panel intraday-panel">
            <div className="panel-header">
              <div><div className="panel-kicker">NEXT-DAY DELIVERY DETAIL</div><h3>Forecast vs. actual</h3><p>{getSelectedDateLabel(selectedDeliveryDate)}</p></div>
              <span className="period-count">{intradayData.length} intervals</span>
            </div>
            <div className="chart-wrap intraday-chart">
              {intradayData.length ? <ResponsiveContainer width="100%" height="100%"><AreaChart data={intradayData} margin={{ top: 12, right: 6, bottom: 0, left: -15 }}>
                <defs><linearGradient id="forecastFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#19b9ab" stopOpacity={0.17} /><stop offset="95%" stopColor="#19b9ab" stopOpacity={0.01} /></linearGradient></defs>
                <CartesianGrid strokeDasharray="3 5" vertical={false} stroke="#e9edf3" />
                <XAxis dataKey="time_label" tickLine={false} axisLine={false} tick={{ fill: '#8290a3', fontSize: 10 }} interval={7} tickMargin={12} />
                <YAxis tickLine={false} axisLine={false} tick={{ fill: '#8290a3', fontSize: 10 }} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} width={40} />
                <Tooltip content={<ChartTooltip formatter={(value) => `${value} UTC`} />} />
                <Area type="monotone" dataKey="forecast_mw" name="Forecast" stroke="#14a9a0" strokeWidth={2.2} fill="url(#forecastFill)" dot={false} activeDot={{ r: 4, strokeWidth: 0 }} connectNulls />
                <Line type="monotone" dataKey="actual_mw" name="Actual" stroke="#7c8ca3" strokeWidth={1.8} strokeDasharray="4 3" dot={false} activeDot={{ r: 4, strokeWidth: 0 }} connectNulls />
              </AreaChart></ResponsiveContainer> : <EmptyChart message="No intraday values for this delivery date." />}
            </div>
            <div className="intraday-summary">
              <div><span>HOURLY DAY MAE</span><strong>{fmt(data?.daily_series?.find((d) => d.date === selectedDeliveryDate)?.mae_mw)} <small>MW</small></strong></div>
              <div><span>SELECTED FORECAST PUBLISHED</span><strong className="publish-time">{getTime(data?.latest_selected_publish_time)} <small>UTC</small></strong></div>
              <div><span>SNAPSHOT RULE</span><strong>D−1 · {cutoff} <small>UTC</small></strong></div>
            </div>
          </section>
        </div>

        <section className="panel table-panel">
          <div className="panel-header table-header">
            <div><div className="panel-kicker">HOURLY TARGET AUDIT</div><h3>Decision-time forecast ledger</h3><p>Delivery: {getSelectedDateLabel(selectedDeliveryDate)} · decision: {getSelectedDateLabel(selectedDate)} · UTC</p></div>
            <button className="export-button" onClick={downloadCsv} disabled={!data?.intraday?.length}><Download size={15} /> Export CSV</button>
          </div>
          <div className="table-overflow">
            <table>
              <thead><tr><th>TARGET HOUR (UTC)</th><th className="align-right">HOUR #</th><th className="align-right">FORECAST</th><th className="align-right">ACTUAL</th><th className="align-right">ERROR</th><th className="align-right">ABS. ERROR</th><th>FORECAST PUBLICATION</th></tr></thead>
              <tbody>
                {intradayData.map((row, index) => <tr key={row.timestamp}>
                  <td><span className="time-cell">{getTime(row.timestamp)}<small>UTC</small></span></td>
                  <td className="align-right period-cell">{Number(row.hour_period) >= 1 ? row.hour_period : index + 1}</td>
                  <td className="align-right number-cell">{fmt(row.forecast_mw)}</td>
                  <td className="align-right number-cell">{fmt(row.actual_mw)}</td>
                  <td className={`align-right error-cell ${row.error_mw > 0 ? 'error-positive' : row.error_mw < 0 ? 'error-negative' : ''}`}>{row.error_mw === null ? '—' : `${row.error_mw > 0 ? '+' : ''}${fmt(row.error_mw)}`}</td>
                  <td className="align-right number-cell">{fmt(row.abs_error_mw)}</td>
                  <td className="published-cell">{row.publish_time ? `${new Date(row.publish_time).toLocaleDateString('en-GB', { timeZone: 'UTC', day: '2-digit', month: 'short' })} · ${getTime(row.publish_time)}` : <span className="missing-tag">No eligible forecast</span>}</td>
                </tr>)}
                {!intradayData.length && <tr><td colSpan="7" className="no-rows">No rows to display. Refresh live data or load demo data.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="table-footer"><span>ERROR = HOURLY FORECAST − HOURLY-AVERAGE ACTUAL · Positive values indicate wind over-forecast</span><span>{intradayData.filter((r) => r.forecast_mw !== null && r.actual_mw !== null).length} matched hours in view</span></div>
        </section>

        <section className="insight-row">
          <div className="insight-mark"><Zap size={19} /></div>
          <div className="insight-copy"><strong>What this tells the procurement team</strong><p>Forecast error quantifies uncertainty in expected wind generation at decision time. Under-forecasting can leave more wind generation than expected; over-forecasting can leave less. This dataset alone does not establish total demand, net system position, or the volume of additional power to buy.</p></div>
          <button className="method-button" onClick={() => setShowMethodology((v) => !v)}>{showMethodology ? 'Hide methodology' : 'View methodology'}<ChevronDown size={15} className={showMethodology ? 'rotate' : ''} /></button>
        </section>

        {showMethodology && <section className="panel methodology-panel">
          <div className="method-grid">
            <div><span className="method-number">01</span><h4>Data provenance</h4><p>FUELHH rows are filtered to <code>fuelType = WIND</code>. WINDFOR supplies forecast revisions with both target <code>startTime</code> and issue <code>publishTime</code>.</p><a href="https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream" target="_blank" rel="noreferrer">FUELHH documentation <ExternalLink size={12} /></a><a href="https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream" target="_blank" rel="noreferrer">WINDFOR documentation <ExternalLink size={12} /></a></div>
            <div><span className="method-number">02</span><h4>As-of selection</h4><p>For every target hour, choose the latest forecast revision published no later than the day-before cutoff. Average the two half-hour FUELHH actual observations to form the matching hourly actual. Later revisions are excluded to avoid look-ahead bias.</p></div>
            <div><span className="method-number">03</span><h4>Error metrics</h4><p>MAE and RMSE are in MW. WAPE is the sum of absolute errors divided by total actual generation. Bias is mean forecast minus actual. Metrics use matched pairs only.</p></div>
            <div><span className="method-number">04</span><h4>Data limitations</h4><p>Missing observations remain missing rather than being imputed. Synthetic data is clearly labelled. This pilot does not ingest demand, solar, thermal generation, prices, or market trades.</p></div>
          </div>
        </section>}

        <footer className="footer">
          <div className="footer-brand"><div className="footer-mark"><Wind size={16} /></div><span>GRID BRICKS <i>·</i> WIND INTELLIGENCE</span></div>
          <div className="footer-meta"><span><Database size={13} />{data?.source_name || 'Awaiting data source'}</span><span><Clock3 size={13} />{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}` : 'Not yet updated'}</span><span className="footer-version">PILOT V1.1.0</span></div>
        </footer>
      </main>
    </div>
  );
}

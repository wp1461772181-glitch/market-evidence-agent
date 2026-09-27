import type { PriceCandle, RefreshReport, Snapshot } from "./types";

export type TargetWindow = { start: string; end: string };

export function CandlestickChart({ candles, selected, report, target, activeDate, onInspect, cutoffDate, targetEndDate, symbol }: {
  candles: PriceCandle[];
  selected?: Snapshot;
  report?: RefreshReport;
  target?: TargetWindow;
  activeDate: string;
  onInspect: (date: string) => void;
  cutoffDate?: string;
  targetEndDate?: string;
  symbol?: string;
}) {
  const width = 1000;
  const height = 320;
  const margin = { top: 22, right: 58, bottom: 34, left: 8 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const low = Math.min(...candles.map((candle) => candle.low));
  const high = Math.max(...candles.map((candle) => candle.high));
  const padding = Math.max((high - low) * 0.08, 0.5);
  const min = low - padding;
  const max = high + padding;
  const originalDate = report?.original_snapshot.feature_trading_date;
  const revisedDate = report?.revised_snapshot.feature_trading_date;
  const markerIndex = (date: string | undefined) => date ? candles.findIndex((candle) => candle.trading_date === date) : -1;
  const targetStart = markerIndex(target?.start);
  const targetEnd = markerIndex(target?.end);
  const futureTargetSlots = target && targetStart < 0 && target.start > candles.at(-1)!.trading_date ? 20 : 0;
  const slotCount = candles.length + futureTargetSlots;
  const x = (index: number) => margin.left + ((index + 0.5) / slotCount) * plotWidth;
  const y = (value: number) => margin.top + ((max - value) / (max - min)) * plotHeight;
  const bodyWidth = Math.max(2, Math.min(10, (plotWidth / slotCount) * 0.58));
  const originalIndex = markerIndex(originalDate);
  const revisedIndex = report ? markerIndex(revisedDate) : -1;
  const cutoffIndex = report ? -1 : markerIndex(cutoffDate ?? selected?.feature_trading_date);
  const v3TargetIndex = markerIndex(targetEndDate);
  const futureV3Target = Boolean(targetEndDate && v3TargetIndex < 0 && targetEndDate > candles.at(-1)!.trading_date);
  const actualStart = targetEnd >= 0 ? targetEnd + 1 : -1;
  const ticks = [max, (max + min) / 2, min];
  const dateTicks = [0, Math.floor((candles.length - 1) / 2), candles.length - 1];

  return <figure className="candlestick-figure">
    <svg className="candlestick-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="candle-chart-title candle-chart-description">
      <title id="candle-chart-title">{symbol ?? selected?.symbol ?? "股票"} 历史日线蜡烛图</title>
      <desc id="candle-chart-description">使用鼠标停留或键盘聚焦蜡烛图中的日线，读取当天开盘、最高、最低和收盘价格。{target ? "阴影区域是保存的 20 个交易日滚动目标窗口。" : ""}{targetEndDate ? ` 固定目标日为 ${targetEndDate}。` : ""}</desc>
      {ticks.map((tick) => <g key={tick}><line x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} className="chart-grid" /><text x={width - margin.right + 9} y={y(tick) + 4} className="chart-price-label">{formatPrice(tick)}</text></g>)}
      {targetStart >= 0 && targetEnd >= targetStart && <g className="target-window"><rect x={x(targetStart) - bodyWidth} y={margin.top} width={x(targetEnd) - x(targetStart) + bodyWidth * 2} height={plotHeight} /><text x={x(targetStart) + 5} y={margin.top + 14}>20-session target</text></g>}
      {futureTargetSlots > 0 && <g className="target-window target-window-future"><rect x={x(candles.length - 1) + bodyWidth} y={margin.top} width={width - margin.right - (x(candles.length - 1) + bodyWidth)} height={plotHeight} /><text x={x(candles.length - 1) + bodyWidth + 5} y={margin.top + 14}>20-session target / awaiting results</text></g>}
      {actualStart >= 0 && actualStart < candles.length && <g className="actual-region"><line x1={x(actualStart) - bodyWidth} x2={x(actualStart) - bodyWidth} y1={margin.top} y2={margin.top + plotHeight} /><text x={x(actualStart) + 5} y={height - margin.bottom - 8}>目标后实际行情</text></g>}
      {candles.map((candle, index) => {
        const up = candle.close >= candle.open;
        const bodyY = y(Math.max(candle.open, candle.close));
        const bodyHeight = Math.max(1.5, Math.abs(y(candle.open) - y(candle.close)));
        const afterTarget = actualStart >= 0 && index >= actualStart;
        return <g key={candle.trading_date} className={`candle ${up ? "is-up" : "is-down"} ${afterTarget ? "is-actual" : ""} ${activeDate === candle.trading_date ? "is-active" : ""}`} tabIndex={0} role="img" aria-label={candleLabel(candle)} onFocus={() => onInspect(candle.trading_date)} onMouseEnter={() => onInspect(candle.trading_date)}>
          <title>{candleLabel(candle)}</title><line x1={x(index)} x2={x(index)} y1={y(candle.high)} y2={y(candle.low)} /><rect x={x(index) - bodyWidth / 2} y={bodyY} width={bodyWidth} height={bodyHeight} />
        </g>;
      })}
      {originalIndex >= 0 && <Marker x={x(originalIndex)} label="原始截止" />}
      {revisedIndex >= 0 && revisedIndex !== originalIndex && <Marker x={x(revisedIndex)} label="修订截止" tone="revised" />}
      {cutoffIndex >= 0 && <Marker x={x(cutoffIndex)} label={cutoffDate ? "决策行情截止" : "版本截止"} />}
      {v3TargetIndex >= 0 && <Marker x={x(v3TargetIndex)} label="V3 固定目标日" tone="revised" />}
      {futureV3Target && <g className="snapshot-marker revised"><line x1={width - margin.right} x2={width - margin.right} y1={19} y2={287} /><text x={width - margin.right - 6} y={18} textAnchor="end">固定目标 {targetEndDate}</text></g>}
      {dateTicks.map((index) => <text key={index} x={x(index)} y={height - 10} textAnchor="middle" className="chart-date-label">{shortDate(candles[index]!.trading_date)}</text>)}
    </svg>
    <figcaption><span><i className="legend-up" />收高于开</span><span><i className="legend-down" />收低于开</span>{target && <span><i className="legend-window" />保存的目标窗口</span>}{targetEndDate && <span>V3 固定目标日：{targetEndDate}</span>}</figcaption>
  </figure>;
}

function Marker({ x, label, tone }: { x: number; label: string; tone?: "revised" }) { return <g className={`snapshot-marker ${tone ?? ""}`}><line x1={x} x2={x} y1={19} y2={287} /><text x={x + 5} y={18}>{label}</text></g>; }
function formatPrice(value: number) { return `$${value.toFixed(2)}`; }
function candleLabel(candle: PriceCandle) { return `${candle.trading_date}，开 ${formatPrice(candle.open)}，高 ${formatPrice(candle.high)}，低 ${formatPrice(candle.low)}，收 ${formatPrice(candle.close)}`; }
function shortDate(value: string) { return `${value.slice(5, 7)}/${value.slice(8, 10)}`; }

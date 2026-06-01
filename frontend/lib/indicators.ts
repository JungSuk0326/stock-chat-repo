/**
 * Pure TA indicator computations.
 *
 * Inputs are arrays of numbers ordered oldest → newest (matching the
 * candle order from PriceChart). Outputs are aligned arrays of the same
 * length where the warm-up positions are `null` (not `NaN`) so the chart
 * library skips them cleanly.
 *
 * Same formulas as `backend/app/services/llm_context.py`:
 *   - SMA: trailing arithmetic mean
 *   - EMA: alpha = 2 / (period + 1), seeded with the SMA of the first period
 *   - RSI: Wilder's smoothing (alpha = 1/period), seed = avg of first 14 diffs
 *   - Bollinger: SMA(period) ± k * sample-stddev(period)
 *
 * If we ever diverge from the backend computation, fix HERE first since the
 * chart is what the user looks at most often — backend can re-borrow.
 */

export function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (period <= 0 || values.length < period) return out;
  let running = 0;
  for (let i = 0; i < period; i += 1) running += values[i];
  out[period - 1] = running / period;
  for (let i = period; i < values.length; i += 1) {
    running += values[i] - values[i - period];
    out[i] = running / period;
  }
  return out;
}

export function ema(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (period <= 0 || values.length < period) return out;
  const alpha = 2 / (period + 1);
  // Seed with SMA of the first `period` values, just like most charting libs.
  let seed = 0;
  for (let i = 0; i < period; i += 1) seed += values[i];
  seed /= period;
  out[period - 1] = seed;
  for (let i = period; i < values.length; i += 1) {
    const prev = out[i - 1] as number;
    out[i] = prev + alpha * (values[i] - prev);
  }
  return out;
}

export interface BollingerOutput {
  middle: (number | null)[];
  upper: (number | null)[];
  lower: (number | null)[];
}

export function bollinger(
  values: number[],
  period = 20,
  stdDevMultiplier = 2,
): BollingerOutput {
  const middle = sma(values, period);
  const upper: (number | null)[] = new Array(values.length).fill(null);
  const lower: (number | null)[] = new Array(values.length).fill(null);
  if (values.length < period) return { middle, upper, lower };

  for (let i = period - 1; i < values.length; i += 1) {
    const m = middle[i] as number;
    let sumSq = 0;
    for (let j = i - period + 1; j <= i; j += 1) {
      const diff = values[j] - m;
      sumSq += diff * diff;
    }
    // Sample std-dev (N-1) matches TradingView's default; switch to N if
    // you ever want population stddev.
    const sd = Math.sqrt(sumSq / (period - 1));
    upper[i] = m + stdDevMultiplier * sd;
    lower[i] = m - stdDevMultiplier * sd;
  }
  return { middle, upper, lower };
}

export function rsi(values: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length <= period) return out;

  let gainSum = 0;
  let lossSum = 0;
  for (let i = 1; i <= period; i += 1) {
    const diff = values[i] - values[i - 1];
    if (diff >= 0) gainSum += diff;
    else lossSum += -diff;
  }
  let avgGain = gainSum / period;
  let avgLoss = lossSum / period;
  out[period] = _rsiValue(avgGain, avgLoss);

  for (let i = period + 1; i < values.length; i += 1) {
    const diff = values[i] - values[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = _rsiValue(avgGain, avgLoss);
  }
  return out;
}

function _rsiValue(avgGain: number, avgLoss: number): number {
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

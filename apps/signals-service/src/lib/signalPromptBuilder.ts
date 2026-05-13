import type {
  NewsResponse,
  TechnicalsResponse,
  FiiDiiResponse,
  OptionsResponse,
  MacroResponse,
} from './types.js';

interface SignalBundle {
  symbol: string;
  exchange: string;
  companyName?: string;
  sector?: string;
  news: NewsResponse | null;
  technicals: TechnicalsResponse | null;
  fiiDii: FiiDiiResponse | null;
  options: OptionsResponse | null;
  macro: MacroResponse | null;
}

export function buildSignalPrompt(bundle: SignalBundle): string {
  const sections: string[] = [];

  sections.push(`=== STOCK UNDER ANALYSIS ===
Symbol: ${bundle.symbol} (${bundle.exchange})
Company: ${bundle.companyName ?? 'Unknown'}
Sector: ${bundle.sector ?? 'Unknown'}`);

  if (bundle.technicals && !bundle.technicals.error) {
    const t = bundle.technicals;
    sections.push(`=== TECHNICAL INDICATORS ===
Current Price: ${t.currentPrice}
RSI: ${t.rsi.value} — ${t.rsi.interpretation}
MACD Histogram: ${t.macd.value} — ${t.macd.interpretation}
Bollinger Band Position: ${t.bollingerBands.value} — ${t.bollingerBands.interpretation}
EMA20: ${t.ema20.value} — ${t.ema20.interpretation}
EMA50: ${t.ema50.value} — ${t.ema50.interpretation}
Volume Ratio: ${t.volumeRatio.value} — ${t.volumeRatio.interpretation}`);
  } else {
    sections.push(`=== TECHNICAL INDICATORS ===
Status: UNAVAILABLE${bundle.technicals?.error ? ` (${bundle.technicals.error})` : ''}`);
  }

  if (bundle.fiiDii && !bundle.fiiDii.error) {
    const f = bundle.fiiDii;
    sections.push(`=== FII/DII FLOWS (${f.date}) ===
FII: Buy ₹${f.fii.buy}Cr | Sell ₹${f.fii.sell}Cr | Net ₹${f.fii.net}Cr (${f.fii.net >= 0 ? 'NET BUYING' : 'NET SELLING'})
DII: Buy ₹${f.dii.buy}Cr | Sell ₹${f.dii.sell}Cr | Net ₹${f.dii.net}Cr (${f.dii.net >= 0 ? 'NET BUYING' : 'NET SELLING'})`);
  } else {
    sections.push(`=== FII/DII FLOWS ===
Status: UNAVAILABLE`);
  }

  if (bundle.options && !bundle.options.error) {
    const o = bundle.options;
    sections.push(`=== OPTIONS DATA ===
Put-Call Ratio (PCR): ${o.pcr} — ${o.interpretation}
Max Pain: ${o.maxPain}`);
  } else {
    sections.push(`=== OPTIONS DATA ===
Status: UNAVAILABLE`);
  }

  if (bundle.macro && !bundle.macro.error) {
    const macroLines = bundle.macro.indicators
      .map((m) => `${m.label}: ${m.value} (${m.changePercent >= 0 ? '+' : ''}${m.changePercent.toFixed(2)}%) — ${m.context}`)
      .join('\n');
    sections.push(`=== MACRO INDICATORS ===\n${macroLines}`);
  } else {
    sections.push(`=== MACRO INDICATORS ===
Status: UNAVAILABLE`);
  }

  if (bundle.news && bundle.news.articles.length > 0) {
    const headlines = bundle.news.articles
      .slice(0, 10)
      .map((a, i) => `${i + 1}. [${a.source}] ${a.title}`)
      .join('\n');
    sections.push(`=== RECENT NEWS HEADLINES ===\n${headlines}`);
  } else {
    sections.push(`=== RECENT NEWS HEADLINES ===
Status: UNAVAILABLE`);
  }

  sections.push(`=== INSTRUCTION ===
Analyze all available signals above. Note which sources are unavailable and reduce confidence accordingly. Return ONLY the JSON object as specified.`);

  return sections.join('\n\n');
}

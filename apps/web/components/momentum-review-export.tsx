'use client';

import { useState } from 'react';

// Each mounted chart registers its canvas snapshot without exposing chart internals.
export const reviewChartSnapshots = new WeakMap<Element, () => Promise<string>>();

export function ReviewExport({ disabled = false }: { disabled?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(
    'Exports the selected review, available charts and evidence.',
  );
  async function exportReview() {
    const source = document.getElementById('momentum-detail');
    if (!source || busy) return;
    setBusy(true);
    let frame: HTMLIFrameElement | null = null;
    const cleanup = () => {
      frame?.remove();
      setBusy(false);
    };
    try {
      const charts = Array.from(source.querySelectorAll('[data-review-chart]'));
      if (!charts.length || charts.some((chart) => !reviewChartSnapshots.has(chart)))
        throw new Error('Wait for both charts to finish loading.');
      setStatus('Preparing complete charts…');
      const copy = source.cloneNode(true) as HTMLElement;
      // Native details fragmentation can leave blank continuation pages in Chrome.
      Array.from(copy.querySelectorAll('details'))
        .reverse()
        .forEach((detail) => {
          const section = document.createElement('section');
          section.className = detail.className;
          const summary = detail.querySelector(':scope > summary');
          if (summary) {
            const heading = document.createElement('h3');
            heading.textContent = summary.textContent;
            section.appendChild(heading);
            summary.remove();
          }
          while (detail.firstChild) section.appendChild(detail.firstChild);
          detail.replaceWith(section);
        });
      copy
        .querySelectorAll('.review-export,button,[data-chart-controls],.review-keyboard-help')
        .forEach((el) => el.remove());
      const copies = copy.querySelectorAll('[data-review-chart]');
      const snapshots = await Promise.all(
        charts.map((chart) => reviewChartSnapshots.get(chart)!()),
      );
      charts.forEach((chart, index) => {
        const image = document.createElement('img');
        image.src = snapshots[index]!;
        const target = copies[index]!;
        const heading = target.previousElementSibling;
        if (heading?.querySelector('h3')) target.prepend(heading);
        image.alt = chart.getAttribute('aria-label') ?? 'Complete candle chart';
        image.className = 'pdf-chart-image';
        target.querySelector('[data-chart-plot]')!.replaceChildren(image);
        target.removeAttribute('tabindex');
      });
      frame = document.createElement('iframe');
      frame.title = 'Printable momentum review';
      frame.setAttribute('aria-hidden', 'true');
      frame.style.cssText = 'position:fixed;left:-20000px;top:0;width:1060px;height:800px;border:0';
      document.body.appendChild(frame);
      const doc = frame.contentDocument!;
      doc.open();
      doc.write(
        '<!doctype html><html><head><title>Momentum review</title></head><body></body></html>',
      );
      doc.close();
      const styles = Array.from(document.querySelectorAll('style,link[rel="stylesheet"]'));
      const ready: Promise<void>[] = [];
      styles.forEach((style) => {
        const clone = style.cloneNode(true) as HTMLElement;
        if (clone instanceof HTMLLinkElement)
          ready.push(
            new Promise((resolve, reject) => {
              clone.onload = () => resolve();
              clone.onerror = () => reject(new Error('Print styles could not load.'));
            }),
          );
        doc.head.appendChild(clone);
      });
      const printStyle = doc.createElement('style');
      printStyle.textContent = `@page{size:A4 landscape;margin:12mm}html,body{background:#fff;color:#17212a}body{margin:0;font:12px/1.4 sans-serif}.review-detail{border:0;padding:0;box-shadow:none}.review-disclosure{border:0}.review-detail>.review-disclosure{break-before:page}.review-disclosure>summary{font-size:17px;font-weight:bold;list-style:none}.review-disclosure>summary::marker{content:''}details,section,div{overflow:visible!important;max-height:none!important}thead{display:table-header-group}tr{break-inside:avoid}h2,h3{break-after:avoid}p{color:#384550!important}[data-review-chart]{break-before:page;break-inside:avoid;border:0;border-radius:0;overflow:visible!important;background:white;color:#17212a}[data-review-chart] :not(img){color:#17212a!important}[data-chart-plot]{height:auto!important;min-height:0!important}.pdf-chart-image{display:block;width:100%;height:auto;max-height:145mm;object-fit:contain}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}`;
      doc.head.appendChild(printStyle);
      const context = doc.createElement('p');
      context.textContent = `Full-session charts · Times in ${charts[0]?.getAttribute('data-chart-timezone')} · Prices in ${charts[0]?.getAttribute('data-chart-currency')}`;
      copy.prepend(context);
      doc.title = source.getAttribute('aria-label') ?? 'Momentum review';
      doc.body.appendChild(copy);
      await Promise.all([...ready, ...Array.from(doc.images).map((image) => image.decode())]);
      await doc.fonts.ready;
      frame.contentWindow!.addEventListener(
        'afterprint',
        () => {
          setStatus('Print dialog closed. Ready to export again.');
          cleanup();
        },
        { once: true },
      );
      setStatus('Ready. Choose Save as PDF in the print dialog.');
      frame.contentWindow!.focus();
      frame.contentWindow!.print();
    } catch (error) {
      cleanup();
      setStatus(
        error instanceof Error ? error.message : 'Could not prepare PDF. Please try again.',
      );
    }
  }
  return (
    <div className="review-export">
      <button
        type="button"
        disabled={disabled || busy}
        onClick={() => {
          void exportReview();
        }}
      >
        {busy ? 'Preparing PDF…' : 'Export review PDF'}
      </button>
      <span role="status">{status}</span>
    </div>
  );
}

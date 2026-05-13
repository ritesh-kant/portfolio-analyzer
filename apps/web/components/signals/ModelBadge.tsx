'use client';

import type { SettingsResponse } from '../../lib/signals-api';

interface ModelBadgeProps {
  settings: SettingsResponse | null;
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  gemini: 'Google',
  kimi: 'NVIDIA / Kimi',
  ollama: 'Local / Ollama',
};

export function ModelBadge({ settings }: ModelBadgeProps) {
  if (!settings) {
    return (
      <div className="flex items-center gap-2 rounded-full bg-panel px-3 py-1.5 text-xs text-ink/50 shadow-card">
        Loading model…
      </div>
    );
  }

  const providerLabel = PROVIDER_LABELS[settings.provider] ?? settings.provider;

  return (
    <div className="flex items-center gap-2 rounded-full bg-panel px-3 py-1.5 text-xs shadow-card">
      <span className="text-ink/60">Model:</span>
      <span className="font-semibold text-ink">{settings.model}</span>
      <span className="text-ink/50">({providerLabel})</span>
      {settings.isLocal && (
        <span className="rounded-full bg-green-100 px-2 py-0.5 text-[10px] font-semibold text-green-700">
          Local
        </span>
      )}
    </div>
  );
}

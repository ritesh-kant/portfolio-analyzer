import { config } from './config.js';

const ALLOWED_ORIGINS = new Set([
  config.allowedOrigin,
  'https://portfolio-analyzer-web-alpha.vercel.app',
  'https://raptguru.in',
  'https://www.raptguru.in',
]);

export function resolveOrigin(requestOrigin?: string): string {
  if (requestOrigin && ALLOWED_ORIGINS.has(requestOrigin)) return requestOrigin;
  return config.allowedOrigin;
}

function corsHeaders(requestOrigin?: string) {
  return {
    'access-control-allow-origin': resolveOrigin(requestOrigin),
    'access-control-allow-methods': 'GET,POST,PUT,DELETE,OPTIONS',
    'access-control-allow-headers': 'content-type,authorization',
    vary: 'Origin',
  };
}

export function json(statusCode: number, body: unknown, requestOrigin?: string) {
  return {
    statusCode,
    headers: { 'content-type': 'application/json', ...corsHeaders(requestOrigin) },
    body: JSON.stringify(body),
  };
}

export function options(requestOrigin?: string) {
  return { statusCode: 204, headers: corsHeaders(requestOrigin), body: '' };
}

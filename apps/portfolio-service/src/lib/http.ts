import { config } from './config.js';

// ALLOWED_ORIGIN is baked in at deploy time, but the Lambda env on AWS still
// carries the old value in some stages — keep the production web origins as
// unconditional fallbacks so a stale env var can never strip CORS headers.
const EXTRA_ORIGINS = [
  'https://portfolio-analyzer-web-alpha.vercel.app',
  'https://raptguru.in',
  'https://www.raptguru.in',
];

function allowedOrigins(): Set<string> {
  return new Set([config.allowedOrigin, ...EXTRA_ORIGINS]);
}

export function resolveOrigin(requestOrigin?: string): string {
  if (requestOrigin && allowedOrigins().has(requestOrigin)) return requestOrigin;
  // Fall back to the configured origin when it is a real web origin;
  // otherwise default to the production site so a stale localhost env var on
  // Lambda can never produce a CORS mismatch. Never reflect arbitrary origins.
  if (config.allowedOrigin?.startsWith('https://')) return config.allowedOrigin;
  return 'https://www.raptguru.in';
}

function corsHeaders(requestOrigin?: string) {
  const origin = resolveOrigin(requestOrigin);
  return {
    // Canonical casing for API Gateway / browsers; keep lowercase copies for
    // any case-sensitive proxy in between.
    'Access-Control-Allow-Origin': origin,
    'access-control-allow-origin': origin,
    'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
    'access-control-allow-methods': 'GET,POST,PUT,DELETE,OPTIONS',
    'Access-Control-Allow-Headers': 'content-type,authorization',
    'access-control-allow-headers': 'content-type,authorization',
    Vary: 'Origin',
    vary: 'Origin',
  };
}

export interface ApiResponse {
  statusCode: number;
  headers: Record<string, string>;
  body: string;
}

export function json(statusCode: number, body: unknown, requestOrigin?: string): ApiResponse {
  return {
    statusCode,
    headers: { 'content-type': 'application/json', ...corsHeaders(requestOrigin) },
    body: JSON.stringify(body),
  };
}

export function options(requestOrigin?: string): ApiResponse {
  return { statusCode: 204, headers: corsHeaders(requestOrigin), body: '' };
}

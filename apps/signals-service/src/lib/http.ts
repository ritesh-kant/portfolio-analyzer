const ALLOWED_ORIGIN = process.env.ALLOWED_ORIGIN ?? 'http://localhost:3000';

const CORS_HEADERS = {
  'access-control-allow-origin': ALLOWED_ORIGIN,
  'access-control-allow-methods': 'GET,POST,PUT,DELETE,OPTIONS',
  'access-control-allow-headers': 'content-type,authorization',
  vary: 'Origin',
};

export function json(statusCode: number, body: unknown) {
  return {
    statusCode,
    headers: {
      'content-type': 'application/json',
      ...CORS_HEADERS,
    },
    body: JSON.stringify(body),
  };
}

export function options() {
  return { statusCode: 204, headers: CORS_HEADERS, body: '' };
}

import { jwtVerify, type JWTPayload } from 'jose';

export interface AuthPayload extends JWTPayload {
  email: string;
  name?: string;
}

export async function verifySessionToken(token: string): Promise<AuthPayload> {
  const secret = process.env.AUTH_SECRET;
  if (!secret) throw new Error('AUTH_SECRET env var not set');
  const key = new TextEncoder().encode(secret);
  const { payload } = await jwtVerify(token, key);
  if (!payload['email']) throw new Error('Token missing email claim');
  return payload as AuthPayload;
}

export function extractBearerToken(authHeader: string | undefined): string | null {
  if (!authHeader?.startsWith('Bearer ')) return null;
  return authHeader.slice(7);
}

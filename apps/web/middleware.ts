import NextAuth, { type NextAuthResult } from 'next-auth';
import { authConfig } from './auth.config';
import type { NextMiddleware, NextRequest } from 'next/server';
import { NextResponse } from 'next/server';

const nextAuth: NextAuthResult = NextAuth(authConfig);

const authMiddleware = nextAuth.auth as NextMiddleware;

export default function middleware(req: NextRequest) {
  if (process.env.DISABLE_AUTH === 'true') {
    return NextResponse.next();
  }
  return authMiddleware(req, {} as Parameters<NextMiddleware>[1]);
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|login|api/auth).*)'],
};

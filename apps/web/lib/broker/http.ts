import { NextResponse } from 'next/server';
import { BrokerError } from './types';

export function fail(e: unknown): NextResponse {
  if (e instanceof BrokerError) return NextResponse.json({ error: e.message }, { status: e.status });
  return NextResponse.json({ error: e instanceof Error ? e.message : 'broker error' }, { status: 500 });
}

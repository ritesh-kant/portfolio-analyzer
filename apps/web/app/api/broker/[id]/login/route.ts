import { randomBytes } from 'node:crypto';
import { NextResponse } from 'next/server';
import { fail } from '../../../../../lib/broker/http';
import { requireUser } from '../../../../../lib/broker/guards';
import { brokerById } from '../../../../../lib/broker/registry';
import { BrokerError } from '../../../../../lib/broker/types';

export const dynamic = 'force-dynamic';

export async function GET(_req: Request, { params }: { params: { id: string } }) {
  try {
    await requireUser();
    const broker = brokerById(params.id);
    if (!broker.configured()) throw new BrokerError(`${broker.name} API key and secret are not set on the server`, 400);
    const state = randomBytes(16).toString('hex');
    const res = NextResponse.redirect(broker.loginUrl(state));
    res.cookies.set(`broker_state_${broker.id}`, state, { httpOnly: true, sameSite: 'lax', maxAge: 600, path: '/' });
    return res;
  } catch (e) {
    return fail(e);
  }
}

import { NextResponse } from 'next/server';
import { fail } from '../../../../../lib/broker/http';
import { requireUser } from '../../../../../lib/broker/guards';
import { brokerById } from '../../../../../lib/broker/registry';
import { saveToken } from '../../../../../lib/broker/session-store';
import { BrokerError } from '../../../../../lib/broker/types';

export const dynamic = 'force-dynamic';

/** The broker sends the browser back here after login; we trade the one-time code for today's access token. */
export async function GET(req: Request, { params }: { params: { id: string } }) {
  try {
    await requireUser();
    const broker = brokerById(params.id);
    const url = new URL(req.url);
    const cookie = req.headers
      .get('cookie')
      ?.split(/;\s*/)
      .find((c) => c.startsWith(`broker_state_${broker.id}=`))
      ?.split('=')[1];
    if (!cookie || cookie !== url.searchParams.get('state')) throw new BrokerError('Login state mismatch', 400);
    saveToken(broker.id, await broker.exchange(url.searchParams));
    const res = NextResponse.redirect(new URL('/chart', req.url));
    res.cookies.delete(`broker_state_${broker.id}`);
    return res;
  } catch (e) {
    return fail(e);
  }
}

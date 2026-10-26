import { NextResponse } from 'next/server';
import { fail } from '../../../../../lib/broker/http';
import { requireUser } from '../../../../../lib/broker/guards';
import { brokerById } from '../../../../../lib/broker/registry';
import { clearToken, getToken } from '../../../../../lib/broker/session-store';
import { BrokerError } from '../../../../../lib/broker/types';

export const dynamic = 'force-dynamic';

/** Orders and positions as the broker reports them: the chart draws these and keeps no copy of its own. */
export async function GET(_req: Request, { params }: { params: { id: string } }) {
  try {
    await requireUser();
    const broker = brokerById(params.id);
    const token = getToken(broker.id);
    if (!token) throw new BrokerError('Not connected', 401);
    const api = broker.connect(token);
    try {
      const [user, orders, positions] = await Promise.all([api.profile(), api.orders(), api.positions()]);
      return NextResponse.json({ user: user.user, orders, positions });
    } catch (e) {
      if (e instanceof BrokerError && e.status === 401) clearToken(broker.id);
      throw e;
    }
  } catch (e) {
    return fail(e);
  }
}

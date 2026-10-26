import { NextResponse } from 'next/server';
import { fail } from '../../../../../lib/broker/http';
import { requireSameOrigin, requireUser, validateOrder } from '../../../../../lib/broker/guards';
import { brokerById } from '../../../../../lib/broker/registry';
import { getToken } from '../../../../../lib/broker/session-store';
import { BrokerError, type PlaceOrder } from '../../../../../lib/broker/types';

export const dynamic = 'force-dynamic';

function connected(id: string) {
  const broker = brokerById(id);
  const token = getToken(broker.id);
  if (!token) throw new BrokerError('Not connected', 401);
  return broker.connect(token);
}

/**
 * Plain orders only. Stop-loss / take-profit brackets are not sent: neither broker's REST API takes them atomically
 * with the entry, so the ticket places them as separate orders you can see and cancel.
 */
export async function POST(req: Request, { params }: { params: { id: string } }) {
  try {
    requireSameOrigin(req);
    await requireUser();
    const api = connected(params.id);
    const order = validateOrder((await req.json()) as PlaceOrder);
    return NextResponse.json(await api.place(order));
  } catch (e) {
    return fail(e);
  }
}

export async function DELETE(req: Request, { params }: { params: { id: string } }) {
  try {
    requireSameOrigin(req);
    await requireUser();
    const orderId = new URL(req.url).searchParams.get('orderId');
    if (!orderId || !/^[A-Za-z0-9_-]{4,40}$/.test(orderId)) throw new BrokerError('bad orderId', 400);
    await connected(params.id).cancel(orderId);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return fail(e);
  }
}

import type { MedusaRequest, MedusaResponse } from "@medusajs/framework/http"
import {
  capturePaymentWorkflow,
  completeCartWorkflow,
  createPaymentCollectionForCartWorkflow,
  createPaymentSessionsWorkflow,
} from "@medusajs/medusa/core-flows"
import { ContainerRegistrationKeys } from "@medusajs/framework/utils"

type Body = {
  provider_payment_id?: string
  provider?: string
}

const asNumber = (value: unknown): number => {
  if (typeof value === "number") return value
  if (typeof value === "string") return Number(value || 0)
  if (value && typeof value === "object") {
    const v = value as any
    if (typeof v.numeric === "number") return v.numeric
    if (typeof v.value === "number" || typeof v.value === "string") return Number(v.value || 0)
    if (v.raw && (typeof v.raw.value === "number" || typeof v.raw.value === "string")) return Number(v.raw.value || 0)
  }
  return 0
}

async function fetchOrderWithPayments(req: MedusaRequest, orderId: string) {
  const query = req.scope.resolve(ContainerRegistrationKeys.QUERY)
  const { data: orders } = await query.graph({
    entity: "order",
    fields: [
      "id",
      "metadata",
      "items.id",
      "items.metadata",
      "payment_status",
      "fulfillment_status",
      "payment_collections.id",
      "payment_collections.payments.id",
      "payment_collections.payments.amount",
      "payment_collections.payments.captured_amount",
      "payment_collections.payments.captures.id",
      "payment_collections.payments.captures.amount",
      "payment_collections.payments.refunds.id",
      "payment_collections.payments.refunds.amount",
    ],
    filters: { id: orderId },
  })
  return (orders[0] || null) as any
}

/**
 * Mirror an already-verified ClubOS external payment into Medusa.
 *
 * ClubOS remains payment truth. Medusa's system payment exists only so the Commerce
 * Engine has a captured payment that can later be refunded/reconciled. This endpoint
 * never talks to WeChat/Alipay and must only be called after ClubOS verified payment.
 */
async function ensureCaptured(req: MedusaRequest, orderId: string) {
  let order = await fetchOrderWithPayments(req, orderId)
  if (!order) throw new Error("order not found after cart completion")

  const payments = (order.payment_collections || []).flatMap((pc: any) => pc.payments || [])
  if (!payments.length) throw new Error("Medusa order has no payment to capture")

  for (const payment of payments) {
    const amount = asNumber(payment.amount)
    const captured = asNumber(payment.captured_amount)
    const remaining = Math.max(0, amount - captured)
    if (remaining > 0.000001) {
      await capturePaymentWorkflow(req.scope).run({
        input: {
          payment_id: payment.id,
          amount: remaining,
        },
      })
    }
  }

  order = await fetchOrderWithPayments(req, orderId)
  return order
}

export const POST = async (req: MedusaRequest<Body>, res: MedusaResponse) => {
  const cartId = req.params.id
  const providerId = process.env.MEDUSA_INTERNAL_PAYMENT_PROVIDER_ID || "pp_system_default"
  const query = req.scope.resolve(ContainerRegistrationKeys.QUERY)

  const { data: carts } = await query.graph({
    entity: "cart",
    fields: [
      "id",
      "total",
      "metadata",
      "completed_at",
      "order.id",
      "order.metadata",
      "payment_collection.id",
      "payment_collection.payment_sessions.id",
      "payment_collection.payment_sessions.provider_id",
    ],
    filters: { id: cartId },
  })
  const cart = carts[0] as any
  if (!cart) return res.status(404).json({ message: "cart not found" })

  // Idempotent retries must also finish the capture mirror. A previous request may
  // have created the order but crashed before capture completed.
  if (cart.order?.id) {
    const order = await ensureCaptured(req, cart.order.id)
    return res.status(200).json({ type: "order", order, idempotent: true })
  }

  let paymentCollectionId = cart.payment_collection?.id as string | undefined
  if (!paymentCollectionId) {
    const { result } = await createPaymentCollectionForCartWorkflow(req.scope).run({
      input: { cart_id: cartId },
    })
    paymentCollectionId = result.id
  }

  const sessions = cart.payment_collection?.payment_sessions || []
  if (!sessions.some((session: any) => session.provider_id === providerId)) {
    await createPaymentSessionsWorkflow(req.scope).run({
      input: {
        payment_collection_id: paymentCollectionId!,
        provider_id: providerId,
        data: {
          clubos_verified: true,
          clubos_provider: req.body?.provider || "external",
          clubos_provider_payment_id: req.body?.provider_payment_id || null,
        },
      },
    })
  }

  const { result } = await completeCartWorkflow(req.scope).run({ input: { id: cartId } })
  const orderId = (result as any)?.id
  if (!orderId) {
    return res.status(409).json({ message: "Medusa completeCartWorkflow did not return an order" })
  }

  const order = await ensureCaptured(req, orderId)
  return res.status(200).json({ type: "order", order: order || result, idempotent: false })
}

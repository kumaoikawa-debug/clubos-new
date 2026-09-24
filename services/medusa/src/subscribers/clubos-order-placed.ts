import type { SubscriberArgs, SubscriberConfig } from "@medusajs/framework"
import { ContainerRegistrationKeys } from "@medusajs/framework/utils"

/**
 * Reconciliation callback only. Payment truth comes from ClubOS v0.15 payment
 * providers. order.placed must never award points or mark a checkout paid by itself.
 */
export default async function clubosOrderPlacedHandler({
  event: { data },
  container,
}: SubscriberArgs<{ id: string }>) {
  const logger = container.resolve(ContainerRegistrationKeys.LOGGER)
  const query = container.resolve(ContainerRegistrationKeys.QUERY)
  const callbackBase = process.env.CLUBOS_CALLBACK_URL
  if (!callbackBase) return

  try {
    const { data: orders } = await query.graph({
      entity: "order",
      fields: ["id", "cart_id", "metadata", "items.id", "items.metadata"],
      filters: { id: data.id },
    })
    const order = orders[0] as any
    if (!order) return

    const fromOrder = order.metadata?.clubos_checkout_intent_id
    const fromItem = (order.items || []).map((x: any) => x.metadata?.clubos_checkout_intent_id).find(Boolean)
    const checkoutId = fromOrder || fromItem
    if (!checkoutId) return

    const secret = process.env.CLUBOS_COMMERCE_WEBHOOK_SECRET || ""
    const response = await fetch(`${callbackBase.replace(/\/$/, "")}/api/commerce/events/order-placed`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(secret ? { "x-clubos-commerce-secret": secret } : {}),
      },
      body: JSON.stringify({
        eventKey: `medusa:order.placed:${order.id}`,
        provider: "medusa",
        checkoutId,
        orderId: order.id,
        cartId: order.cart_id || null,
      }),
    })
    if (!response.ok) {
      logger.error(`ClubOS reconciliation callback failed for order ${order.id}: ${response.status} ${await response.text()}`)
    }
  } catch (error) {
    logger.error(`ClubOS order.placed subscriber error: ${error}`)
  }
}

export const config: SubscriberConfig = { event: "order.placed" }

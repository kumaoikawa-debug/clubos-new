import { createWorkflow, transform, WorkflowResponse } from "@medusajs/framework/workflows-sdk"
import { acquireLockStep, addToCartWorkflow, releaseLockStep, useQueryGraphStep } from "@medusajs/medusa/core-flows"

export type ClubOSCustomCartItem = {
  variant_id: string
  quantity: number
  unit_price: number
  metadata?: Record<string, unknown> | null
}

type Input = {
  cart_id: string
  items: ClubOSCustomCartItem[]
}

// ClubOS calculates the payable cash amount. Medusa remains the cart/order/payment engine.
// The domain meaning of the price difference (Club Points vs Gear Points vs platform subsidy)
// stays in ClubOS and is carried through metadata for audit/callback purposes.
export const addClubOSItemsToCartWorkflow = createWorkflow(
  "add-clubos-items-to-cart",
  ({ cart_id, items }: Input) => {
    const itemInput = transform({ items }, ({ items }) =>
      items.map((item) => ({
        variant_id: item.variant_id,
        quantity: item.quantity,
        unit_price: item.unit_price,
        metadata: item.metadata || {},
      }))
    )

    acquireLockStep({ key: cart_id, timeout: 2, ttl: 10 })

    addToCartWorkflow.runAsStep({
      input: {
        cart_id,
        items: itemInput,
      },
    })

    const { data: carts } = useQueryGraphStep({
      entity: "cart",
      filters: { id: cart_id },
      fields: ["id", "currency_code", "total", "subtotal", "items.*"],
      options: { throwIfKeyNotFound: true },
    }).config({ name: "refetch-clubos-cart" })

    releaseLockStep({ key: cart_id })

    return new WorkflowResponse({ cart: carts[0] })
  }
)

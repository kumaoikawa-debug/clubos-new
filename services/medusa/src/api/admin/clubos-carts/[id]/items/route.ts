import type { MedusaRequest, MedusaResponse } from "@medusajs/framework/http"
import { addClubOSItemsToCartWorkflow, ClubOSCustomCartItem } from "../../../../../workflows/add-clubos-items-to-cart"

type Body = { items: ClubOSCustomCartItem[] }

export const POST = async (req: MedusaRequest<Body>, res: MedusaResponse) => {
  const items = req.body?.items || []
  if (!items.length) {
    return res.status(400).json({ message: "items required" })
  }
  for (const item of items) {
    if (!item.variant_id || !Number.isFinite(Number(item.quantity)) || Number(item.quantity) < 1 || !Number.isFinite(Number(item.unit_price)) || Number(item.unit_price) < 0) {
      return res.status(400).json({ message: "invalid ClubOS cart item" })
    }
  }
  const { result } = await addClubOSItemsToCartWorkflow(req.scope).run({
    input: { cart_id: req.params.id, items },
  })
  return res.status(200).json({ cart: result.cart })
}

import { authenticate, defineMiddlewares } from "@medusajs/framework/http"

export default defineMiddlewares({
  routes: [
    {
      matcher: "/admin/clubos-paid-carts*",
      middlewares: [authenticate("user", ["session", "bearer", "api-key"])],
    },
    {
      matcher: "/admin/clubos-carts*",
      middlewares: [authenticate("user", ["session", "bearer", "api-key"])],
    },
  ],
})

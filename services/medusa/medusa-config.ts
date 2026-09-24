import { defineConfig, loadEnv } from "@medusajs/framework/utils"

loadEnv(process.env.NODE_ENV || "development", process.cwd())

/**
 * ClubOS NEW v0.16: Medusa is intentionally a pure Commerce Engine.
 * Activity booking, Club/Gear Points, commission, AI Credits and subsidy rules
 * stay in the ClubOS domain service and are never modeled as Medusa modules.
 */
module.exports = defineConfig({
  projectConfig: {
    databaseUrl: process.env.DATABASE_URL,
    redisUrl: process.env.REDIS_URL,
    http: {
      storeCors: process.env.STORE_CORS || "http://localhost:8000",
      adminCors: process.env.ADMIN_CORS || "http://localhost:9000,http://localhost:8000",
      authCors: process.env.AUTH_CORS || "http://localhost:9000,http://localhost:8000",
      jwtSecret: process.env.JWT_SECRET || "change-me",
      cookieSecret: process.env.COOKIE_SECRET || "change-me",
    },
  },
})

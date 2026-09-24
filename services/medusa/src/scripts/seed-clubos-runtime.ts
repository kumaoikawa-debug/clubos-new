import fs from "node:fs"
import path from "node:path"
import type { ExecArgs } from "@medusajs/framework/types"
import { ContainerRegistrationKeys, Modules } from "@medusajs/framework/utils"
import { createWorkflow, transform, WorkflowResponse } from "@medusajs/framework/workflows-sdk"
import {
  createApiKeysWorkflow,
  createRegionsWorkflow,
  createSalesChannelsWorkflow,
  createShippingOptionsWorkflow,
  createShippingProfilesWorkflow,
  createStockLocationsWorkflow,
  createTaxRegionsWorkflow,
  linkSalesChannelsToApiKeyWorkflow,
  linkSalesChannelsToStockLocationWorkflow,
  updateStoresStep,
  updateStoresWorkflow,
} from "@medusajs/medusa/core-flows"

const updateStoreCurrencies = createWorkflow(
  "clubos-update-store-currencies",
  (input: {
    supported_currencies: { currency_code: string; is_default?: boolean }[]
    store_id: string
  }) => {
    const normalized = transform({ input }, (data) => ({
      selector: { id: data.input.store_id },
      update: {
        supported_currencies: data.input.supported_currencies.map((currency) => ({
          currency_code: currency.currency_code,
          is_default: currency.is_default ?? false,
        })),
      },
    }))
    const stores = updateStoresStep(normalized)
    return new WorkflowResponse(stores)
  }
)

export default async function seedClubOSRuntime({ container }: ExecArgs) {
  const logger = container.resolve(ContainerRegistrationKeys.LOGGER)
  const link = container.resolve(ContainerRegistrationKeys.LINK)
  const query = container.resolve(ContainerRegistrationKeys.QUERY)
  const fulfillment = container.resolve(Modules.FULFILLMENT)
  const salesChannelService = container.resolve(Modules.SALES_CHANNEL)
  const storeService = container.resolve(Modules.STORE)

  const [store] = await storeService.listStores()
  if (!store) throw new Error("Medusa default store is missing")

  let channels = await salesChannelService.listSalesChannels({ name: "ClubOS Gear" })
  if (!channels.length) {
    const { result } = await createSalesChannelsWorkflow(container).run({
      input: { salesChannelsData: [{ name: "ClubOS Gear" }] },
    })
    channels = result
  }
  const salesChannel = channels[0]

  await updateStoreCurrencies(container).run({
    input: {
      store_id: store.id,
      supported_currencies: [{ currency_code: "cny", is_default: true }],
    },
  })
  await updateStoresWorkflow(container).run({
    input: {
      selector: { id: store.id },
      update: { default_sales_channel_id: salesChannel.id },
    },
  })

  const { result: regionResult } = await createRegionsWorkflow(container).run({
    input: {
      regions: [
        {
          name: "ClubOS China",
          currency_code: "cny",
          countries: ["cn"],
          payment_providers: ["pp_system_default"],
        },
      ],
    },
  })
  const region = regionResult[0]

  await createTaxRegionsWorkflow(container).run({
    input: [{ country_code: "cn", provider_id: "tp_system" }],
  })

  const { result: stockResult } = await createStockLocationsWorkflow(container).run({
    input: {
      locations: [
        {
          name: "ClubOS Platform Warehouse",
          address: {
            city: "Chengdu",
            province: "Sichuan",
            country_code: "CN",
            address_1: "ClubOS Runtime Warehouse",
          },
        },
      ],
    },
  })
  const stockLocation = stockResult[0]

  await updateStoresWorkflow(container).run({
    input: {
      selector: { id: store.id },
      update: { default_location_id: stockLocation.id },
    },
  })

  await link.create({
    [Modules.STOCK_LOCATION]: { stock_location_id: stockLocation.id },
    [Modules.FULFILLMENT]: { fulfillment_provider_id: "manual_manual" },
  })

  let [shippingProfile] = await fulfillment.listShippingProfiles({ type: "default" })
  if (!shippingProfile) {
    const { result } = await createShippingProfilesWorkflow(container).run({
      input: { data: [{ name: "ClubOS Default Shipping", type: "default" }] },
    })
    shippingProfile = result[0]
  }

  const fulfillmentSet = await fulfillment.createFulfillmentSets({
    name: "ClubOS China delivery",
    type: "shipping",
    service_zones: [
      {
        name: "China",
        geo_zones: [{ country_code: "cn", type: "country" }],
      },
    ],
  })

  await link.create({
    [Modules.STOCK_LOCATION]: { stock_location_id: stockLocation.id },
    [Modules.FULFILLMENT]: { fulfillment_set_id: fulfillmentSet.id },
  })

  const { result: shippingOptions } = await createShippingOptionsWorkflow(container).run({
    input: [
      {
        name: "ClubOS Standard Shipping",
        price_type: "flat",
        provider_id: "manual_manual",
        service_zone_id: fulfillmentSet.service_zones[0].id,
        shipping_profile_id: shippingProfile.id,
        type: {
          label: "Standard",
          description: "ClubOS platform fulfillment",
          code: "clubos-standard",
        },
        prices: [
          { currency_code: "cny", amount: 0 },
          { region_id: region.id, amount: 0 },
        ],
        rules: [
          { attribute: "enabled_in_store", value: "true", operator: "eq" },
          { attribute: "is_return", value: "false", operator: "eq" },
        ],
      },
    ],
  })
  const shippingOption = (shippingOptions as any[])[0]

  await linkSalesChannelsToStockLocationWorkflow(container).run({
    input: { id: stockLocation.id, add: [salesChannel.id] },
  })

  const { result: apiKeyResult } = await createApiKeysWorkflow(container).run({
    input: {
      api_keys: [
        {
          title: "ClubOS Runtime Store",
          type: "publishable",
          created_by: "",
        },
      ],
    },
  })
  let publishableKey: any = (apiKeyResult as any[])[0]
  await linkSalesChannelsToApiKeyWorkflow(container).run({
    input: { id: publishableKey.id, add: [salesChannel.id] },
  })

  if (!publishableKey.token) {
    const { data } = await query.graph({
      entity: "api_key",
      fields: ["id", "title", "type", "token"],
      filters: { id: publishableKey.id },
    })
    publishableKey = data[0] || publishableKey
  }
  if (!publishableKey.token) throw new Error("Publishable API key token was not returned")

  const runtime = {
    medusa_version: "2.21.1",
    region_id: region.id,
    sales_channel_id: salesChannel.id,
    shipping_profile_id: shippingProfile.id,
    stock_location_id: stockLocation.id,
    shipping_option_id: shippingOption.id,
    publishable_key_id: publishableKey.id,
    publishable_key_token: publishableKey.token,
    payment_provider_id: "pp_system_default",
  }

  const outputPath = path.resolve(process.cwd(), ".clubos-runtime.json")
  fs.writeFileSync(outputPath, JSON.stringify(runtime, null, 2) + "\n")
  logger.info(`ClubOS v0.16 runtime seed written to ${outputPath}`)
  logger.info(JSON.stringify({ ...runtime, publishable_key_token: "[redacted]" }))
}

# Grocery adapter feasibility — 2026-10-09

Scope: research only, no adapters implemented, no login, basket or order operations. Hermes connection renamed from marketplace_local to shopping; Python package and project path unchanged. `hermes mcp test shopping` connected and discovered search, get_card, get_cards. Configuration was read back after removing the old entry.

## VARUS

Candidate is technically promising. Read-only live investigation observed:
- https://varus.ua/stores and the site's GET /api/catalog/vue_storefront_catalog_2/tms_shop/_search request expose shop identifiers, addresses and coordinates. The exact query parameters must be captured again before implementation; this note is not a complete API specification.
- https://varus.ua/maslo-zlagoda-solodkovershkove-ekstra-82-180-g (SKU 2656669) contains __INITIAL_STATE__ and shop_availability_list in its HTML. Entries include shop id, address/coordinates, product_in_stock, price and special_price. Browser shop availability agrees with available/unavailable statuses. Observed prices differ between shops.
- https://varus.ua/search?q=молоко is publicly usable.
- storeView.storeId is a language/storefront identifier, NOT the physical shop id; retain shop.id and tms_id separately.

This supports an SSR adapter for per-shop availability and approximate pricing. The full address-to-nearest-shop path was NOT exercised; geocoding, filtering inactive/service locations and search/card matching need separate tests. A boolean product_in_stock is not proof that the requested quantity exists. Do not interpret generic stock.qty as physical-shop inventory.

Parent independently retrieved the public product page and stores page through web retrieval. The product page advertises shop availability; that retrieval alone does not expose all structured shop fields observed by the investigation.

## Lenta

https://lenta.com/ and https://lenta.com/info/shops/ failed in the investigation's direct browser/HTTP access with connection closure/TLS EOF. Cause is unknown; do not call this proof of a captcha, geo-block or mandatory authentication.

Parent web retrieval did obtain the public shops page and catalog navigation at https://lenta.com/info/shops/. Thus the site is not universally inaccessible, but no usable shop-specific inventory/price data path was verified. Full adapter feasibility remains unconfirmed. A successful indexed/retrieved page is not proof the runtime transport can reach a shop-specific backend.

## Proposed contract for a later implementation

- Explicit provider and currency (RUB/UAH), configured address or coordinates, selected physical shop id and address.
- Define nearest as straight-line distance unless route distance is deliberately added; filter to eligible operating retail shops.
- Separate physical shop availability from delivery-fulfillment availability.
- Return availability as available/unavailable/unknown, with source and observation time; never map errors to unavailable.
- Separate regular/promotional/conditional prices; estimate totals from requested units or weight and disclose missing products and uncertain quantities.
- Keep existing Yandex operations compatible. Current price_rub schema needs currency-aware evolution before adding VARUS.

Recommendation: implement and verify VARUS first; re-probe Lenta's runtime access and store selection before promising its adapter. No implementation was authorized in this phase.

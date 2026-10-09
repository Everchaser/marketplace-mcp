# VARUS verification — 2026-10-09

Реальные read-only проверки без корзин, login, покупок, synthetic live fallback, изменения Hermes config/VPN/DNS. Для location использованы **sample координаты публичного VARUS**, вул. Панікахи 15, Дніпро; это не адрес пользователя. Независимый reviewer approval не заявляется.

## Артефакты и воспроизведение

Project: `/home/everchaser/Projects/marketplace-mcp`.

Wheel: `dist/marketplace_mcp-0.1.0-py3-none-any.whl`; sdist: `dist/marketplace_mcp-0.1.0.tar.gz`.

Все captures/logs/snapshot находятся в `/home/everchaser/.hermes/cache/scratch/varus-implementation/` (далее `$A`). Scratch может быть очищен, это не постоянный production storage.

- `$A/before/` — snapshot до изменения кода, без `.venv`, caches и dist.
- `$A/01-settings-red.log` … `$A/16-shop-identity-red.log` и соответствующие `*-green.log` — реальные RED/GREEN выходы по вертикальным slices, GREEN запускает всю suite.
- `$A/source-stdio.json`, `$A/source-stdio.log` — актуальная source VARUS проверка, `passed=true`, ровно 3 tools, 8 calls.
- `$A/wheel-stdio.json`, `$A/wheel-stdio.log` — отдельный clean-wheel stdio, `passed=true`, 8 calls.
- `$A/yandex-stdio.json`, `$A/yandex-stdio.log` и `$A/wheel-yandex-stdio.json`, `$A/wheel-yandex-stdio.log` — оба Yandex regression smokes, `passed=true`, 5 calls каждый.
- `$A/public-address-stdio.json` — настоящий public-address opt-in: **`geocoding_ambiguous`**, не успешная адресная привязка и не отсутствие товара. Никакого произвольного первого результата не выбрано.
- `$A/extra-stdio.log` — дополнительная живая weighted-card проверка unavailable и исключения из totals. `$A/weighted-card.json` — сырой SSR product banana; `special_price=0`, `discount_percent=0`, `price.special=""`, `unit=kg`, `quantityText="за 1 кг"`.
- `$A/card.html/json`, `$A/search.html/json`, `$A/stores.html/json`, `$A/shops.json`, `$A/store-requests.json`, `$A/search-requests.json`, `$A/product-requests.json`, `$A/catalog-search-like.json`, `$A/weighted-candidates.json`, `$A/nominatim-policy.txt` — exploratory первичные HTTP/browser captures. Это evidence, не production fixtures и не fallback.
- `$A/extra-stdio.json` и `$A/weighted-stdio.log` — exploratory **pre-sentinel-fix** captures; актуальный исправленный kg результат находится в source/wheel stdio. Не выдавать старый special=0 за бесплатный товар.

```bash
cd /home/everchaser/Projects/marketplace-mcp
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
uv run --locked python scripts/live_varus_smoke.py --nic enp9s0 \
  --settings examples/shopping.sample.json --output "$A/source-stdio.json"
uv run --locked python scripts/live_smoke.py --nic enp9s0 --output "$A/yandex-stdio.json"

uv venv --python 3.12 "$A/wheel-env"
uv pip install --python "$A/wheel-env/bin/python" \
  dist/marketplace_mcp-0.1.0-py3-none-any.whl
"$A/wheel-env/bin/python" -I -c 'import marketplace_mcp; print(marketplace_mcp.__file__)'
"$A/wheel-env/bin/python" scripts/live_varus_smoke.py --nic enp9s0 \
  --settings examples/shopping.sample.json --output "$A/wheel-stdio.json"
"$A/wheel-env/bin/python" scripts/live_smoke.py --nic enp9s0 --output "$A/wheel-yandex-stdio.json"
```

Baseline: **154 passed in 3.55s**. Финальная проверка: **218 passed**; Ruff check/format check проходят; uv build создал wheel и sdist. Clean env использует Python 3.12.14; импорт из `$A/wheel-env/lib/python3.12/site-packages/marketplace_mcp`, не editable src. Его разрешённые зависимости получены заново (MCP 1.30.0, HTTPX 0.28.1, Pydantic 2.14.0), и stdio реально упражнён, а не только import/launch.

## Свежие источники и решение по search

1. Прямой HTTPX `trust_env=false`, TLS, host route, `Accept-Encoding: identity`: card/search/stores ответили 200; приблизительно 1.69/0.97/0.94 MB соответственно, без Content-Encoding. Поэтому bounded raw identity-only transport достаточен; compression не внедрялась. HTTP headers показывали upstream cache Age и `X-Cache-Stale=true` — данные не гарантированно свежие.
2. Browser `https://varus.ua/stores` заново дал GET `https://varus.ua/api/catalog/vue_storefront_catalog_2/tms_shop/_search`. Полный запрос с `_appliedFilters=[{attribute:status,value:{eq:"1"},scope:default}]`, size=250, response_format=compact сохранён в store-requests. HTTP вернул 128 hits/total.eq; поля id, tms_id, address, lat/long, status, is_enabled, inactive_reason, is_darkstore, is_darkhub, is_shipping_hub.
3. `https://varus.ua/search?q=молоко` — SSR loading shell, **не** готовые search products. Browser использует внешний `api.multisearch.io` для relevance IDs, затем first-party `product_v2/_search` для гидратации. Не расширяем approved VARUS transport host allowlist на MultiSearch/static hosts.
4. Проверенный first-party `product_v2/_search` с name.like работает для lowercase отдельных токенов; `_searchText` оказался недостаточным, multiword like дал 0. Повторные name.like filters дали token-AND (пример пиріжок+здобний: 8 записей). Поэтому additive контракт явно документирует другой search ranking. Uppercase casefold и token splitting покрыты RED/GREEN.
5. url_key сам по себе не считается observed advertised URL: сначала candidate fetch, потом наблюдаемый canonical link + SSR id/SKU/url_key identity. Несовпадение/HTTP failure сохраняется как candidate_error и partial, без invented routes.
6. SSR разбирается `JSONDecoder.raw_decode` после __INITIAL_STATE__, без eval/exec. `storeView.storeId` — language storefront, не physical shop. У каталога встречаются повторяющиеся tms_id, а SSR shop.id может отличаться: например tms_id 1193, catalogue id 139275, availability id 139255. Используется уникальный tms join; ambiguity fail closed даже при id override.
7. Unit basis подтверждён в свежем SSR: масло `unit=pc`, banana `unit=kg` / `quantityText=за 1 кг`. Weight/title не используются для пересчёта. Raw zero special у banana — отсутствие акции, не price=0; обычный numeric zero не теряется.
8. Официальная [Nominatim policy](https://operations.osmfoundation.org/policies/nominatim/) заново прочитана в browser; privacy, deliberate developer decision, disable/switch, <=1req/s, identifiable UA, cache, attribution и no autocomplete отражены в настройках/документации.

## Наблюдаемые live результаты

Source и clean wheel согласованы на момент проверок (18:18 и 18:19 UTC):

- Ровно `search`, `get_card`, `get_cards`; старые имена/default marketplace сохранены.
- Sample nearest shop: **id 1 / tms_id 1001**, Панікахи 15, straight-line 0 km. VARUS остаётся host route даже когда stdio запущен с существующим `--nic enp9s0` для Яндекса.
- Search молоко: 2 observed canonical URLs, 2 verified catalogue/card identities.
- Масло SKU **2656669**: available, ordinary **124 UAH/unit**, promotional **74.9 UAH/unit**, exact_quantity=null.
- Дубликаты масла с quantities 2+3: ordinary **620.00 UAH**, promotional **374.50 UAH**, price coverage 2/2. Это два запроса одного SKU в списке, не подтверждение пяти штук.
- Mixed list: 3 inputs, 2 returned, 1 validation failure; status=partial. Ordinary **423.90 UAH**, included 2/3, complete=false; promotional **74.90 UAH**, included 1/3, complete=false.
- Unit vs kg mismatch: карточка успешна, но pricing coverage 0/1; totals 0.00 с complete=false, explicit excluded_reason=unit_mismatch.
- Banana SKU **127622**: available, ordinary **74.9 UAH/kg**, no promotional price; 0.5 kg = **37.45 UAH**, ordinary coverage 1/1, promotional coverage 0/1.
- Дополнительный weighted pastry: unavailable в shop 1, ordinary 339.9 UAH/kg; 0.5 kg исключены, total 0.00, included 0, complete=false — не подмена соседним магазином.
- Unsupported marketplace — error без provider query.
- Yandex source и clean wheel: search 4 items, single card, batch 2/2, mixed batch 2 successes/1 failure, unsupported — проходят без изменения Yandex transport/relay/provider.

## Ограничения и deployment

- Полный opt-in address → uniquely geocoded point → nearest-shop путь покрыт deterministic tests с synthetic response; **живой sample public address дал ambiguity**, поэтому successful public geocoding в этом прогоне не доказан. Coordinates → nearest-shop и shop-specific card/totals доказаны live. Не обходите ambiguity выбором первого города/результата; храните проверенные coordinates.
- Active/eligible — по API operational flags, не проверка «открыт прямо сейчас» по расписанию. Выбор не зависит от наличия конкретного товара.
- Exact quantity, reservation, fulfillment/delivery, fees, eligibility for conditional discounts неизвестны. Даже complete totals — approximate, не предложение продажи.
- Цены/наличие/SSR могут измениться; upstream cache may be stale. Reviews VARUS не извлекаются.
- Search casefold token-AND, не fuzzy/website ranking; каждый candidate требует дополнительной карточки. Большие limits медленнее и могут превысить client timeout; по умолчанию limit 10, для live использован 2. Captcha/encoding/body-limit/identity failures честно возвращаются, без bypass/fallback.
- Nominatim optional/disableable; адрес должен быть публичным и non-confidential. Только фиксированный allowlisted endpoint, не произвольный external geocoder. Для private/home — coordinates/shop_id. Persistent cache не автоистекает, cross-process locking — Linux/Unix; operator отвечает за суммарный policy traffic всех инстансов.
- Production settings не создавались: задайте `$XDG_CONFIG_HOME/shopping/settings.json` или `~/.config/shopping/settings.json`, либо явно `--settings`. Sample не является default store.
- Hermes config, существующий shopping/--nic, Obsidian не менялись. Editable installation увидит новый код после ручного `/reload-mcp`; отдельный restart gateway не выполнялся. Commit/push не делались.

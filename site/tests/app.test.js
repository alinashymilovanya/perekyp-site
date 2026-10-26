import test from "node:test";
import assert from "node:assert/strict";

import { calculateMargin, dashboardDataFromStatic, formatRubles, sortListings } from "../app.js";

test("calculates the same margin formula as the bot", () => {
  assert.equal(
    calculateMargin({ marketPrice: 50_000, price: 40_000, delivery: 400, commission: 0, reserve: 1_500 }),
    8_100,
  );
});

test("formats rubles with a readable thousands separator", () => {
  assert.equal(formatRubles(8100), "8 100 ₽");
  assert.equal(formatRubles(null), "—");
});

test("sorts listings by calculated margin with unknown values last", () => {
  const listings = [
    { item_id: "a", margin_rub: null },
    { item_id: "b", margin_rub: 500 },
    { item_id: "c", margin_rub: 2_000 },
  ];

  assert.deepEqual(sortListings(listings).map((listing) => listing.item_id), ["c", "b", "a"]);
});

test("marks the InfinityFree fallback as a static demo mode", () => {
  const dashboard = dashboardDataFromStatic({ status: { monitoring_enabled: false }, items: [] });

  assert.equal(dashboard.status.static_mode, true);
  assert.deepEqual(dashboard.listings, []);
});

// Price = base rate for bike type and zone, times a demand multiplier from demand-model.
const express = require("express");
const Decimal = require("decimal.js");
const { DemandModelClient } = require("demand-model-client");

// The deploy sets PLATFORM_HTTP_TIMEOUT from this service's override, else platform.yaml.
const timeoutMs = parseDuration(process.env.PLATFORM_HTTP_TIMEOUT);
const demand = new DemandModelClient({ baseUrl: "http://demand-model.internal", timeoutMs });

function parseDuration(value) {
  return value.endsWith("ms") ? Number(value.slice(0, -2)) : Number(value.slice(0, -1)) * 1000;
}

const app = express();
app.get("/price", async (req, res) => {
  const base = new Decimal(req.query.base);
  const multiplier = await demand.multiplier(req.query.zone).catch(() => 1);
  res.json({ price: base.times(multiplier).toFixed(2) });
});
app.listen(8080);

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const SOURCE_URL = "https://stockcharts.com/data/sp500-SCTR.json";
const OUTPUT_DIR = path.resolve(process.argv[2] || "artifacts/sctr-top-200");
const EXPECTED_COUNT = 200;

const response = await fetch(SOURCE_URL, {
  headers: {
    accept: "application/json",
    "user-agent": "dennysignal-sctr-test/1.0 (+https://github.com/dennyroland/dennysignal)",
  },
  signal: AbortSignal.timeout(30_000),
});

if (!response.ok) {
  throw new Error(`StockCharts request failed with HTTP ${response.status}`);
}

const source = await response.json();
if (!Array.isArray(source.sctrs)) {
  throw new Error("StockCharts response does not contain an sctrs array");
}

const ranked = source.sctrs
  .filter((row) => row && typeof row.symbol === "string" && Number.isFinite(Number(row.sctr)))
  .map((row) => ({
    symbol: row.symbol.trim().toUpperCase(),
    name: String(row.name || "").trim(),
    sector: String(row.sector || "").trim(),
    industry: String(row.industry || "").trim(),
    sctr: Number(row.sctr),
    previousSctr: row.lastSctr === "" || row.lastSctr == null ? null : Number(row.lastSctr),
    sctrChange:
      row.lastSctr === "" || row.lastSctr == null
        ? null
        : Number((Number(row.sctr) - Number(row.lastSctr)).toFixed(1)),
    last: Number(row.last),
    volume: Number(row.volume),
    marketCap: Number(row.marketcap),
  }))
  .sort((a, b) => b.sctr - a.sctr || a.symbol.localeCompare(b.symbol));

const unique = [];
const seen = new Set();
for (const row of ranked) {
  if (seen.has(row.symbol)) continue;
  seen.add(row.symbol);
  unique.push(row);
}

const top200 = unique.slice(0, EXPECTED_COUNT).map((row, index) => ({
  rank: index + 1,
  ...row,
}));

if (top200.length !== EXPECTED_COUNT) {
  throw new Error(`Expected ${EXPECTED_COUNT} unique symbols but received ${top200.length}`);
}

for (let index = 1; index < top200.length; index += 1) {
  if (top200[index].sctr > top200[index - 1].sctr) {
    throw new Error(`SCTR order validation failed at rank ${index + 1}`);
  }
}

const generatedAt = new Date().toISOString();
const result = {
  source: SOURCE_URL,
  universe: "Large Cap Stocks",
  timeframe: "End-of-Day",
  sourceTime: String(source.time || ""),
  generatedAt,
  count: top200.length,
  stocks: top200,
};

const csvFields = [
  "rank",
  "symbol",
  "name",
  "sector",
  "industry",
  "sctr",
  "previousSctr",
  "sctrChange",
  "last",
  "volume",
  "marketCap",
];

function csvValue(value) {
  if (value == null) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

const csv = [
  csvFields.join(","),
  ...top200.map((row) => csvFields.map((field) => csvValue(row[field])).join(",")),
].join("\n");

await mkdir(OUTPUT_DIR, { recursive: true });
await Promise.all([
  writeFile(path.join(OUTPUT_DIR, "sctr-top-200.json"), `${JSON.stringify(result, null, 2)}\n`),
  writeFile(path.join(OUTPUT_DIR, "sctr-top-200.csv"), `${csv}\n`),
]);

const preview = top200
  .slice(0, 10)
  .map((row) => `${String(row.rank).padStart(3)}  ${row.symbol.padEnd(6)}  ${row.sctr.toFixed(1)}`)
  .join("\n");

console.log(`Validated ${top200.length} unique Large-Cap EOD SCTR symbols.`);
console.log(`StockCharts report time: ${result.sourceTime || "not supplied"}`);
console.log("\nTop 10:\n" + preview);

if (process.env.GITHUB_STEP_SUMMARY) {
  const summary = [
    "# SCTR Top 200 test",
    "",
    `- Source time: ${result.sourceTime || "not supplied"}`,
    `- Generated at: ${generatedAt}`,
    `- Validated symbols: ${top200.length}`,
    "",
    "| Rank | Symbol | SCTR | Name |",
    "| ---: | :--- | ---: | :--- |",
    ...top200.slice(0, 20).map((row) => `| ${row.rank} | ${row.symbol} | ${row.sctr.toFixed(1)} | ${row.name} |`),
    "",
    "The complete JSON and CSV files are attached to this workflow run as an artifact.",
  ].join("\n");
  await writeFile(process.env.GITHUB_STEP_SUMMARY, `${summary}\n`, { flag: "a" });
}

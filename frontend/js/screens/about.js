import { enter } from "../ui.js";

export function renderAbout(view) {
  view.innerHTML = `
    <div class="page-head" data-enter>
      <div class="eyebrow">Reference</div>
      <h1>Data and assumptions</h1>
      <p class="lede">Everything OptiRoute shows is computed from a simulated network. This page explains how that data was made
      and which assumptions the planner relies on.</p>
    </div>
    <div class="panel panel-pad prose" data-enter>
      <h2 style="margin-top:0">The data is synthetic</h2>
      <p>No real company data is used. A seeded generator (seed 42) builds the same network every time:</p>
      <ul>
        <li><b>3 warehouses, 20 stores, 30 products</b> in 5 categories. Dairy and bakery (12 products) are perishable with 14 to 45 days of shelf life; the rest last 180 to 365 days.</li>
        <li><b>60 lanes</b>, one per warehouse and store, each with a fixed truck cost (₹3,200 to ₹10,000), a per-unit handling cost and a 1 to 3 day transit time.</li>
        <li><b>60 weeks of daily sales</b> (252,000 records) with weekend lifts, random promotions, weekly regional shocks and recorded stockout days.</li>
        <li><b>283 warehouse batches</b>, each with its own quantity and expiry date, and store shelf stock covering 1 to 4 days of demand.</li>
      </ul>

      <h2>How a plan is made</h2>
      <ol>
        <li><b>Clean the history.</b> Days where a store sold nothing because it was out of stock are replaced with a recent typical value, so the forecast is not dragged down.</li>
        <li><b>Forecast.</b> LightGBM models predict a likely (median) and a cautious (90th percentile) demand for each store and product over the planning days.</li>
        <li><b>Size the target.</b> Each product's target sits between those two numbers depending on its margin, holding cost and perishability.</li>
        <li><b>Optimise.</b> The HiGHS solver chooses warehouse, batch, quantity and number of trucks to minimise freight, lost sales and expired stock together.</li>
        <li><b>Oldest stock first.</b> A repair pass swaps in older batches wherever shelf life allows, then a checker confirms zero FEFO breaks.</li>
        <li><b>Stress-test.</b> The plan is replayed against 500 simulated weeks of demand.</li>
      </ol>

      <h2>Key assumptions</h2>
      <ul>
        <li>Each truck holds 500 units of volume. Products take 0.8 to 1.5 units of space each.</li>
        <li>A lost sale costs the product's margin plus a goodwill charge of half its unit cost.</li>
        <li>Expired stock costs its full unit cost.</li>
        <li>A batch is only sent if it arrives with at least a quarter of its total shelf life left (at least 3 days).</li>
        <li>Stores sell older stock before newer stock, at their typical daily rate.</li>
        <li>Stock a store already holds is reduced by expected sales during transit, using the fastest lane to that store.</li>
        <li>In the stress-test, demand follows a Negative Binomial distribution around the median forecast, with a shared shock per region (LogNormal, σ = 0.15).</li>
        <li>Each truck runs one direct trip from warehouse to store. Multi-stop routes are out of scope.</li>
      </ul>

      <h2>Known limits</h2>
      <ul>
        <li>Case quantities are first solved as fractions, then rounded to whole cases and topped up into spare truck space. This keeps solving under 10 seconds with the cost proven within 1% of the best plan before rounding.</li>
        <li>The proof page replays simulated weeks from today's stock. It is not a replay of 8 real historical weeks.</li>
        <li>The forecaster does not yet calibrate its intervals against held-out data, so the cautious estimate is a model output, not a guaranteed 90% bound.</li>
        <li>Some products are short across all warehouses. No plan can serve that demand, which caps the best possible fill rate at about 91%.</li>
      </ul>
    </div>`;
  enter(view);
}

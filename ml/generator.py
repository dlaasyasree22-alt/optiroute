import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def generate_optiroute_dataset(db_path='optiroute_data.sqlite', seed=42):
    np.random.seed(seed)
    
    # 1. Generate 30 SKUs across 5 Categories
    categories = ['Dairy', 'Bakery', 'Beverages', 'Personal_Care', 'Packaged_Foods']
    skus = []
    for i in range(1, 31):
        sku_id = f"SKU_{i:02d}"
        cat = categories[(i - 1) % len(categories)]
        is_perishable = 1 if cat in ['Dairy', 'Bakery'] else 0
        shelf_life = int(np.random.choice([14, 21, 30, 45])) if is_perishable else int(np.random.choice([180, 270, 365]))
        min_life = int(max(3, int(shelf_life * 0.25)))
        unit_cost = float(np.round(np.random.uniform(20.0, 150.0), 2))
        margin = float(np.round(unit_cost * np.random.uniform(0.20, 0.45), 2))
        holding_cost = float(np.round(unit_cost * 0.02, 2))
        case_pack = int(np.random.choice([10, 20]))
        volume = float(np.round(np.random.uniform(0.8, 1.5), 2))
        skus.append({
            'sku_id': sku_id,
            'category': cat,
            'unit_cost': unit_cost,
            'margin': margin,
            'holding_cost': holding_cost,
            'shelf_life_days': shelf_life,
            'min_life_at_delivery': min_life,
            'case_pack_size': case_pack,
            'volume_footprint': volume,
            'is_perishable': is_perishable
        })
    df_skus = pd.DataFrame(skus)

    # 2. 3 Warehouses and 20 Stores
    warehouses = ['WH1', 'WH2', 'WH3']
    stores = [f"STORE_{j:02d}" for j in range(1, 21)]

    # 3. 60 Transportation Lanes
    lanes = []
    for w in warehouses:
        for s in stores:
            dist_factor = np.random.uniform(0.8, 2.5)
            lanes.append({
                'warehouse_id': w,
                'store_id': s,
                'fixed_truck_cost': float(np.round(4000.0 * dist_factor, 2)),
                'variable_unit_cost': float(np.round(1.5 * dist_factor, 2)),
                'lead_time_days': int(np.random.choice([1, 2, 3])),
                'truck_capacity_units': 500
            })
    df_lanes = pd.DataFrame(lanes)

    # 4. 60 Weeks of Daily Sales with Promo Spikes and Censored Stockouts
    start_date = datetime(2025, 9, 1)
    total_days = 60 * 7
    date_range = [start_date + timedelta(days=d) for d in range(total_days)]
    weekly_shocks = {w: np.random.lognormal(0.0, 0.15) for w in range(60)}

    sales_records = []
    for s_idx, s in enumerate(stores):
        store_scale = np.random.uniform(0.8, 1.3)
        region_id = s_idx % 3
        for sku in skus:
            sku_id = sku['sku_id']
            base_demand = np.random.uniform(5.0, 25.0) * store_scale
            
            for day_idx, current_date in enumerate(date_range):
                weekday = current_date.weekday()
                day_factor = 1.35 if weekday in [4, 5, 6] else 0.90 # weekend lift
                week_num = day_idx // 7
                shock = weekly_shocks[week_num] if region_id == 0 else 1.0
                
                # Promo lift (~5% probability)
                is_promo = 1 if np.random.rand() < 0.05 else 0
                promo_factor = 1.8 if is_promo else 1.0
                
                lam = base_demand * day_factor * shock * promo_factor
                demanded = int(np.random.poisson(lam))
                
                # Censored demand simulation (stockouts record 0 sales)
                is_stockout = 1 if (demanded > 20 and np.random.rand() < 0.08) else 0
                sold = 0 if is_stockout else demanded
                
                sales_records.append({
                    'date': current_date.strftime('%Y-%m-%d'),
                    'store_id': s,
                    'sku_id': sku_id,
                    'units_demanded': demanded,
                    'units_sold': sold,
                    'is_stockout': is_stockout,
                    'is_promo': is_promo
                })
    df_sales = pd.DataFrame(sales_records)

    # 5. Inventory Batches at Warehouses (Planning Day T = 2026-10-26)
    planning_day = datetime(2026, 10, 26)
    batches = []
    batch_counter = 1
    for w in warehouses:
        for sku in skus:
            for _ in range(np.random.randint(2, 5)):
                batch_id = f"BAT_{w}_{batch_counter:04d}"
                batch_counter += 1
                max_life = sku['shelf_life_days']
                days_remaining = int(np.random.uniform(max_life * 0.2, max_life * 0.95))
                exp_date = planning_day + timedelta(days=days_remaining)
                mfg_date = exp_date - timedelta(days=max_life)
                qty = int(np.random.choice([100, 200, 300, 500]))
                
                batches.append({
                    'batch_id': batch_id,
                    'sku_id': sku['sku_id'],
                    'warehouse_id': w,
                    'quantity_units': qty,
                    'manufacture_date': mfg_date.strftime('%Y-%m-%d'),
                    'expiry_date': exp_date.strftime('%Y-%m-%d'),
                    'days_to_expiry': days_remaining
                })
    df_batches = pd.DataFrame(batches)

    # Save to SQLite database
    conn = sqlite3.connect(db_path)
    df_skus.to_sql('skus', conn, if_exists='replace', index=False)
    df_lanes.to_sql('lanes', conn, if_exists='replace', index=False)
    df_sales.to_sql('sales_history', conn, if_exists='replace', index=False)
    df_batches.to_sql('inventory_batches', conn, if_exists='replace', index=False)
    conn.close()
    ensure_store_inventory(db_path, seed=seed)

    print(f"Generated {db_path} with {len(df_sales):,} sales rows and {len(df_batches)} batches.")

def ensure_store_inventory(db_path='optiroute_data.sqlite', seed=42, force=False):
    """
    Seeds store on-hand stock at the planning day T as 1 to 4 days of each pair's
    recent average demand. Uses its own RNG so the other tables stay byte-identical.
    """
    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='store_inventory'"
        ).fetchone()
        if exists and not force:
            return
        recent = pd.read_sql_query(
            """SELECT store_id, sku_id, AVG(units_demanded) AS avg_daily
               FROM sales_history
               WHERE date >= (SELECT DATE(MAX(date), '-27 day') FROM sales_history)
               GROUP BY store_id, sku_id ORDER BY store_id, sku_id""",
            conn,
        )
        rng = np.random.default_rng(seed + 1)
        days_cover = rng.uniform(1.0, 4.0, size=len(recent))
        recent['on_hand_units'] = np.round(recent['avg_daily'] * days_cover).astype(int)
        recent[['store_id', 'sku_id', 'on_hand_units']].to_sql(
            'store_inventory', conn, if_exists='replace', index=False
        )
    finally:
        conn.close()


if __name__ == '__main__':
    generate_optiroute_dataset()
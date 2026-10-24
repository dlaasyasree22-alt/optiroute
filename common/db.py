import sqlite3
import pandas as pd

DB_PATH = "optiroute_data.sqlite"


def get_connection():
    return sqlite3.connect(DB_PATH)


def load_table(table_name: str) -> pd.DataFrame:
    allowed_tables = {
        "skus",
        "lanes",
        "sales_history",
        "inventory_batches",
    }

    if table_name not in allowed_tables:
        raise ValueError(f"Unknown table: {table_name}")

    with get_connection() as conn:
        return pd.read_sql_query(
            f"SELECT * FROM {table_name}", conn
        )
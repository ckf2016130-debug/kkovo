import akshare as ak


TASKS = [
    ("ths_ind_5", ak.stock_fund_flow_industry, {"symbol": "5日排行"}),
    ("ths_con_5", ak.stock_fund_flow_concept, {"symbol": "5日排行"}),
    ("em_ind_5", ak.stock_sector_fund_flow_rank, {"indicator": "5日", "sector_type": "行业资金流"}),
    ("em_con_5", ak.stock_sector_fund_flow_rank, {"indicator": "5日", "sector_type": "概念资金流"}),
]


def main():
    for label, func, kwargs in TASKS:
        print(f"\n--- {label} ---")
        try:
            df = func(**kwargs)
            print(df.shape)
            print(df.columns.tolist())
            print(df.head(5).to_string())
        except Exception as e:
            print(type(e).__name__, e)


if __name__ == "__main__":
    main()

import asyncio, os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from twscrape import API, gather

load_dotenv()

TERM_GROUPS = [
    "(#nifty50 OR #nifty OR $NIFTY)",
    "(#banknifty OR #bank_nifty OR $BANKNIFTY)",
    "(#sensex OR #bse OR $SENSEX)",
    "(#intraday OR #optionstrading OR #stockmarketindia)",
    "(#niftyoptions OR #expiry OR #optionchain)",
    "($RELIANCE OR $HDFCBANK OR $INFY OR $TCS OR $ICICIBANK)",
    "(nifty OR banknifty) (target OR SL OR breakout OR support)",
]

async def main():
    api = API()
    await api.pool.add_account_cookies(
        "burner1",
        f"auth_token={os.environ['X_AUTH_TOKEN']}; ct0={os.environ['X_CT0']}"
    )
    now = datetime.now(timezone.utc)
    # 14:00-15:00 IST today = peak market hours
    end = int((now - timedelta(hours=8)).timestamp())
    start = int((now - timedelta(hours=9)).timestamp())

    total = 0
    for terms in TERM_GROUPS:
        q = f"{terms} lang:en -filter:retweets since_time:{start} until_time:{end}"
        n = len(await gather(api.search(q, limit=200)))
        total += n
        print(f"{n:4d}  {terms}")
    print(f"\nTOTAL for 1h slice (en only): {total}")
    print(f"Projected 24h x 2 langs: ~{total * 24 * 1.3:.0f}")

asyncio.run(main())
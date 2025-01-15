import requests
import pandas as pd
from sqlalchemy import create_engine, MetaData, Table, Column, String, Float, DateTime
import time
import datetime
import logging
import json
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext

# Load Configuration
with open("config.json", "r") as file:
    config = json.load(file)

# Database Configuration
DATABASE_URI = "sqlite:///coins_analysis.db"
engine = create_engine(DATABASE_URI)
metadata = MetaData()

# Table Definition
coins_table = Table(
    "coins",
    metadata,
    Column("symbol", String),
    Column("price", Float),
    Column("volume_24h", Float),
    Column("liquidity", Float),
    Column("price_change_24h", Float),
    Column("dev_wallet", String),
    Column("contract_address", String),
    Column("last_updated", DateTime),
)

# Create the table if it doesn't exist
metadata.create_all(engine)

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = config["telegram_bot_token"]
TELEGRAM_CHAT_ID = int(config["telegram_chat_id"])  # Ensure ID is an integer
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Dexscreener API Configuration
DEXSCREENER_API_URL = "https://api.dexscreener.com/latest/dex/tokens"
RUGCHECK_API_URL = config["rugcheck_api_url"]
RUGCHECK_API_KEY = config["rugcheck_api_key"]

# Trading Configuration
MIN_BUY_AMOUNT = 0.025  # Minimum buy amount in SOL
MAX_BUY_AMOUNT = 0.1  # Maximum buy amount in SOL
FETCH_INTERVAL = config.get("fetch_interval", 300)

# Logging Configuration
logging.basicConfig(
    filename="trading_bot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
)

# Blacklists and Filters
FILTERS = config["filters"]
COIN_BLACKLIST = set(config["coin_blacklist"])
DEV_BLACKLIST = set(config["dev_blacklist"])

# Helper Functions
def send_telegram_message(message):
    """
    Send a notification message to a Telegram chat.
    """
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.warning(f"Failed to send Telegram message: {e}")

def fetch_coin_data():
    """
    Fetch the latest coin data from Dexscreener API.
    """
    try:
        response = requests.get(DEXSCREENER_API_URL, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"Error fetching coin data: {e}")
        return None

def check_rugcheck(contract_address):
    """
    Check token safety on RugCheck.xyz using their API.
    """
    try:
        headers = {"Authorization": f"Bearer {RUGCHECK_API_KEY}"}
        response = requests.get(f"{RUGCHECK_API_URL}/{contract_address}", headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("status") == "Good"
    except Exception as e:
        logging.warning(f"Error with RugCheck API: {e}")
        return False

def check_bundled_supply(market):
    """
    Check if the coin supply is bundled and flag it.
    """
    try:
        total_supply = float(market.get("baseToken", {}).get("supply", 0))
        liquidity = float(market.get("liquidity", {}).get("usd", 0))
        return total_supply > 0 and (liquidity / total_supply) > 10
    except Exception as e:
        logging.error(f"Error checking bundled supply: {e}")
        return True  # Assume suspicious if check fails

def parse_coin_data(data):
    """
    Parse Dexscreener coin data and apply filters.
    """
    for market in data.get("pairs", []):
        try:
            symbol = market.get("baseToken", {}).get("symbol", "")
            price = float(market.get("priceUsd", 0))
            volume_24h = float(market.get("volume", {}).get("h24", 0))
            contract_address = market.get("baseToken", {}).get("address", "")

            if symbol in COIN_BLACKLIST:
                continue
            if volume_24h < FILTERS["min_volume"]:
                continue
            if not check_rugcheck(contract_address):
                logging.info(f"RugCheck failed for {symbol}")
                continue
            if check_bundled_supply(market):
                logging.info(f"Bundled supply detected for {symbol}")
                continue

            # Simulate buying
            buy_amount = max(MIN_BUY_AMOUNT, min(MAX_BUY_AMOUNT, price))
            send_telegram_message(f"Buying {symbol} for {buy_amount} SOL.")
        except Exception as e:
            logging.error(f"Error parsing coin data: {e}")

def save_to_database(data):
    """
    Save parsed coin data to the database.
    """
    if not data:
        return
    try:
        df = pd.DataFrame(data)
        df.to_sql("coins", engine, if_exists="append", index=False)
        logging.info(f"Saved {len(df)} records to the database.")
    except Exception as e:
        logging.error(f"Error saving to database: {e}")

def start(update: Update, context: CallbackContext):
    """
    Start the bot.
    """
    if update.effective_chat.id == TELEGRAM_CHAT_ID:
        update.message.reply_text("Bot is running!")

def manual_trade(update: Update, context: CallbackContext):
    """
    Trigger manual trading.
    """
    if update.effective_chat.id == TELEGRAM_CHAT_ID:
        data = fetch_coin_data()
        if data:
            parse_coin_data(data)
            update.message.reply_text("Manual trade triggered.")
        else:
            update.message.reply_text("Failed to fetch data.")

# Main Function
def main():
    updater = Updater(token=TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("trade", manual_trade))

    updater.start_polling()
    logging.info("Bot is running...")
    updater.idle()

if __name__ == "__main__":
    main()
